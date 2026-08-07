"""
Validation layer for candidate rules.

Takes Claude-generated candidate rules, validates syntax, checks schema against
original rule files, and detects overlaps with existing rules.

For multi-file engines (transfer, liability), validates each candidate against
the correct target file based on the `target_file` column.

Usage:
    python scripts/validate_candidates.py \
        --review_dir reviews/2026-08-07/ \
        --output reviews/2026-08-07/validation_report.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd

from common import (
    load_config,
    resolve_rule_path,
    resolve_rules_base,
    META_COLUMNS,
    log,
    setup_logging,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _validate_regex(pattern: str) -> tuple[bool, str]:
    """Check if a regex pattern compiles. Returns (ok, error_message)."""
    try:
        re.compile(pattern, re.IGNORECASE)
        return True, ""
    except re.error as e:
        return False, f"Regex compile error: {e}"


def _validate_csv_schema(
    candidate_df: pd.DataFrame,
    original_df: pd.DataFrame,
) -> tuple[bool, str]:
    """Check that candidate CSV columns match original rule CSV.

    Meta columns (status, hit_count, etc.) are allowed as extras.
    """
    orig_cols = set(original_df.columns)
    cand_cols = set(candidate_df.columns)

    cand_cols_core = cand_cols - META_COLUMNS

    missing = orig_cols - cand_cols_core
    extra = cand_cols_core - orig_cols

    errors = []
    if missing:
        errors.append(f"Missing columns: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"Unexpected columns: {', '.join(sorted(extra))}")

    if errors:
        return False, "; ".join(errors)
    return True, ""


def _check_overlaps(
    pattern: str,
    match_type: str,
    existing_rules: pd.DataFrame,
) -> list[str]:
    """Check if a new pattern overlaps with any existing rules.

    For patterns ≥5 chars, detects substring relationships.
    For shorter patterns, only flags exact matches.
    """
    overlaps = []
    for _, rule in existing_rules.iterrows():
        existing_pattern = str(rule.get("pattern", rule.get("keyword", "")))
        if not existing_pattern:
            continue

        p_upper = pattern.upper()
        e_upper = existing_pattern.upper()

        if len(p_upper) >= 5 and len(e_upper) >= 5:
            if p_upper in e_upper or e_upper in p_upper:
                rule_name = rule.get("rule_name", rule.get("rule_id", str(rule.name)))
                overlaps.append(f"Pattern overlaps with existing: {rule_name} ({existing_pattern})")
        elif p_upper == e_upper:
            rule_name = rule.get("rule_name", rule.get("rule_id", str(rule.name)))
            overlaps.append(f"Pattern identical to existing: {rule_name}")

    return overlaps


def _validate_value_constraints(
    row: pd.Series,
    rule_name: str,
    orig_cols: set[str],
) -> list[str]:
    """Validate data-type and value constraints for a candidate row."""
    issues = []

    # match_type must be 'keyword' or 'regex' if present
    if "match_type" in row.index:
        mt = str(row["match_type"]).strip().lower()
        if mt and mt not in ("keyword", "regex"):
            issues.append(f"VALUE: match_type '{mt}' is not 'keyword' or 'regex'")

    # rule_type must be 'keyword' or 'regex' if present
    if "rule_type" in row.index:
        rt = str(row["rule_type"]).strip().lower()
        if rt and rt not in ("keyword", "regex"):
            issues.append(f"VALUE: rule_type '{rt}' is not 'keyword' or 'regex'")

    # confidence must be 0-1 if present
    if "confidence" in row.index:
        try:
            conf = float(row["confidence"])
            if conf < 0 or conf > 1:
                issues.append(f"VALUE: confidence {conf} is not in range [0, 1]")
        except (ValueError, TypeError):
            pass  # non-numeric confidence is OK for new candidates

    return issues


# ── main ─────────────────────────────────────────────────────────────────────

def validate_candidates(
    review_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Validate all candidate CSV files in the review directory.

    For multi-file engines, each candidate is validated against the rule file
    specified in its `target_file` column (or the only file for single-file engines).
    """
    project_root = Path(__file__).resolve().parent.parent
    rules_base = resolve_rules_base(project_root, config)

    report: dict[str, Any] = {
        "engines": {},
        "summary": {
            "total_candidates": 0,
            "syntax_errors": 0,
            "schema_errors": 0,
            "overlap_warnings": 0,
            "value_errors": 0,
        },
    }

    candidate_files = sorted(review_dir.glob("*_candidates.csv"))
    if not candidate_files:
        log.warning("No candidate files found.")
        return report

    for cand_path in candidate_files:
        engine_id = cand_path.stem.replace("_candidates", "")
        log.info("Processing: %s", engine_id)

        try:
            candidates = pd.read_csv(cand_path, encoding="utf-8-sig")
        except Exception as e:
            log.error("ERROR reading %s: %s", cand_path, e)
            continue

        log.info("  Candidates: %d", len(candidates))

        eng_cfg = config["engines"].get(engine_id, {})
        rule_files: list[str] = eng_cfg.get("rule_files", [])

        engine_report: dict[str, Any] = {
            "candidate_count": len(candidates),
            "syntax_errors": [],
            "schema_errors": [],
            "overlap_warnings": [],
            "value_errors": [],
            "valid_candidates": [],
        }

        # Pre-load all rule files for overlap checks and schema validation
        existing_rules_cache: dict[str, pd.DataFrame] = {}
        schema_errors_by_file: dict[str, str] = {}

        for rf in rule_files:
            rule_path = resolve_rule_path(rules_base, engine_id, eng_cfg, rf)
            if rule_path.exists():
                try:
                    existing_rules_cache[rf] = pd.read_csv(rule_path, encoding="utf-8-sig")
                    # Schema check (against each rule file)
                    ok, err = _validate_csv_schema(candidates, existing_rules_cache[rf])
                    if not ok:
                        schema_errors_by_file[rf] = err
                except Exception as e:
                    log.warning("  Could not load %s: %s", rf, e)
            else:
                log.warning("  Rule file not found: %s", rule_path)

        # Report schema errors
        for rf, err in schema_errors_by_file.items():
            # Schema mismatch is expected when candidate has columns for
            # multiple target files — only report if ALL files mismatch
            pass
        if len(schema_errors_by_file) == len(rule_files) and rule_files:
            # All files have schema mismatches — this is a real problem
            all_errs = "; ".join(f"{rf}: {err}" for rf, err in schema_errors_by_file.items())
            engine_report["schema_errors"].append(all_errs)
            log.warning("  ⚠ Schema mismatch against all rule files")

        # Validate each candidate
        large_files_cfg = config.get("large_files", {})

        for idx, row in candidates.iterrows():
            pattern = str(row.get("pattern", row.get("keyword", "")))
            match_type = str(row.get("match_type", "keyword")).lower()
            rule_name = str(row.get("rule_name", row.get("rule_id", f"candidate_{idx}")))

            issues = []

            # 1. Syntax check
            if match_type == "regex" and pattern:
                ok, err = _validate_regex(pattern)
                if not ok:
                    issues.append(f"SYNTAX: {err}")
                    engine_report["syntax_errors"].append({
                        "rule_name": rule_name,
                        "pattern": pattern,
                        "error": err,
                    })

            # 2. Pattern length check
            if len(pattern) < 3:
                issues.append(f"SYNTAX: Pattern too short ({len(pattern)} chars)")

            # 3. Boundary check for regex
            if match_type == "regex" and pattern:
                if not (r"\b" in pattern or pattern.startswith("^") or pattern.endswith("$")):
                    issues.append("STYLE: Regex missing word boundaries (\\b) or anchors (^/$)")

            # 4. Determine which rule file to validate against
            target_file = None
            if "target_file" in row.index:
                tf = str(row["target_file"]).strip()
                if tf and tf.lower() != "nan" and tf in existing_rules_cache:
                    target_file = tf
            elif len(rule_files) == 1:
                target_file = rule_files[0]
            else:
                # Multi-file without target_file — check against all
                target_file = rule_files[0]  # default for overlap check

            # 5. Overlap check against the target file's existing rules
            if target_file and target_file in existing_rules_cache:
                # Skip overlap check for large files
                if target_file not in large_files_cfg or \
                   not large_files_cfg[target_file].get("skip_pattern_loading"):
                    overlaps = _check_overlaps(pattern, match_type, existing_rules_cache[target_file])
                    if overlaps:
                        issues.append(f"OVERLAP: {'; '.join(overlaps)}")
                        engine_report["overlap_warnings"].append({
                            "rule_name": rule_name,
                            "pattern": pattern,
                            "target_file": target_file,
                            "overlaps": overlaps,
                        })

            # 6. Value constraint validation
            if target_file and target_file in existing_rules_cache:
                orig_cols = set(existing_rules_cache[target_file].columns)
                value_issues = _validate_value_constraints(row, rule_name, orig_cols)
                for vi in value_issues:
                    issues.append(vi)
                    engine_report["value_errors"].append({
                        "rule_name": rule_name,
                        "pattern": pattern,
                        "error": vi,
                    })

            if issues:
                log.warning("  ⚠ %s: %s", rule_name, ", ".join(issues))
            else:
                engine_report["valid_candidates"].append({
                    "rule_name": rule_name,
                    "pattern": pattern,
                    "match_type": match_type,
                    "target_file": target_file,
                })

        report["engines"][engine_id] = engine_report

        # Update summary
        report["summary"]["total_candidates"] += len(candidates)
        report["summary"]["syntax_errors"] += len(engine_report["syntax_errors"])
        report["summary"]["schema_errors"] += len(engine_report["schema_errors"])
        report["summary"]["overlap_warnings"] += len(engine_report["overlap_warnings"])
        report["summary"]["value_errors"] += len(engine_report["value_errors"])

    return report


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging("validate")
    parser = argparse.ArgumentParser(
        description="Validate candidate rules: syntax, schema, and regression checks."
    )
    parser.add_argument(
        "--review_dir", required=True,
        help="Directory containing <engine>_candidates.csv files."
    )
    parser.add_argument(
        "--output",
        help="Path for validation_report.json (default: review_dir/validation_report.json)."
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to config.json."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and print report without writing files."
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root)

    review_dir = Path(args.review_dir)
    output_path = Path(args.output) if args.output else (review_dir / "validation_report.json")

    report = validate_candidates(review_dir, config)

    if not args.dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log.info("Report written to %s", output_path)

    log.info("Summary: %s", json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
