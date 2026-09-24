#!/usr/bin/env python3
"""Step 1 of the liability enrichment pipeline — find suspected-lender gaps.

Reads a finv_category_V2 classification report and extracts three classes of
transactions that *might* be lenders the liability_engine failed to recognise.

This module only gathers evidence. It never proposes or writes rules — turning
gaps into candidates is Step 2 (the `liability-enrichment` skill), and every
candidate still has to clear the normal approval gate.

The three classes
-----------------
``generic_loan_catchall``
    ``finv_category == "Non SACC Loans"`` and ``counterparty == "Generic Loans"``.
    These hit ``apply_generic_loan_catchall`` — the engine's own ``\\bLOAN\\b``
    backstop. It knew it was looking at a loan but had no named counterparty.

``unclassified_loan_signal``
    ``classification_status == "unclassified"`` and the text matches
    ``LOAN_SIGNAL_RE``. Nothing claimed them, but the wording is lender-ish.
    Entries matching only the weak tokens (CASH / CREDIT / FINANCE) are tagged
    ``signal_strength: weak`` and ranked last rather than dropped.

``illion_liability_missed``
    illion's own label is a liability *loan* category while finv's is not.
    illion is the semi-automatic ground truth, so this is the strongest single
    signal that a counterparty rule is missing. Measured on real data, this
    class has by far the highest yield.

A transaction is assigned to exactly one class, priority
``generic_loan_catchall`` > ``unclassified_loan_signal`` > ``illion_liability_missed``.
The other signals are still recorded on the entry, so nothing is dropped.

Output: ``<output>/liability_gaps.json``

Usage:
    python modules/liability_enrich/gap_source.py \\
        --input input/202609091024.xlsx \\
        --output reviews/2026-09-09_1025/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402  (path must be set up first)
    load_config,
    normalize_pattern_text,
    read_transactions,
    resolve_rules_base,
    setup_logging,
)
from _shared import (  # noqa: E402
    COUNTERPARTY_RULE_FILE,
    FLAG_RULE_FILE,
    GENERIC_LOAN_COUNTERPARTY,
    liability_categories,
)

log = setup_logging("liability_gaps")

# ── Tunables ─────────────────────────────────────────────────────────────────

# The spec's four core signals (LOAN / CREDIT / CASH / FINANCE) plus the
# lexically direct lender words. Being generous here is cheap: a wider class B
# only means more entries for the skill to web-verify, and a false positive
# cannot reach raw/ without passing the approval gate.
LOAN_SIGNAL_RE = re.compile(
    r"\b(?:LOAN|CREDIT|CASH|FINANCE|LENDER|LENDING|LEND|BORROW|PAYDAY|PAWN|BNPL)\b",
    re.IGNORECASE,
)

# Subset of the above that actually indicates a lender. Measured on
# input/202609091024.xlsx, the CASH/CREDIT/FINANCE-only tail of class B is
# overwhelmingly ATM withdrawals, cash deposits and interest credits — real
# but near-zero-yield, and every entry costs the skill a web search. Entries
# matching only the weak tokens are therefore ranked last, not dropped.
STRONG_SIGNAL_RE = re.compile(
    r"\b(?:LOAN|LENDER|LENDING|LEND|BORROW|PAYDAY|PAWN|BNPL)\b",
    re.IGNORECASE,
)

# Counterparty written by finv's generic-loan backstop (liability_engine/pipeline.py).
GENERIC_LOAN_COUNTERPARTY = "Generic Loans"


# ── helpers ──────────────────────────────────────────────────────────────────

def _rule_path(project_root: Path, config: dict[str, Any], filename: str) -> Path:
    return resolve_rules_base(project_root, config) / "liability_rule" / filename


def _rel(project_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project_root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _summarize_counterparty_rules(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """What `counterparty_keyword_rules.csv` already covers."""
    path = _rule_path(project_root, config, COUNTERPARTY_RULE_FILE)
    if not path.exists():
        log.warning("未找到现有规则文件: %s", path)
        return {"source_file": _rel(project_root, path), "row_count": 0,
                "counterparties": [], "keywords": [], "product_types": {}}

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")

    # keyword column holds semicolon-separated variants; `keyword` variants are
    # plain text, `regex` ones are patterns and must not be treated as literals.
    keywords: set[str] = set()
    for _, row in df.iterrows():
        if str(row.get("match_type", "")).strip().lower() == "regex":
            continue
        for variant in str(row.get("keyword", "")).split(";"):
            variant = variant.strip().upper()
            if variant:
                keywords.add(variant)

    return {
        "source_file": _rel(project_root, path),
        "row_count": int(len(df)),
        "counterparties": sorted({str(c).strip() for c in df["counterparty"] if str(c).strip()}),
        "keywords": sorted(keywords),
        "product_types": dict(Counter(str(p).strip() for p in df["product_type"] if str(p).strip())),
    }


def _summarize_flag_rules(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """What `home_loan_car_loan_rules.csv` (Format A) already covers.

    Its `pattern` is a **regex** for `regex` rows and a `;`-separated keyword
    list for `keyword` rows, so the two are reported separately — merging them
    into one "keywords" list would let the skill mistake half a regex for a
    merchant name.

    The merchant name lives in `rule_name`, a column the engine's loader
    ignores. It is the name a human reads and the one `search_merchant.py`
    searches, so it is the only place a home/car-loan lender's identity is
    recorded at all.
    """
    path = _rule_path(project_root, config, FLAG_RULE_FILE)
    empty = {
        "source_file": _rel(project_root, path), "row_count": 0,
        "merchants": [], "regex_patterns": [], "keyword_patterns": [],
        "target_fields": {}, "match_scopes": {},
    }
    if not path.exists():
        log.warning("未找到现有规则文件: %s", path)
        return empty

    df = pd.read_csv(path, encoding="utf-8-sig", dtype=str).fillna("")

    regex_patterns: list[str] = []
    keyword_patterns: list[str] = []
    for _, row in df.iterrows():
        pattern = str(row.get("pattern", "")).strip()
        if not pattern:
            continue  # 纯条件规则（如 HL009），没有文本模式可列
        if str(row.get("match_type", "")).strip().lower() == "keyword":
            keyword_patterns.append(pattern.upper())
        else:
            regex_patterns.append(pattern)

    return {
        "source_file": _rel(project_root, path),
        "row_count": int(len(df)),
        "merchants": sorted({str(m).strip() for m in df.get("rule_name", []) if str(m).strip()}),
        "regex_patterns": sorted(regex_patterns),
        "keyword_patterns": sorted(keyword_patterns),
        "target_fields": dict(Counter(str(v).strip() for v in df.get("target_field", []) if str(v).strip())),
        "match_scopes": dict(Counter(str(v).strip() for v in df.get("match_scope", []) if str(v).strip())),
    }


def _load_existing_rules(project_root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """Summarise what the liability merchant files already cover.

    The skill needs this to answer two questions per gap: "is this already
    covered?" and "is this an alias/truncation of a merchant I already have?".

    Both files are summarised, because they cover different merchant
    populations: the counterparty file holds general lenders under
    `counterparty`, the flag file holds home/car-loan lenders under
    `rule_name`. Reading only the first is how `Advantedge`,
    `Toyota Finance` and `Firstmac` get re-proposed as brand-new merchants.

    The flat keys describe the counterparty file (that is what they meant
    before the two-file contract); the flag file is nested under
    `flag_rule_file`.
    """
    return {
        **_summarize_counterparty_rules(project_root, config),
        "flag_rule_file": _summarize_flag_rules(project_root, config),
    }


def _collect(
    df: pd.DataFrame,
    mask: pd.Series,
    class_name: str,
    config: dict[str, Any],
    min_freq: int,
) -> list[dict[str, Any]]:
    """Group the selected rows by normalized text and summarise each group."""
    subset = df[mask]
    groups: dict[str, dict[str, Any]] = {}

    has_amount = "amount" in subset.columns
    has_drcr = "dr_cr" in subset.columns

    for _, row in subset.iterrows():
        raw_text = str(row.get("text", ""))
        norm = normalize_pattern_text(raw_text, config)
        if not norm:
            continue

        g = groups.get(norm)
        if g is None:
            g = groups[norm] = {
                "pattern_norm": norm,
                "gap_class": class_name,
                "count": 0,
                "samples": [],
                "third_parties": set(),
                "illion_categories": set(),
                "finv_categories": set(),
                "engines": set(),
                "dr_cr": set(),
                "amounts": [],
            }

        g["count"] += 1
        if len(g["samples"]) < 5:
            g["samples"].append(raw_text[:200])
        for field, key in (
            ("third_party", "third_parties"),
            ("category", "illion_categories"),
            ("finv_category", "finv_categories"),
            ("classification_engine", "engines"),
            ("dr_cr", "dr_cr"),
        ):
            val = str(row.get(field, "") or "").strip()
            if val:
                g[key].add(val)
        if has_amount:
            try:
                amt = float(row["amount"])
                if amt == amt:  # not NaN
                    g["amounts"].append(amt)
            except (TypeError, ValueError):
                pass

    out: list[dict[str, Any]] = []
    # Only class B was selected *by* the text signal, so only there does
    # signal_strength mean anything. A and C are selected for other reasons
    # (the engine's own backstop, illion's label) and their text holds the
    # lender's name rather than a loan word.
    rank_by_strength = class_name == "unclassified_loan_signal"

    for g in groups.values():
        if g["count"] < min_freq:
            continue
        amounts = sorted(g.pop("amounts"))
        if amounts:
            g["amount_min"] = round(amounts[0], 2)
            g["amount_median"] = round(amounts[len(amounts) // 2], 2)
            g["amount_max"] = round(amounts[-1], 2)
        for key in ("third_parties", "illion_categories", "finv_categories", "engines", "dr_cr"):
            g[key] = sorted(g[key])
        if rank_by_strength:
            g["signal_strength"] = "strong" if STRONG_SIGNAL_RE.search(g["pattern_norm"]) else "weak"
        out.append(g)

    if rank_by_strength:
        # Strong first (weak = only CASH/CREDIT/FINANCE matched), then by count.
        out.sort(key=lambda x: (x["signal_strength"] != "strong", -x["count"], x["pattern_norm"]))
    else:
        out.sort(key=lambda x: (-x["count"], x["pattern_norm"]))
    return out


# ── main logic ───────────────────────────────────────────────────────────────

def find_gaps(
    input_path: Path,
    output_dir: Path,
    config: dict[str, Any],
    min_freq: int = 1,
    top_n: int | None = None,
) -> dict[str, Any]:
    """Extract the three gap classes and write liability_gaps.json."""
    project_root = REPO_ROOT
    df = read_transactions(input_path)
    total = len(df)

    for required in ("text", "classification_status"):
        if required not in df.columns:
            raise ValueError(
                f"报告缺少 '{required}' 列。输入必须是 finv_category_V2 跑完流水线后导出的报告。"
            )

    status = df["classification_status"].fillna("").str.strip().str.lower()
    finv_cat = df.get("finv_category", pd.Series([""] * total)).fillna("").astype(str).str.strip()
    counterparty = df.get("counterparty", pd.Series([""] * total)).fillna("").astype(str).str.strip()
    illion_cat = df.get("category", pd.Series([""] * total)).fillna("").astype(str).str.strip()

    liability_cats = liability_categories(project_root)
    log.info("liability 类别（%d 个）: %s", len(liability_cats), "、".join(sorted(liability_cats)))

    # Text signal is evaluated on the raw text; the regex is case-insensitive.
    text_signal = df["text"].fillna("").astype(str).str.contains(LOAN_SIGNAL_RE)

    mask_a = (finv_cat == "Non SACC Loans") & (counterparty == GENERIC_LOAN_COUNTERPARTY)
    mask_b = (status == "unclassified") & text_signal
    mask_c = illion_cat.isin(liability_cats) & ~finv_cat.isin(liability_cats)

    log.info("类 A  generic_loan 兜底命中 : %s", f"{mask_a.sum():,}")
    log.info("类 B  未分类 + 放贷措辞     : %s", f"{mask_b.sum():,}")
    log.info("类 C  illion 负债类但 finv 未归类: %s", f"{mask_c.sum():,}")

    # Exclusive assignment: A > B > C. The raw masks overlap (an unclassified row
    # whose illion label is a liability category is in both B and C).
    assigned = pd.Series([""] * total, index=df.index)
    assigned[mask_c] = "illion_liability_missed"
    assigned[mask_b] = "unclassified_loan_signal"
    assigned[mask_a] = "generic_loan_catchall"

    classes: dict[str, list[dict[str, Any]]] = {}
    for name in ("generic_loan_catchall", "unclassified_loan_signal", "illion_liability_missed"):
        entries = _collect(df, assigned == name, name, config, min_freq)
        if top_n is not None:
            entries = entries[:top_n]
        classes[name] = entries
        log.info("  → %-26s %d 组模式", name, len(entries))

    existing = _load_existing_rules(project_root, config)
    flag = existing["flag_rule_file"]
    log.info("现有商户 —— %s: 对照方 %d 个 / keyword %d 条",
             existing["source_file"], len(existing["counterparties"]), len(existing["keywords"]))
    log.info("           %s: 房贷/车贷商户 %d 个 / 规则 %d 条",
             flag["source_file"], len(flag["merchants"]), flag["row_count"])

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_file": str(input_path),
        "total_transactions": total,
        "min_freq": min_freq,
        "top_n": top_n,
        "class_sizes": {
            "generic_loan_catchall": int(mask_a.sum()),
            "unclassified_loan_signal": int(mask_b.sum()),
            "illion_liability_missed": int(mask_c.sum()),
            "assigned_total": int((assigned != "").sum()),
        },
        "liability_categories": sorted(liability_cats),
        "loan_signal_regex": LOAN_SIGNAL_RE.pattern,
        "existing_rules": existing,
        "gaps": classes,
        "notes": [
            "类内按 count 降序；同一条交易只归入一个类（A > B > C），"
            "其余信号仍记在 illion_categories/finv_categories 上。",
            "signal_strength 只出现在 unclassified_loan_signal 类（只有它是由文本信号选出来的）。"
            "weak = 只命中了 CASH/CREDIT/FINANCE —— 实测 134 组里只有 1 组是 strong，"
            "其余几乎全是 ATM 取现、现金存入和利息；保留是为了不漏判，技能侧可自行跳过。",
            "另一条实测结论：三类里 illion_liability_missed 产出率最高（SECURE FUNDING、"
            "CHARTER MERCANTILE 等真实放贷商），generic_loan_catchall 大量是"
            "「向某个说不出名字的放贷商还款」，不代表能提炼出商户。",
            "pattern_norm 已剥离日期/金额/长数字并大写，仅用于聚类；生成 keyword 时以 samples 为准。",
            "existing_rules 的顶层键（counterparties / keywords / product_types）描述的是"
            " counterparty_keyword_rules.csv；home_loan_car_loan_rules.csv 单独放在 "
            "existing_rules.flag_rule_file 里（商户名在 rule_name 列，pattern 分 regex/keyword 两类）。"
            "判定「新商户 vs 已有商户别名」时必须两个都查，否则会把 Advantedge / Toyota Finance "
            "这类已存在的房贷车贷商户重复添加。",
            "本文件只列缺口，不代表任何规则建议。",
        ],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "liability_gaps.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log.info("缺口清单 → %s", out_path)

    return summary


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="从分类报告中提取疑似放贷商缺口，产出 liability_gaps.json。"
    )
    parser.add_argument("--input", required=True, help="分类报告 .xlsx 或 .csv")
    parser.add_argument("--output", required=True, help="输出目录（写入 liability_gaps.json）")
    parser.add_argument("--min-freq", type=int, default=1,
                        help="模式最低出现次数（默认 1，即全部保留）")
    parser.add_argument("--top-n", type=int, default=None,
                        help="每个类别最多保留多少组模式（默认不限）")
    args = parser.parse_args()

    config = load_config(REPO_ROOT)
    find_gaps(
        input_path=Path(args.input),
        output_dir=Path(args.output),
        config=config,
        min_freq=args.min_freq,
        top_n=args.top_n,
    )


if __name__ == "__main__":
    main()
