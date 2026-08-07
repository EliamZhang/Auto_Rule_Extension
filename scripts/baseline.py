"""
Baseline system for measuring candidate rule impact.

Provides two subcommands:

    save  — Capture current classification state from an input .xlsx report.
    diff  — Simulate applying candidate rules and measure gain/conflict per rule.

Does NOT require finv_category_V2 — works purely with text matching against
the pre-classified report data.

Usage:
    python scripts/baseline.py save \\
        --input input/report2.xlsx \\
        --output baseline/2026-08-07/

    python scripts/baseline.py diff \\
        --candidates reviews/2026-08-07/ \\
        --baseline baseline/2026-08-07/ \\
        --input input/report2.xlsx
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from common import (
    load_config,
    get_engine_priority,
    META_COLUMNS,
    log,
    setup_logging,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _match_text(pattern: str, match_type: str, text: str) -> bool:
    """Check if a pattern matches transaction text (used for sample extraction)."""
    if not pattern or not text:
        return False
    if match_type == "regex":
        try:
            return bool(re.search(pattern, str(text), re.IGNORECASE))
        except re.error:
            return False
    else:
        return pattern.upper() in str(text).upper()


# ── save ─────────────────────────────────────────────────────────────────────

def baseline_save(input_path: Path, output_dir: Path) -> dict[str, Any]:
    """Capture current classification state from a pre-classified .xlsx.

    Stores the baseline as a gzip-compressed JSON file (.json.gz) to handle
    large transaction volumes without excessive disk usage.
    """
    df = pd.read_excel(input_path, sheet_name="transactions")
    total = len(df)
    log.info("Loaded %s transactions", f"{total:,}")

    if "classification_status" not in df.columns:
        raise ValueError("Input must be a pre-classified .xlsx with 'classification_status' column")

    uncl_mask = df["classification_status"].str.strip().str.lower() == "unclassified"
    cl_mask = ~uncl_mask

    unclassified = df[uncl_mask]
    classified = df[cl_mask]

    # Per-engine stats
    per_engine = (
        classified.groupby("classification_engine").size()
        .sort_values(ascending=False).to_dict()
    )
    per_engine = {str(k): int(v) for k, v in per_engine.items()}

    # Per-category stats
    if "finv_category" in classified.columns:
        per_category = (
            classified.groupby("finv_category").size()
            .sort_values(ascending=False).to_dict()
        )
        per_category = {str(k): int(v) for k, v in per_category.items()}
    else:
        per_category = {}

    # Store unclassified texts for diff matching
    unclassified_texts: list[dict[str, Any]] = []
    for _, row in unclassified.iterrows():
        unclassified_texts.append({
            "text": str(row.get("text", "")),
            "category": str(row.get("category", "")) if pd.notna(row.get("category")) else "",
        })

    # Store classified texts with engine/category for conflict detection
    classified_texts: list[dict[str, Any]] = []
    for _, row in classified.iterrows():
        classified_texts.append({
            "text": str(row.get("text", "")),
            "engine": str(row.get("classification_engine", "")),
            "finv_category": str(row.get("finv_category", "")),
            "category": str(row.get("category", "")) if pd.notna(row.get("category")) else "",
        })

    baseline = {
        "input_file": str(input_path),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total": total,
            "classified": int(cl_mask.sum()),
            "unclassified": int(uncl_mask.sum()),
            "per_engine": per_engine,
            "per_category": per_category,
        },
        "unclassified": unclassified_texts,
        "classified": classified_texts,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "baseline.json.gz"
    with gzip.open(output_path, "wt", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False)

    log.info("→ %s", output_path)
    log.info("  Total:       %s", f"{total:,}")
    log.info("  Classified:  %s (%d%%)", f"{cl_mask.sum():,}", cl_mask.sum() / total * 100)
    log.info("  Unclassified: %s (%d%%)", f"{uncl_mask.sum():,}", uncl_mask.sum() / total * 100)
    log.info("  Engines:     %d", len(per_engine))
    for eng, cnt in sorted(per_engine.items(), key=lambda x: -x[1]):
        log.info("    %s: %s", eng, f"{cnt:,}")

    return baseline


# ── diff ─────────────────────────────────────────────────────────────────────

def _match_series(
    pattern: str,
    match_type: str,
    series: pd.Series,
) -> pd.Series:
    """Vectorized pattern matching against a pandas Series.

    Uses pandas str.contains for both keyword and regex matching,
    which is orders of magnitude faster than Python-level iteration.
    """
    if not pattern or series.empty:
        return pd.Series([False] * len(series), index=series.index)

    if match_type == "regex":
        return series.str.contains(pattern, case=False, regex=True, na=False)
    else:
        return series.str.contains(pattern, case=False, regex=False, na=False)


def baseline_diff(
    input_path: Path,
    baseline_dir: Path,
    candidates_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Simulate candidate rules against baseline to measure impact.

    Uses pandas vectorized string matching for scalability with large datasets.
    Reads gzip-compressed baseline if present, falls back to plain JSON.
    """
    # Try gzipped first, then plain JSON for backward compatibility
    gz_path = baseline_dir / "baseline.json.gz"
    plain_path = baseline_dir / "baseline.json"

    if gz_path.exists():
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            baseline = json.load(f)
        log.info("Loaded compressed baseline from %s", gz_path)
    elif plain_path.exists():
        with open(plain_path, encoding="utf-8") as f:
            baseline = json.load(f)
        log.info("Loaded baseline from %s", plain_path)
    else:
        raise FileNotFoundError(
            f"Baseline not found at {gz_path} or {plain_path}. Run 'baseline.py save' first."
        )

    unclassified_list = baseline["unclassified"]
    classified_list = baseline["classified"]

    log.info("Baseline: %s total, %s unclassified, %s classified",
             f"{baseline['stats']['total']:,}",
             f"{len(unclassified_list):,}",
             f"{len(classified_list):,}")

    # Convert to pandas Series/DataFrame for vectorized matching
    uncl_texts = pd.Series(
        [item["text"] for item in unclassified_list],
        dtype="string",
    )
    cl_texts = pd.Series(
        [item["text"] for item in classified_list],
        dtype="string",
    )
    cl_df = pd.DataFrame(classified_list)
    if "engine" not in cl_df.columns:
        cl_df["engine"] = "unknown"
    if "finv_category" not in cl_df.columns:
        cl_df["finv_category"] = ""

    # Find candidate CSV files
    candidate_files = sorted(candidates_dir.glob("*_candidates.csv"))
    if not candidate_files:
        log.warning("No candidate files found.")
        return {"engines": {}, "summary": {"total_gain": 0, "total_conflicts": 0}}

    impact: dict[str, Any] = {
        "engines": {},
        "summary": {"total_gain": 0, "total_conflicts": 0},
    }

    for cand_path in candidate_files:
        engine_id = cand_path.stem.replace("_candidates", "")
        eng_priority = get_engine_priority(engine_id, config)

        try:
            candidates = pd.read_csv(cand_path, encoding="utf-8-sig")
        except Exception as e:
            log.error("ERROR reading %s: %s", cand_path, e)
            continue

        log.info("Engine: %s (%d candidates, priority=%d)", engine_id, len(candidates), eng_priority)

        engine_impact: dict[str, Any] = {
            "rules": [],
            "summary": {"total_gain": 0, "total_conflicts": 0},
        }

        for _, row in candidates.iterrows():
            pattern = str(row.get("pattern", row.get("keyword", "")))
            match_type = str(row.get("match_type", "keyword")).lower()
            rule_name = str(row.get("rule_name", row.get("rule_id", f"candidate_{_}")))

            if not pattern or pattern == "nan":
                continue

            # ── Match against unclassified (gain) using vectorized ops ──
            uncl_mask = _match_series(pattern, match_type, uncl_texts)
            gain_count = int(uncl_mask.sum())
            gain_indices = uncl_mask[uncl_mask].index[:5]
            gain_samples = [unclassified_list[i]["text"] for i in gain_indices]

            # ── Match against classified (potential conflicts) using vectorized ops ──
            cl_mask = _match_series(pattern, match_type, cl_texts)
            matched_cl = cl_df[cl_mask]

            conflict_categories: dict[tuple[str, str, bool], int] = {}
            conflict_samples: list[str] = []

            for _, item in matched_cl.iterrows():
                other_engine = str(item["engine"])
                other_priority = get_engine_priority(other_engine, config)
                is_conflict = eng_priority > other_priority

                key = (other_engine, str(item["finv_category"]), is_conflict)
                conflict_categories[key] = conflict_categories.get(key, 0) + 1

                if len(conflict_samples) < 3:
                    conflict_samples.append(str(item["text"]))

            # Build conflict summary
            conflicts = []
            for (other_eng, other_cat, is_conflict), count in sorted(
                conflict_categories.items(), key=lambda x: -x[1]
            ):
                conflicts.append({
                    "engine": other_eng,
                    "category": other_cat,
                    "count": count,
                    "priority_conflict": is_conflict,
                })

            total_conflicts = sum(c["count"] for c in conflicts)
            real_conflicts = sum(c["count"] for c in conflicts if c["priority_conflict"])

            rule_result = {
                "rule_name": rule_name,
                "pattern": pattern,
                "match_type": match_type,
                "gain": gain_count,
                "conflicts": conflicts,
                "real_conflict_count": real_conflicts,
                "gain_samples": gain_samples[:3],
                "conflict_samples": conflict_samples[:3],
            }
            engine_impact["rules"].append(rule_result)
            engine_impact["summary"]["total_gain"] += gain_count
            engine_impact["summary"]["total_conflicts"] += real_conflicts

            if gain_count > 0 or real_conflicts > 0:
                flag = " ⚠" if real_conflicts > 0 else ""
                log.info("  %s: gain=%d, conflicts=%d%s", rule_name, gain_count, real_conflicts, flag)
                if real_conflicts > 0:
                    for c in conflicts:
                        if c["priority_conflict"]:
                            log.info("    ↳ would overwrite: %s/%s (%dx)", c["engine"], c["category"], c["count"])

        impact["engines"][engine_id] = engine_impact
        impact["summary"]["total_gain"] += engine_impact["summary"]["total_gain"]
        impact["summary"]["total_conflicts"] += engine_impact["summary"]["total_conflicts"]

    # Write impact report
    report_path = candidates_dir / "impact_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(impact, f, ensure_ascii=False, indent=2)

    log.info("→ %s", report_path)
    log.info("  Total gain:      %s", f"{impact['summary']['total_gain']:,}")
    log.info("  Total conflicts: %s", f"{impact['summary']['total_conflicts']:,}")
    if impact['summary']['total_conflicts'] > 0:
        log.warning("  ⚠ Review conflicts before approving!")

    return impact


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging("baseline")
    parser = argparse.ArgumentParser(
        description="Baseline system: measure candidate rule impact."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_save = sub.add_parser("save", help="Capture current classification state")
    p_save.add_argument("--input", required=True, help="Pre-classified .xlsx report")
    p_save.add_argument("--output", required=True, help="Directory for baseline.json.gz")

    p_diff = sub.add_parser("diff", help="Simulate candidate rule impact")
    p_diff.add_argument("--candidates", required=True, help="Directory with <engine>_candidates.csv")
    p_diff.add_argument("--baseline", required=True, help="Directory with baseline.json.gz")
    p_diff.add_argument("--input", required=True, help="Original .xlsx report")
    p_diff.add_argument("--config", default=None, help="Path to config.json")

    args = parser.parse_args()
    project_root = Path(__file__).resolve().parent.parent

    if args.command == "save":
        baseline_save(
            input_path=Path(args.input),
            output_dir=Path(args.output),
        )
    elif args.command == "diff":
        config = load_config(project_root)
        baseline_diff(
            input_path=Path(args.input),
            baseline_dir=Path(args.baseline),
            candidates_dir=Path(args.candidates),
            config=config,
        )


if __name__ == "__main__":
    main()
