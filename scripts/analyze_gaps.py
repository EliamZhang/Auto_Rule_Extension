"""
Statistical gap discovery layer.

Reads an already-classified transaction report (.xlsx or .csv), identifies
high-frequency unclassified patterns, and leverages illion enrichment labels
as category hints. Outputs per-engine gap summaries for the Claude Skill.

Input: .xlsx (pre-classified) — Reads 'transactions' sheet.
  Must contain: text, classification_status, category (illion label), third_party,
  and optionally finv_category for disagreement analysis.

Usage:
    python scripts/analyze_gaps.py \
        --input input/report2.xlsx \
        --output reviews/2026-08-07/
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from common import (
    load_config,
    load_category_catalog,
    normalize_pattern_text,
    read_transactions,
    resolve_rules_base,
    log,
    setup_logging,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_existing_patterns(
    rules_base: Path, engine_id: str, engine_config: dict[str, Any], config: dict[str, Any]
) -> set[str]:
    """Load all existing patterns for an engine from the local raw/ directory.

    Skips files marked as large_files in config to avoid memory issues.
    """
    patterns: set[str] = set()
    engine_dir = engine_config.get("engine_dir", f"{engine_id}_engine")
    rule_dir = rules_base / engine_dir

    large_files_cfg = config.get("large_files", {})

    for rule_file_name in engine_config.get("rule_files", []):
        # Skip files marked as too large for pattern loading
        if rule_file_name in large_files_cfg:
            if large_files_cfg[rule_file_name].get("skip_pattern_loading"):
                log.info("  Skipping pattern load for large file: %s", rule_file_name)
                continue

        rule_path = rule_dir / rule_file_name
        if not rule_path.exists():
            continue
        try:
            df = pd.read_csv(rule_path, encoding="utf-8-sig")
        except Exception:
            continue

        for col in ("pattern", "keyword", "keywords"):
            if col in df.columns:
                for val in df[col].dropna().astype(str):
                    patterns.add(val.strip().upper())
                    patterns.add(val.strip())

    return patterns


def _classify_pattern_type(
    pattern_norm: str,
    samples: list[str],
    pattern_config: dict[str, Any],
) -> str:
    """Classify a pattern as 'merchant', 'generic', 'gambling', or 'ambiguous'.

    Classification rules are loaded from config.json (pattern_classification section)
    rather than hardcoded, making it easy to update without code changes.

    - 'merchant': specific business name → initial_engine (merchant_kb.csv)
    - 'generic': descriptive keyword → catch_all_engine
    - 'gambling': betting/casino → gambling_engine (gambling_rules.csv)
    - 'rent': rent/property keyword → rent_engine (rent_rules.csv)
    - 'ambiguous': needs Claude to decide
    """
    p = pattern_norm.upper()
    all_text = p + " " + " ".join(s.upper() for s in samples)

    # ── Gambling detection (highest priority) ──
    gambling_indicators = pattern_config.get("gambling_indicators", [])
    if any(ind in all_text for ind in gambling_indicators):
        return "gambling"

    # ── Rent detection (category-level signal, whole-word to avoid substrings) ──
    rent_indicators = pattern_config.get("rent_indicators", [])
    for ind in rent_indicators:
        if re.search(r"\b" + re.escape(str(ind).strip()) + r"\b", all_text):
            return "rent"

    # ── Strong merchant signals ──
    card_purchase_prefixes = pattern_config.get("card_purchase_prefixes", [])
    is_card_txn = any(p.startswith(prefix) for prefix in card_purchase_prefixes)

    has_round_up = "INCLUDING - ROUND-UP" in p

    business_indicators = pattern_config.get("business_indicators", [])
    has_business_ind = any(ind in all_text for ind in business_indicators)

    # Location patterns (Australia-focused)
    location_pattern = re.search(
        r'\b(?:AU|AUS)\b|'
        r'\b(?:VIC|NSW|QLD|WA|SA|TAS|NT|ACT)\b|'
        r'\b[A-Z][a-z]+\s+(?:VIC|NSW|QLD|WA|SA|TAS|NT|ACT)\b',
        p
    )
    concat_location = re.search(
        r'[A-Z]{3,}(?:AU|AUS)\b',
        p
    )
    has_location = bool(location_pattern) or bool(concat_location)

    if is_card_txn or has_round_up or has_business_ind or has_location:
        # Exception: PayPal to individuals, person names
        if is_card_txn and any(ind in p for ind in ["PAYPAL *", "PAYPAL "]):
            return "ambiguous"
        return "merchant"

    # ── Generic keyword detection ──
    generic_keywords = pattern_config.get("generic_keywords", [])
    generic_score = sum(1 for kw in generic_keywords if kw in p)
    if generic_score >= 1:
        return "generic"

    # ── Known ambiguous patterns ──
    online_indicators = pattern_config.get("online_indicators", [])
    if any(ind in all_text for ind in online_indicators):
        return "ambiguous"

    return "ambiguous"


# ── main logic ───────────────────────────────────────────────────────────────

def analyze_gaps(
    input_path: Path,
    output_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Read pre-classified report, find gaps, output per-engine summaries."""
    engine_configs = config["engines"]
    pattern_config = config.get("pattern_classification", {})

    # 1. Load data
    project_root = Path(__file__).resolve().parent.parent
    df = read_transactions(input_path)
    total = len(df)
    log.info("Total transactions: %s", f"{total:,}")

    # Load category catalog for engine routing
    catalog = load_category_catalog(project_root)
    category_to_engine = catalog.get("category_to_primary_engine", {})

    has_illion = "category" in df.columns and "third_party" in df.columns

    if "classification_status" not in df.columns:
        log.error("Input must be a pre-classified .xlsx report.")
        log.error("The report must have a 'classification_status' column.")
        log.error("Run the finv_category_V2 pipeline first, then use the output .xlsx here.")
        raise SystemExit(1)

    # 2. Split classified / unclassified
    uncl_mask = df["classification_status"].str.strip().str.lower() == "unclassified"
    uncl = df[uncl_mask]
    cl = df[~uncl_mask]

    log.info("Classified: %s", f"{len(cl):,}")
    log.info("Unclassified: %s", f"{len(uncl):,}")

    if has_illion:
        illion_labeled = uncl["category"].notna() & (uncl["category"] != "")
        log.info("Unclassified with illion label: %s", f"{illion_labeled.sum():,}")
        log.info("Unclassified without illion label: %s", f"{(~illion_labeled).sum():,}")

    # 3. Cluster unclassified by normalized text
    min_freq = config["analysis"]["min_pattern_frequency"]
    min_len = config["analysis"]["min_pattern_length"]

    pattern_groups: dict[tuple[str, str], dict[str, Any]] = {}

    for _, row in uncl.iterrows():
        raw_text = str(row.get("text", ""))
        norm_text = normalize_pattern_text(raw_text, config)

        if len(norm_text) < min_len:
            continue

        illion_cat = str(row.get("category", "")).strip() if has_illion else ""
        illion_tp = str(row.get("third_party", "")).strip() if has_illion else ""

        key = (norm_text, illion_cat)

        if key not in pattern_groups:
            pattern_groups[key] = {
                "pattern_norm": norm_text,
                "illion_category": illion_cat,
                "count": 0,
                "samples": [],
                "third_parties": set(),
            }

        g = pattern_groups[key]
        g["count"] += 1
        if len(g["samples"]) < 5:
            g["samples"].append(raw_text[:200])
        if illion_tp:
            g["third_parties"].add(illion_tp)

    # Convert third_parties to list for JSON
    for g in pattern_groups.values():
        g["third_parties"] = sorted(g["third_parties"])

    # Filter by frequency
    frequent = {k: v for k, v in pattern_groups.items() if v["count"] >= min_freq}
    log.info("Unique patterns (freq >= %d): %d", min_freq, len(frequent))

    # 4. Assign patterns to engines with smart classification
    gap_summary: dict[str, list[dict[str, Any]]] = {}

    for eng_id in engine_configs:
        gap_summary[eng_id] = []

    for (norm_text, illion_cat), info in sorted(
        frequent.items(), key=lambda x: -x[1]["count"]
    ):
        pattern_type = _classify_pattern_type(norm_text, info["samples"], pattern_config)

        # Route based on pattern type
        if pattern_type == "merchant":
            target = "initial"
        elif pattern_type == "gambling":
            target = "gambling"
        elif pattern_type == "rent" or illion_cat == "Rent":
            target = "rent"
        elif pattern_type == "generic":
            target = "catch_all"
        else:
            # ambiguous: use category catalog to find primary owner engine
            target = category_to_engine.get(illion_cat, "catch_all")

        if target not in gap_summary:
            target = "catch_all"

        top_n = engine_configs.get(target, {}).get("top_n_gaps", 100)

        if len(gap_summary[target]) < top_n:
            info["pattern_type"] = pattern_type
            gap_summary[target].append(info)

    # 5. Disagreement analysis (illion vs finv)
    disagreements: list[dict[str, Any]] = []
    if has_illion and "finv_category" in cl.columns:
        disagree_mask = cl["category"].fillna("") != cl["finv_category"].fillna("")
        disagree_df = cl[disagree_mask]
        log.info("illion-vs-finv disagreements: %s", f"{len(disagree_df):,}")

        dis_groups = (
            disagree_df.groupby(["finv_category", "category", "classification_engine"])
            .size()
            .sort_values(ascending=False)
        )

        for (finv_cat, illion_cat, engine), count in dis_groups.head(50).items():
            samples = (
                disagree_df[
                    (disagree_df["finv_category"] == finv_cat)
                    & (disagree_df["category"] == illion_cat)
                ]["text"]
                .head(5)
                .tolist()
            )
            disagreements.append({
                "finv_category": str(finv_cat),
                "illion_category": str(illion_cat),
                "engine": str(engine),
                "count": int(count),
                "samples": [str(s)[:200] for s in samples],
            })

    # 6. Write output
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / "gap_summary.json"
    summary = {
        "input_file": str(input_path),
        "total_transactions": total,
        "classified_count": len(cl),
        "unclassified_count": len(uncl),
        "illion_labeled_count": int(illion_labeled.sum()) if has_illion else 0,
        "unique_patterns_found": len(frequent),
        "disagreement_count": len(disagree_df) if has_illion else 0,
        "engines": gap_summary,
        "disagreements": disagreements,
        "category_catalog": catalog.get("categories", {}),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    log.info("Gap summary → %s", summary_path)

    # Per-engine breakdown
    for eng_id, gaps in gap_summary.items():
        if gaps:
            top_cats = Counter(g["illion_category"] for g in gaps)
            log.info("  %s: %d patterns, top illion categories: %s",
                     eng_id, len(gaps), top_cats.most_common(5))

    return gap_summary


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging("analyze_gaps")
    parser = argparse.ArgumentParser(
        description="Discover high-frequency unclassified patterns from transaction reports."
    )
    parser.add_argument("--input", required=True, help="Path to .xlsx or .csv input.")
    parser.add_argument("--output", required=True, help="Directory for gap_summary.json.")
    parser.add_argument("--config", default=None, help="Path to config.json.")
    parser.add_argument("--min_freq", type=int, default=None, help="Override min_pattern_frequency.")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root)

    if args.min_freq is not None:
        config["analysis"]["min_pattern_frequency"] = args.min_freq

    analyze_gaps(
        input_path=Path(args.input),
        output_dir=Path(args.output),
        config=config,
    )


if __name__ == "__main__":
    main()
