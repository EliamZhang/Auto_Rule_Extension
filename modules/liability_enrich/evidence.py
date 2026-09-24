#!/usr/bin/env python3
"""Step 3 of the liability enrichment pipeline — verify candidates against data.

Step 2 (the `liability-enrichment` skill) proposes rules from web research.
This step is the gate that decides whether the data agrees. Every candidate is
replayed against the real transaction report using the *exact* matching the
liability engine uses, and scored on what it would actually do.

Two target files, two matchings
-------------------------------
Dispatch is on each candidate's ``target_file``; ``_shared.resolve_rule_file``
refuses anything else rather than guessing.

``counterparty_keyword_rules.csv``
    ``keyword`` column, ``;``-separated literal variants, matched against
    whitespace-collapsed **uppercased** text with ``(?<![A-Za-z])kw(?![A-Za-z])``
    — **not** ``\\b``. A looser check here (a plain ``str.contains``) would
    report hits production never sees, and miss the digit-leak failure where
    "360 CASH LOANS" also matches inside "1360 CASH LOANS".

``home_loan_car_loan_rules.csv``
    Format A: ``pattern`` column holding a **regex**, matched against the
    **raw** ``text`` column with ``IGNORECASE`` only — no whitespace collapse,
    no uppercasing. Sets ``is_home_loan`` / ``is_car_loan`` instead of naming a
    counterparty, so ``product_type`` is what a hit changes.

Scoring a Format A candidate with counterparty semantics (or the reverse) is
silent on both sides: it looks healthy here and never fires in production.

What each candidate gets
-------------------------
``hit_count``          rows the rule matches.
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
        --candidates reviews/2026-09-09_1025/liability_candidates.csv \\
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
    FLAG_RULE_FILE,
    NAME_COLUMNS,
    flag_condition_mask,
    flag_rule_regex,
    flag_scope_mask,
    keyword_pattern,
    liability_categories,
    normalize_match_text,
    raw_match_text,
    resolve_rule_file,
    split_variants,
    validate_flag_rule,
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
    if "keyword" not in df.columns and "pattern" not in df.columns:
        raise ValueError(f"{path} 既没有 'keyword' 也没有 'pattern' 列，不是候选规则文件。")
    for col in ("hit_count", "risk_level", "samples", "status"):
        if col not in df.columns:
            df[col] = ""
    if "target_file" not in df.columns:
        # Candidates written before the two-file contract were all counterparty rows.
        df["target_file"] = ""
    # Fail fast on an unknown target file: scoring it under the wrong contract
    # would look fine here and never fire in production.
    df["target_file"] = df["target_file"].map(resolve_rule_file)
    return df


def _known_merchants(project_root: Path, config: dict[str, Any]) -> dict[str, set[str]]:
    """Merchant names already present in each rule file, keyed by file name.

    Both files matter: a home-loan alias has to be attached to the name already
    sitting in ``home_loan_car_loan_rules.csv``'s ``rule_name`` column, not just
    to a ``counterparty`` in the other file.
    """
    base = resolve_rules_base(project_root, config) / "liability_rule"
    out: dict[str, set[str]] = {}
    for filename, column in NAME_COLUMNS.items():
        path = base / filename
        if not path.exists():
            log.warning("未找到现有规则文件: %s", path)
            out[filename] = set()
            continue
        df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")
        out[filename] = {str(v).strip() for v in df.get(column, []) if str(v).strip()}
    return out


def _rank_hits_mask(normalized: pd.Series, variants: list[str]) -> pd.Series:
    """OR together the engine-exact regex for every variant."""
    mask = pd.Series(False, index=normalized.index)
    for variant in variants:
        mask |= normalized.str.contains(keyword_pattern(variant), regex=True, na=False)
    return mask


def _plan_row(row: pd.Series) -> dict[str, Any]:
    """Decide how to match one candidate row — and whether it can fire at all.

    Dispatch is on ``target_file``. The two merchant files share nothing but
    the engine reading them, so applying one file's contract to the other's
    candidate is exactly the silent failure this module exists to catch.
    """
    rule_file = str(row.get("target_file", "")).strip()

    if rule_file == FLAG_RULE_FILE:
        pattern = str(row.get("pattern", "")).strip()
        match_type = str(row.get("match_type", "")).strip().lower()
        match_scope = str(row.get("match_scope", "")).strip().lower()
        has_conditions = any(
            str(row.get(f, "") or "").strip() not in ("", "*")
            for f in ("account_type", "dr_cr", "bank")
        ) or str(row.get("amount_gt", "") or "").strip() != ""
        problem = validate_flag_rule(
            pattern=pattern,
            match_type=match_type,
            match_scope=match_scope,
            target_field=row.get("target_field"),
            enabled=row.get("enabled"),
            has_conditions=has_conditions,
        )
        return {
            "kind": "flag",
            "target_file": rule_file,
            "variants": [pattern] if pattern else [],
            "regex": flag_rule_regex(pattern, match_type, match_scope),
            "scope": match_scope or "text",
            "label": str(row.get("rule_name", "")).strip(),
            "problems": [problem] if problem else [],
        }

    # Counterparty file. `match_type` is deliberately ignored: the loader reads
    # `rule_type`, which this CSV does not have, so every row is a literal
    # keyword however the candidate labels itself.
    raw_keyword = str(row.get("keyword", ""))
    variants = split_variants(raw_keyword)
    problems = [p for p in (validate_keyword(v) for v in variants) if p]
    if not variants:
        problems = problems or ["keyword 为空"]
    return {
        "kind": "counterparty",
        "target_file": rule_file,
        "variants": variants,
        "regex": None,
        "scope": "text",
        "label": str(row.get("counterparty", "")).strip(),
        "problems": problems,
    }


def _match_mask(
    plan: dict[str, Any],
    row: pd.Series,
    df: pd.DataFrame,
    normalized: pd.Series,
    text_raw: pd.Series,
    counterparty_raw: pd.Series,
) -> tuple[pd.Series, list[str]]:
    """Rows one candidate would touch, plus any condition it cannot replay."""
    if plan["kind"] != "flag":
        return _rank_hits_mask(normalized, plan["variants"]), []

    mask = flag_scope_mask(text_raw, counterparty_raw, plan["scope"], plan["regex"])
    if plan["scope"] != "all":
        return mask, []
    cond_mask, problems = flag_condition_mask(df, row)
    return mask & cond_mask, problems


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
    text_raw = raw_match_text(df["text"])
    counterparty_raw = (
        raw_match_text(df["counterparty"]) if "counterparty" in df.columns
        else pd.Series("", index=df.index)
    )
    status = df["classification_status"].fillna("").astype(str).str.strip().str.lower()
    finv_cat = df.get("finv_category", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    engine = df.get("classification_engine", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()
    counterparty = df.get("counterparty", pd.Series([""] * len(df))).fillna("").astype(str).str.strip()

    liability_cats = liability_categories(project_root)
    known = _known_merchants(project_root, config)
    log.info("liability 类别 %d 个 | 现有商户 —— %s: %d / %s: %d",
             len(liability_cats),
             COUNTERPARTY_RULE_FILE, len(known[COUNTERPARTY_RULE_FILE]),
             FLAG_RULE_FILE, len(known[FLAG_RULE_FILE]))

    cand = _load_candidates(candidates_path)
    log.info("候选 %d 条（%s）", len(cand),
             "；".join(f"{f}: {n}" for f, n in Counter(cand["target_file"]).most_common()))

    diagnostics: list[dict[str, Any]] = []

    for idx, row in cand.iterrows():
        plan = _plan_row(row)
        rule_file = plan["target_file"]
        label = plan["label"]
        problems = list(plan["problems"])

        if problems:
            mask = pd.Series(False, index=df.index)
        else:
            mask, problems = _match_mask(plan, row, df, normalized, text_raw, counterparty_raw)
            if problems:
                # A condition we cannot replay makes any count meaningless, and
                # a partial mask would report a confidently wrong number.
                mask = pd.Series(False, index=df.index)

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

        if problems:
            risk, new_status = "死规则", STATUS_DEAD
        elif n_hit == 0:
            risk, new_status = "零增益", STATUS_NO_GAIN
        else:
            stolen_share = n_other / n_hit
            risk = "高" if stolen_share >= _HIGH_RISK_SHARE else ("中" if n_other else "低")
            new_status = STATUS_CONFIRM

        cand.at[idx, "hit_count"] = str(n_hit)
        cand.at[idx, "risk_level"] = risk
        cand.at[idx, "samples"] = " | ".join(samples)
        cand.at[idx, "status"] = new_status

        diagnostics.append({
            "target_file": rule_file,
            "kind": plan["kind"],
            "label": label,
            # Format A rows carry their own identity: rule_id is what the CSV
            # row will be written as, target_field decides is_home_loan vs
            # is_car_loan. Both live only in the candidate CSV otherwise, and
            # the approval view is meant to stand on this JSON alone.
            "rule_id": str(row.get("rule_id", "")).strip(),
            "target_field": str(row.get("target_field", "")).strip(),
            "pattern": str(row.get("keyword", "") or row.get("pattern", "")).strip(),
            "variants": plan["variants"],
            "match_scope": plan["scope"],
            "match_type": str(row.get("match_type", "")).strip(),
            "counterparty": str(row.get("counterparty", "")).strip(),
            "product_type": str(row.get("product_type", "")).strip(),
            "is_new_merchant": not problems and bool(label) and label not in known[rule_file],
            "evidence_source": str(row.get("evidence_source", "")).strip(),
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
        "files": dict(Counter(d["target_file"] for d in diagnostics)),
        "new_merchant_count": sum(1 for d in diagnostics if d["is_new_merchant"]),
        # The reviewer's only handle on a brand-new merchant is "why do you say
        # this is a lender?" — an unsourced proposal is un-reviewable.
        "new_merchants_without_source": [
            d["label"] for d in diagnostics if d["is_new_merchant"] and not d["evidence_source"]
        ],
        "total_hits": sum(d["hit_count"] for d in diagnostics),
        "total_net_gain": sum(d.get("hit_unclassified", 0) for d in diagnostics),
        "total_would_steal": sum(d.get("hit_other", 0) for d in diagnostics),
        "notes": [
            "hit_other 是当前属于其他引擎、但会被本规则整行覆盖掉的行数 —— liability 优先级 300，"
            "晚于 transfer(1)/initial(10)/dishonour(150)/gambling(180)/income(200)，"
            "早于 all_other_credit(400)/fee(500)/rent(800)/catch_all(999)。",
            "⚠️ 该计数只按 finv_category 分桶，未复刻 orchestrator 的候选集排除："
            "Wages/Centrelink 行不会被 liability 认领，gambling 已认领的行对 liability 是终局，"
            "fee 等后置引擎还可能把行抢回去。所以 hit_other 会高估风险 ——"
            "审批时结合 conflict_engines 明细判断，不要只看数字。",
            "命中按 target_file 用两套语义算：counterparty_keyword_rules.csv 是"
            "「空白压缩 + 大写 + (?<![A-Za-z])kw(?![A-Za-z])」，home_loan_car_loan_rules.csv 是"
            "「原始 text 列 + IGNORECASE」的 Format A。同一份报告两边都算过，但结果不可互换 ——"
            "所以 candidates 里每条都带 kind/target_file。",
            "home_loan_car_loan_rules.csv 的候选不产出 counterparty：命中后由 _TARGET_METADATA_MAP "
            "无条件写 product_type=home_loan/car_loan，counterparty 只在为空时填 'Home Loan'/'Car Loan'。"
            "它的 label 是 rule_name（loader 忽略该列，仅供人读和 search_merchant 检索）。",
            "⚠️ Format A 的 keyword 型规则被引擎按 target_field 合并成一个 \\b(?:…)\\b 再匹配，"
            "单行打分只会少算不会多算（合并只可能增加命中）。regex 型规则各自独立，不受影响。",
            "problems 非空的候选 hit_count 一律记 0 —— 规则在 production 里就是不会命中，"
            "报一个按错误语义算出来的命中数只会误导审批。problems 里写了原因和改法。",
            "每条候选的 evidence_source（联网来源 URL）已带进本文件，供审批复核 ——"
            "新商户（is_new_merchant=true）没有来源就是没有可核的依据，"
            "名字会列在顶层的 new_merchants_without_source 里，先补搜再送审。",
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
    if report["new_merchants_without_source"]:
        log.warning("⚠️ %d 条新商户没有联网来源，审批无法复核：%s —— 补搜后再送审",
                    len(report["new_merchants_without_source"]),
                    "、".join(report["new_merchants_without_source"]))

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
