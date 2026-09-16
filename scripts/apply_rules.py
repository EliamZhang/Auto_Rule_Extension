"""
Rule application layer.

Reads human-confirmed candidate rules and appends them to the local raw/
directory CSV files. Optionally syncs to a target engine directory.

For engines with multiple rule files (transfer, liability), candidates
must include a `target_file` column to specify which file each rule
belongs to. If omitted for single-file engines, the only rule file is used.

Usage:
    # Apply to local raw/ directory
    python scripts/apply_rules.py --review_dir reviews/2026-08-07/

    # Apply to local raw/ and sync to finv_category_V2
    python scripts/apply_rules.py --review_dir reviews/2026-08-07/ --sync_to D:/project/finv_category_V2

    # Preview without making changes
    python scripts/apply_rules.py --review_dir reviews/2026-08-07/ --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from common import (
    candidate_pattern,
    load_config,
    resolve_finv_path,
    resolve_rule_path,
    resolve_rules_base,
    META_COLUMNS,
    log,
    setup_logging,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _detect_target_file(
    row: pd.Series,
    rule_files: list[str],
    engine_id: str,
) -> str:
    """Determine which rule file a candidate row should be written to.

    Priority:
    1. If `target_file` column is present and non-empty, use it directly
       (validated against rule_files list).
    2. If only one rule file, use it automatically.
    3. If multiple rule files and no target_file, warn and fall back to first.
    """
    if "target_file" in row.index:
        tf = str(row["target_file"]).strip()
        if tf and tf.lower() != "nan":
            if tf in rule_files:
                return tf
            else:
                raise ValueError(
                    f"target_file '{tf}' not found in engine '{engine_id}' rule_files: {rule_files}"
                )

    if len(rule_files) == 1:
        return rule_files[0]

    log.warning(
        "Engine '%s' has %d rule files but no 'target_file' column. "
        "Defaulting to '%s'. Available: %s",
        engine_id, len(rule_files), rule_files[0], ", ".join(rule_files),
    )
    return rule_files[0]


# ── main ─────────────────────────────────────────────────────────────────────

def apply_rules(
    review_dir: Path,
    config: dict[str, Any],
    sync_to: Path | None = None,
) -> dict[str, Any]:
    """Apply confirmed rules to local raw/ CSV files.

    For engines with multiple rule files, candidates are routed via the
    `target_file` column. Each group is appended to its corresponding file.
    """
    project_root = Path(__file__).resolve().parent.parent
    rules_base = resolve_rules_base(project_root, config)

    applied: dict[str, Any] = {
        "engines": {},
        "total_applied": 0,
        "errors": [],
    }

    candidate_files = sorted(review_dir.glob("*_candidates.csv"))
    if not candidate_files:
        log.warning("No candidate files found.")
        return applied

    for cand_path in candidate_files:
        engine_id = cand_path.stem.replace("_candidates", "")
        log.info("Processing: %s", engine_id)

        eng_cfg = config["engines"].get(engine_id, {})
        if not eng_cfg:
            msg = f"Unknown engine '{engine_id}' — not in config.json"
            log.warning("SKIP: %s", msg)
            applied["errors"].append({"engine": engine_id, "error": msg})
            continue

        rule_files: list[str] = eng_cfg.get("rule_files", [])
        if not rule_files:
            msg = f"No rule_files configured for engine '{engine_id}'"
            log.error("ERROR: %s", msg)
            applied["errors"].append({"engine": engine_id, "error": msg})
            continue

        # Read candidates
        try:
            candidates = pd.read_csv(cand_path, encoding="utf-8-sig")
        except Exception as e:
            msg = f"Failed to read {cand_path}: {e}"
            log.error("ERROR: %s", msg)
            applied["errors"].append({"engine": engine_id, "error": msg})
            continue

        # Filter confirmed only
        if "status" not in candidates.columns:
            log.warning("No 'status' column found, skipping all candidates")
            continue

        confirmed = candidates[candidates["status"].str.strip().str.lower() == "confirmed"]
        if confirmed.empty:
            log.info("No confirmed rules, skipping.")
            continue

        log.info("Confirmed: %d rules across %d target file(s)", len(confirmed), len(rule_files))

        # Pre-load all rule file schemas
        rule_schemas: dict[str, dict[str, Any]] = {}
        for rf in rule_files:
            rule_path = resolve_rule_path(rules_base, engine_id, eng_cfg, rf)
            if rule_path.exists():
                try:
                    orig_df = pd.read_csv(rule_path, encoding="utf-8-sig")
                    rule_schemas[rf] = {
                        "path": rule_path,
                        "columns": list(orig_df.columns),
                        "df": orig_df,
                    }
                except Exception as e:
                    log.warning("Could not read %s: %s", rule_path, e)
            else:
                log.warning("Rule file not found: %s", rule_path)

        # Group candidates by target file
        file_groups: dict[str, list[pd.Series]] = {rf: [] for rf in rule_files}
        routing_errors = 0

        for _, row in confirmed.iterrows():
            try:
                target = _detect_target_file(row, rule_files, engine_id)
            except ValueError as e:
                log.error("ERROR: %s", e)
                applied["errors"].append({"engine": engine_id, "error": str(e)})
                routing_errors += 1
                continue

            if target not in file_groups:
                file_groups[target] = []
            file_groups[target].append(row)

        if routing_errors > 0:
            log.warning("%d rule(s) skipped due to routing errors", routing_errors)

        # Write each group to its target file
        engine_applied = 0
        engine_files: dict[str, dict[str, Any]] = {}

        for target_file, rows in file_groups.items():
            if not rows:
                continue

            if target_file not in rule_schemas:
                msg = f"Target file '{target_file}' not found in raw/ for engine '{engine_id}'"
                log.error("ERROR: %s", msg)
                applied["errors"].append({"engine": engine_id, "error": msg})
                continue

            schema_info = rule_schemas[target_file]
            rule_path = schema_info["path"]
            orig_df = schema_info["df"]
            orig_cols = schema_info["columns"]

            log.info("  → %s: %d rule(s)", target_file, len(rows))

            rows_df = pd.DataFrame([r.to_dict() for r in rows])

            # Strip meta columns to get rule-only columns
            rule_cols = [c for c in rows_df.columns if c not in META_COLUMNS]
            matching_cols = [c for c in orig_cols if c in rule_cols]
            new_rules = rows_df[matching_cols].copy()

            # Fill missing columns with empty values
            for col in orig_cols:
                if col not in new_rules.columns:
                    new_rules[col] = ""

            new_rules = new_rules[orig_cols]

            # Backup
            backup_path = rule_path.with_suffix(rule_path.suffix + ".bak")
            shutil.copy2(rule_path, backup_path)
            log.info("    Backup: %s", backup_path)

            try:
                merged = pd.concat([orig_df, new_rules], ignore_index=True)
                merged.to_csv(rule_path, index=False, encoding="utf-8-sig")
                log.info("    Written: %d new rules → %s", len(new_rules), rule_path)

                if sync_to:
                    # finv_category_V2 的目录布局与本地 raw/ 不同：
                    # initial → <finv>/initial_engine/<file>，其余 → <finv>/<engine>_engine/resources/<file>
                    # 不再使用 resolve_rule_path（会写到 finv 不读取的 <engine>_rule/ 目录）
                    sync_path = resolve_finv_path(sync_to, engine_id, eng_cfg, target_file)
                    if not sync_path.parent.exists():
                        log.error(
                            "    SKIP SYNC: 目标目录不存在 %s（请检查 config.json 的 "
                            "finv_engine_dir/finv_rule_dir 与 finv_category_V2 实际布局）",
                            sync_path.parent,
                        )
                        applied["errors"].append({
                            "engine": engine_id,
                            "error": f"sync target dir missing: {sync_path.parent}",
                        })
                    else:
                        shutil.copy2(rule_path, sync_path)
                        log.info("    Synced → %s", sync_path)

                engine_applied += len(new_rules)
                engine_files[target_file] = {
                    "file": str(rule_path),
                    "rules_added": len(new_rules),
                    "backup": str(backup_path),
                }

            except Exception as e:
                msg = f"Failed to write {rule_path}: {e}"
                log.error("    ERROR: %s", msg)
                applied["errors"].append({"engine": engine_id, "error": msg})
                shutil.copy2(backup_path, rule_path)
                log.info("    Restored from backup.")

        if engine_applied > 0:
            applied["engines"][engine_id] = {
                "files": engine_files,
                "total_rules_added": engine_applied,
            }
            applied["total_applied"] += engine_applied

    return applied


# ── CLI ──────────────────────────────────────────────────────────────────────

def _dry_run(review_dir: Path, config: dict[str, Any]) -> None:
    """Preview mode: show what would be written without actually writing."""
    project_root = Path(__file__).resolve().parent.parent
    rules_base = resolve_rules_base(project_root, config)

    candidate_files = sorted(review_dir.glob("*_candidates.csv"))
    if not candidate_files:
        log.warning("No candidate files found.")
        return

    total = 0
    for cand_path in candidate_files:
        engine_id = cand_path.stem.replace("_candidates", "")
        eng_cfg = config["engines"].get(engine_id, {})
        rule_files = eng_cfg.get("rule_files", [])

        try:
            candidates = pd.read_csv(cand_path, encoding="utf-8-sig")
        except Exception as e:
            log.error("ERROR reading %s: %s", cand_path, e)
            continue

        if "status" not in candidates.columns:
            continue

        confirmed = candidates[candidates["status"].str.strip().str.lower() == "confirmed"]
        if confirmed.empty:
            continue

        print(f"\n[apply --dry-run] {engine_id}: {len(confirmed)} confirmed rule(s)")

        file_groups: dict[str, list] = {}
        for _, row in confirmed.iterrows():
            target = rule_files[0]
            if "target_file" in row.index:
                tf = str(row["target_file"]).strip()
                if tf and tf.lower() != "nan" and tf in rule_files:
                    target = tf
            file_groups.setdefault(target, []).append(row)

        for tf, rows in file_groups.items():
            rule_path = resolve_rule_path(rules_base, engine_id, eng_cfg, tf)
            print(f"  → {tf}: {len(rows)} rule(s) [would write to {rule_path}]")
            for r in rows:
                p = candidate_pattern(r)
                print(f"      - {p[:80]}")
            total += len(rows)

    print(f"\n[apply --dry-run] Total rules that would be applied: {total}")
    print("[apply --dry-run] No changes made.")


def main() -> None:
    setup_logging("apply")
    parser = argparse.ArgumentParser(
        description="Apply confirmed rules to local rule CSV files."
    )
    parser.add_argument(
        "--review_dir", required=True,
        help="Directory containing confirmed <engine>_candidates.csv files."
    )
    parser.add_argument(
        "--sync_to",
        help="Optional: sync updated rule files to another directory (e.g. finv_category_V2)."
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to config.json."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview which rules would be written to which files, without making changes."
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root)

    if args.dry_run:
        _dry_run(Path(args.review_dir), config)
        return

    result = apply_rules(
        review_dir=Path(args.review_dir),
        config=config,
        sync_to=Path(args.sync_to) if args.sync_to else None,
    )

    print(f"\n{'='*60}")
    print(f"[apply] Done. Total rules applied: {result['total_applied']}")
    if result["errors"]:
        print(f"[apply] Errors: {len(result['errors'])}")
        for err in result["errors"]:
            print(f"  - {err['engine']}: {err['error']}")


if __name__ == "__main__":
    main()
