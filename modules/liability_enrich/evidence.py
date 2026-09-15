#!/usr/bin/env python3
"""Step 3 of the liability enrichment pipeline — verify candidates against data.

Step 2 (the `liability-enrichment` skill) proposes keywords from web research.
This step is the gate that decides whether the data agrees. Every candidate is
replayed against the real transaction report using the *exact* regex the
liability engine uses, and scored on what it would actually do.

Why the exact regex matters
---------------------------
``liability_engine`` matches with ``(?<![A-Za-z])kw(?![A-Za-z])`` on
whitespace-collapsed, uppercased text — **not** ``\\b``. A looser check here
(a plain ``str.contains``) would report hits that production never sees, and
miss the digit-leak failure where "360 CASH LOANS" also matches inside
"1360 CASH LOANS". ``_shared`` mirrors the engine so this cannot drift.

What each candidate gets
-------------------------
``hit_count``          rows the keyword matches.
``hit_unclassified``   …of which nothing has claimed yet — the real gain.
``hit_liability``      …already in a liability category — reinforcement.
``hit_other``          …currently owned by *another* engine. Because
                       liability runs at priority 300, every one of these
                       would be **stolen**. This is the risk the reviewer is
                       being asked to accept.
``risk_level``         高 / 中 / 低 from the stolen share; 零增益 when nothing
                       matched at all.

Output
------
``<candidates>`` is rewritten in place with ``hit_count`` / ``risk_level`` /
``samples`` / ``status`` filled, plus a sibling ``liability_evidence.json``
carrying the full breakdown. The extra diagnostics live in the JSON rather
than the CSV on purpose: any column not listed in ``common.META_COLUMNS``
would be stripped by apply_rules and land in ``raw/`` as rule data.

Usage:
    python modules/liability_enrich/evidence.py \\
        --candidates reviews/2026-09-14/liability_candidates.csv \\
        --input input/202609091024.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    load_config,
    read_transactions,
    resolve_rules_base,
    setup_logging,
)
from _shared import (  # noqa: E402
    COUNTERPARTY_RULE_FILE,
    keyword_pattern,
    liability_categories,
    normalize_match_text,
    split_variants,
    validate_keyword,
)

log = setup_logging("liability_evidence")

# status values. apply_rules only applies rows whose status is exactly
# "confirmed", so every verdict here is deliberately a non-match and a human
# still has to make the call.
STATUS_CONFIRM = "☐ confirm"        # worth a look
STATUS_NO_GAIN = "✗ 零增益"          # zero hits — nothing to gain, drop it
STATUS_DEAD = "✗ 死规则"             # malformed keyword, can never fire

# Share of hits that must be stolen from another engine to call it 高.
_HIGH_RISK_SHARE = 0.5


def _load_candidates(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    if "keyword" not in df.columns:
        raise ValueError(f"{path} 缺少 'keyword' 列，不是候选规则文件。")
    for col in ("hit_count", "risk_level", "samples", "status"):
        if col not in df.columns:
            df[col] = ""
    return df


def _known_counterparties(project_root: Path, config: dict[str, Any]) -> set[str]:
    path = resolve_rules_base(project_root, config) / "liability_rule" / COUNTERPARTY_RULE_FILE
    if not path.exists():
        return set()
    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
    return {str(c).strip() for c in df.get("counterparty", []) if str(c).strip()}


def _rank_hits_mask(normalized: pd.Series, variants: list[str]) -> pd.Series:
    """OR together the engine-exact regex for every variant."""
    mask = pd.Series(False, index=normalized.index)
    for variant in variants:
        mask |= normalized.str.contains(keyword_pattern(variant), regex=True, na=False)
    return mask


def evaluate(
    candidates_path: Path,
    input_path: Path,
    output_path: Path | None,
    config: dict[str, Any],
    max_samples: int = 5,
) -> dict[str, Any]:
    """Score every candidate against the report and rewrite the CSV."""
    project_root = REPO_ROOT
    df = read_transactions(input_path)

    for required in ("text", "classification_status"):
        if required not in df.columns:
            raise ValueError(
                f"报告缺少 '{required}' 列。输入必须是 finv_category_V2 跑完流水线后导出的报告。"
            )

    normalized = normalize_match_text(df["text"])
    status = df["classification_status"].fillna("").astype(str).str.strip().str.lower()
    finv_cat = df.get("finv_category", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    engine = df.get("classification_engine", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    counterparty = df.get("counterparty", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()

    liability_cats = liability_categories(project_root)
    known = _known_counterparties(project_root, config)
    log.info("liability 类别 %d 个 | 现有对照方 %d 个", len(liability_cats), len(known))

    cand = _load_candidates(candidates_path)
    log.info("候选 %d 条", len(cand))

    diagnostics: list[dict[str, Any]] = []

    for idx, row in cand.iterrows():
        raw_keyword = str(row.get("keyword", ""))
        variants = split_variants(raw_keyword)
        desired_cp = str(row.get("counterparty", "")).strip()

        problems = [p for p in (validate_keyword(v) for v in variants) if p]

        if not variants:
            cand.at[idx, "status"] = STATUS_DEAD
            cand.at[idx, "risk_level"] = "死规则"
            cand.at[idx, "hit_count"] = "0"
            diagnostics.append({
                "keyword": raw_keyword, "variants": [], "counterparty": desired_cp,
                "product_type": str(row.get("product_type", "")).strip(),
                "is_new_counterparty": desired_cp not in known,
                "hit_count": 0, "hit_unclassified": 0, "hit_liability": 0, "hit_other": 0,
                "risk_level": "死规则", "status": STATUS_DEAD,
                "conflict_engines": [], "already_claimed_by": [], "samples": [],
                "problems": problems or ["keyword 为空"],
            })
            continue

        mask = _rank_hits_mask(normalized, variants)
        n_hit = int(mask.sum())

        if n_hit:
            # Three mutually exclusive buckets, so n_other is exactly the
            # remainder rather than a fourth independent test.
            m_unclassified = mask & (status == "unclassified")
            m_liability = mask & finv_cat.isin(liability_cats) & ~m_unclassified
            m_other = mask & ~m_unclassified & ~m_liability

            n_unclassified = int(m_unclassified.sum())
            n_liability = int(m_liability.sum())
            n_other = int(m_other.sum())
            conflict_engines = Counter(engine[m_other]).most_common(5)
            covered_by = Counter(c for c in counterparty[mask] if c).most_common(3)

            # Samples: lead with the rows this rule would take away from
            # another engine — that is what the reviewer must judge.
            samples: list[str] = []
            for source in (df[m_other], df[mask]):
                for text in source["text"]:
                    s = str(text)[:200]
                    if s not in samples:
                        samples.append(s)
                    if len(samples) >= max_samples:
                        break
                if len(samples) >= max_samples:
                    break
        else:
            n_unclassified = n_liability = n_other = 0
            conflict_engines = []
            covered_by = []
            samples = []

        if n_hit == 0:
            risk, new_status = "零增益", STATUS_NO_GAIN
        else:
            stolen_share = n_other / n_hit
            risk = "高" if stolen_share >= _HIGH_RISK_SHARE else ("中" if n_other else "低")
            new_status = STATUS_CONFIRM
        if problems:
            risk, new_status = "死规则", STATUS_DEAD

        cand.at[idx, "hit_count"] = str(n_hit)
        cand.at[idx, "risk_level"] = risk
        cand.at[idx, "samples"] = " | ".join(samples)
        cand.at[idx, "status"] = new_status

        diagnostics.append({
            "keyword": raw_keyword,
            "variants": variants,
            "counterparty": desired_cp,
            "product_type": str(row.get("product_type", "")).strip(),
            "is_new_counterparty": desired_cp not in known,
            "hit_count": n_hit,
            "hit_unclassified": n_unclassified,
            "hit_liability": n_liability,
            "hit_other": n_other,
            "risk_level": risk,
            "status": new_status,
            "conflict_engines": [{"engine": e, "count": c} for e, c in conflict_engines],
            "already_claimed_by": [{"counterparty": c, "count": n} for c, n in covered_by],
            "samples": samples,
            "problems": problems,
        })

    # ── write back ──
    dest = output_path or candidates_path
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    cand.to_csv(tmp, index=False, encoding="utf-8-sig")
    tmp.replace(dest)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_file": str(input_path),
        "candidates_file": str(dest),
        "candidate_count": len(cand),
        "verdicts": dict(Counter(d["status"] for d in diagnostics)),
        "new_counterparty_count": sum(1 for d in diagnostics if d["is_new_counterparty"]),
        "total_hits": sum(d["hit_count"] for d in diagnostics),
        "total_net_gain": sum(d.get("hit_unclassified", 0) for d in diagnostics),
        "total_would_steal": sum(d.get("hit_other", 0) for d in diagnostics),
        "notes": [
            "hit_other 是会被本规则从其他引擎抢走的行数 —— liability 优先级 300，"
            "晚于 transfer/initial/dishonour/income，早于 all_other_credit/fee/rent/catch_all。",
            "status 一律不是 'confirmed'，apply_rules 不会自动写入；必须人工改写后才会生效。",
            "零增益的候选说明联网假设未获数据支持，建议直接丢弃。",
        ],
        "candidates": diagnostics,
    }
    report_path = dest.parent / "liability_evidence.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("候选已更新 → %s", dest)
    log.info("证据报告   → %s", report_path)
    for status_name, count in report["verdicts"].items():
        log.info("  %-14s %d", status_name, count)
    log.info("命中合计 %d（其中未分类 %d / 会抢走 %d）",
             report["total_hits"], report["total_net_gain"], report["total_would_steal"])

    return report


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="用真实交易数据验证 liability 候选规则，回填 hit_count/risk_level/samples/status。"
    )
    parser.add_argument("--candidates", required=True, help="候选规则 CSV（会被就地更新）")
    parser.add_argument("--input", required=True, help="分类报告 .xlsx 或 .csv")
    parser.add_argument("--output", default=None,
                        help="候选 CSV 的输出路径（默认就地更新）")
    parser.add_argument("--max-samples", type=int, default=5, help="每条候选最多保留几个样本")
    args = parser.parse_args()

    config = load_config(REPO_ROOT)
    evaluate(
        candidates_path=Path(args.candidates),
        input_path=Path(args.input),
        output_path=Path(args.output) if args.output else None,
        config=config,
        max_samples=args.max_samples,
    )


if __name__ == "__main__":
    main()
