#!/usr/bin/env python3
"""Shared helpers for the liability_enrich pipeline.

Kept deliberately small and dependency-light: gap_source.py (Step 1) and
evidence.py (Step 3) must agree on what "a liability category" is and on
exactly how the liability engine matches a keyword. Any divergence here shows
up as candidates that look fine in evidence.py but never fire in production.

Two merchant files, two matching contracts
------------------------------------------
The engine loads merchant rules from two files that share nothing but the
engine they feed (``counterparty.py``):

``counterparty_keyword_rules.csv`` — ``load_rules`` / ``:193``
    ``keyword`` column, ``;``-separated plain-text variants, matched against
    whitespace-collapsed **uppercased** text with ``(?<![A-Za-z])…(?![A-Za-z])``
    (not ``\\b`` — digits pass through). Emits a specific counterparty name.

``home_loan_car_loan_rules.csv`` — ``_load_flag_rules_format_a`` / ``:340``
    Format A: ``pattern`` column holding a **regex** (or a ``;``-separated
    keyword list), matched against the **raw** ``text`` column with only
    ``re.IGNORECASE`` — no whitespace collapse, no uppercasing. Sets an
    ``is_home_loan`` / ``is_car_loan`` flag instead of naming a counterparty.

Mixing the two is the failure mode this module exists to prevent: a
counterparty-style keyword scored under Format A semantics (or vice versa)
looks fine on both sides and silently never fires. ``resolve_rule_file`` is
the single place that decides which contract a candidate is under.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from common import load_category_catalog  # noqa: E402

# Fallback if raw/category_catalog.json is missing. Kept in sync with it by hand
# only as a safety net — the catalog is the source of truth.
FALLBACK_LIABILITY_CATEGORIES = frozenset({
    "Non SACC Loans",
    "SACC Loans",
    "Unknown Loans",
    "Credit Card Repayments",
    "Debt Collection",
    "Debt Consolidation",
})

# Written by finv's generic-loan backstop (liability_engine/pipeline.py).
GENERIC_LOAN_COUNTERPARTY = "Generic Loans"

# The two merchant files candidates may target. Both feed liability_engine,
# but they match completely differently — see the module docstring.
COUNTERPARTY_RULE_FILE = "counterparty_keyword_rules.csv"
FLAG_RULE_FILE = "home_loan_car_loan_rules.csv"

# Candidates written before the two-file contract have no `target_file` cell
# (or a blank one); they were all counterparty rows.
DEFAULT_RULE_FILE = COUNTERPARTY_RULE_FILE

# Column holding the matchable pattern in each file.
PATTERN_COLUMNS = {
    COUNTERPARTY_RULE_FILE: "keyword",
    FLAG_RULE_FILE: "pattern",
}

# Column holding the merchant name in each file — what `is_new_merchant`
# is judged against, and what `search_merchant.py` searches.
NAME_COLUMNS = {
    COUNTERPARTY_RULE_FILE: "counterparty",
    FLAG_RULE_FILE: "rule_name",
}

# Format A vocabulary (`counterparty.py:340-421`). Anything outside these sets
# is silently dropped by the loader rather than reported.
FLAG_MATCH_SCOPES = ("text", "text_or_counterparty", "all")
FLAG_TARGET_FIELDS = ("is_home_loan", "is_car_loan")
FLAG_MATCH_TYPES = ("keyword", "regex", "always")


def primary_engine(owner_engine_id: Any) -> str:
    """First engine id in a comma-separated owner list, or '' if empty."""
    return str(owner_engine_id).split(",")[0].strip()


def liability_categories(project_root: Path | None = None) -> set[str]:
    """Loan-type categories whose *primary* owner is the liability engine.

    Primary means first in the catalog's ``owner_engine_id`` list. That
    deliberately excludes three categories liability can also emit:

    - ``Retail``   — ``initial,liability,catch_all``; only reached from
      ``special_rules.py``'s Cash Converters Retail correction. illion calling
      something Retail while finv didn't is not evidence of a missing lender.
    - ``Dishonours``  (``dishonour,liability``) — a failed payment, not a loan.
    - ``Overdrawn``   (``fee,liability``) — a fee, not a loan.

    A plain substring test on ``owner_engine_id`` pulls all three in and floods
    the illion-mismatch class with noise, so this asks the stricter question.
    """
    root = project_root or REPO_ROOT
    catalog = load_category_catalog(root)
    cats = {
        name
        for name, info in catalog.get("categories", {}).items()
        if primary_engine(info.get("owner_engine_id", "")) == "liability"
    }
    return cats or set(FALLBACK_LIABILITY_CATEGORIES)


def normalize_match_text(series: pd.Series) -> pd.Series:
    """Reproduce liability_engine's text normalization for a whole column.

    Mirrors ``counterparty.py``: collapse whitespace, strip, uppercase.
    Punctuation is preserved — this is *not* ``clean_text()``, so a keyword
    must be written exactly as it appears in the raw text.
    """
    return (
        series.fillna("").astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
        .str.upper()
    )


def keyword_pattern(keyword: str) -> str:
    """Build the exact regex liability_engine uses for one keyword.

    ``(?<![A-Za-z])`` / ``(?![A-Za-z])`` — note this is *not* ``\\b``. Digits
    pass straight through, so a keyword starting with a digit over-matches:
    "360 CASH LOANS" also matches inside "1360 CASH LOANS".
    """
    return r"(?<![A-Za-z])" + re.escape(keyword) + r"(?![A-Za-z])"


def split_variants(raw: str) -> list[str]:
    """Split a candidate's ``keyword`` cell into normalized variants.

    The engine splits on ``;`` (``split_upper_terms``) and uppercases each
    variant, so we must too — otherwise a lowercase candidate in the CSV looks
    like a miss here and still fires in production.
    """
    out: list[str] = []
    for part in str(raw or "").split(";"):
        part = re.sub(r"\s+", " ", part).strip().upper()
        if part:
            out.append(part)
    return out


def validate_keyword(keyword: str) -> str | None:
    """Return a human-readable problem with a keyword, or None if it is fine.

    Only the failure modes that make a rule silently dead or actively
    dangerous. Kept here (not in validate_candidates.py) because the skill
    needs to reject these *before* writing the candidate CSV.
    """
    if not keyword:
        return "keyword 为空"
    if "|" in keyword:
        return (f"含 '|'（{keyword!r}）—— liability 引擎只按 ';' 拆分"
                f"（counterparty.py 的 split_upper_terms），整格会被当成"
                f"一个含字面 '|' 的 keyword，永不命中。改用 ';' 分隔；"
                f"'|' 是 merchant_kb / rent / gambling institution 的分隔符，别混用")
    if keyword[0].isdigit():
        return (f"以数字开头（{keyword!r}）—— 边界是 (?<![A-Za-z]) 而非 \\b，"
                f"会误伤 '1{keyword}' 这类文本")
    if not re.search(r"[A-Z]", keyword):
        return f"不含任何字母（{keyword!r}）"
    return None


# ── target-file dispatch ─────────────────────────────────────────────────────

def resolve_rule_file(raw: Any) -> str:
    """Which merchant file a candidate targets.

    Blank (or a full path) is tolerated; an *unknown* name is not. Scoring a
    candidate with the wrong matching contract is silent on both sides — it
    looks healthy here and never fires in production — so this refuses to
    guess. `debt_collection_rules.csv` lands here too: it is a real merchant
    file, but outside this pipeline's contract (see the skill's 契约外路由).
    """
    name = Path(str(raw or "").strip()).name
    if not name:
        return DEFAULT_RULE_FILE
    if name in PATTERN_COLUMNS:
        return name
    raise ValueError(
        f"未知的 target_file: {name!r}。本 pipeline 只支持 "
        f"{' / '.join(PATTERN_COLUMNS)}。其它 liability 商户文件（如 "
        f"debt_collection_rules.csv）属契约外，需先与用户确认再扩展。"
    )


def rule_file_is_flag(rule_file: str) -> bool:
    """True when the target file uses Format A (flag) semantics."""
    return rule_file == FLAG_RULE_FILE


def raw_match_text(series: pd.Series) -> pd.Series:
    """Format A's text side: the raw ``text`` column, no normalization.

    Mirrors ``_apply_flag_rules`` (``counterparty.py:487``). Deliberately
    *not* ``normalize_match_text`` — Format A never collapses whitespace or
    uppercases, so a pattern with a literal space can miss on double-spaced
    text. That is a real failure mode, not a bug to paper over here.
    """
    return series.fillna("").astype(str)


def _strip_inline_flags(pattern: str) -> str:
    """Mirror of ``normalize_regex_pattern`` — the engine adds IGNORECASE itself."""
    pattern = str(pattern or "").strip()
    if pattern.startswith("(?i)"):
        return pattern[4:]
    if pattern.startswith("^(?i)"):
        return "^" + pattern[5:]
    return pattern


def flag_rule_regex(pattern: str, match_type: str, match_scope: str) -> str:
    """The effective regex liability_engine builds for one Format A row.

    Mirrors ``_load_flag_rules_format_a`` (``counterparty.py:340-421``):

    - ``regex`` (and anything non-``keyword`` under ``match_scope=all``): the
      pattern verbatim, compiled with IGNORECASE.
    - ``keyword``: escaped and wrapped as ``\\b(?:a|b)\\b``. The engine merges
      every keyword row of a target field into one such regex, so scoring one
      row alone under-counts slightly — merging can only *add* hits.
    - no pattern at all: the empty pattern, which ``re.search`` matches on
      everything. Only reachable via ``match_scope=all``.
    """
    pattern = str(pattern or "").strip()
    match_type = str(match_type or "").strip().lower() or "keyword"
    match_scope = str(match_scope or "").strip().lower() or "text"

    if match_scope == "all":
        # `keyword` here is the landmine validate_flag_rule rejects: the loader
        # stores the terms in `keywords`, and `_apply_flag_rules`'s all_rules
        # branch only ever looks for `pattern`. Returning "" makes the mis-scored
        # row match everything, which is what production would actually do.
        return "" if match_type == "keyword" else _strip_inline_flags(pattern)

    if match_type == "regex":
        return _strip_inline_flags(pattern)

    keywords = split_variants(pattern)
    if not keywords:
        return ""
    return r"\b(?:" + "|".join(re.escape(k) for k in sorted(set(keywords))) + r")\b"


def flag_scope_mask(
    text: pd.Series, counterparty: pd.Series, scope: str, regex: str
) -> pd.Series:
    """Rows one Format A rule would flag, honouring ``match_scope``.

    ``text`` / ``text_or_counterparty`` / ``all`` — the latter is the text part
    only; its account_type/dr_cr/bank/amount_gt conditions live in
    :func:`flag_condition_mask`.
    """
    if not regex:
        return pd.Series(True, index=text.index)
    hit = text.str.contains(regex, na=False, regex=True, flags=re.IGNORECASE)
    if str(scope or "").strip().lower() != "text_or_counterparty":
        return hit
    hit_cp = counterparty.str.contains(regex, na=False, regex=True, flags=re.IGNORECASE)
    return hit | hit_cp


def flag_condition_mask(df: pd.DataFrame, row: dict[str, Any]) -> tuple[pd.Series, list[str]]:
    """Rows satisfying a Format A row's non-text conditions.

    Returns ``(mask, problems)``. A condition whose column is missing from the
    report cannot be replayed — that is reported rather than silently treated
    as satisfied, which would over-state the rule's reach.
    """
    mask = pd.Series(True, index=df.index)
    problems: list[str] = []

    for field in ("account_type", "dr_cr", "bank"):
        want = str(row.get(field, "*") or "*").strip().lower()
        if want == "*":
            continue
        if field not in df.columns:
            problems.append(f"报告缺 '{field}' 列，无法复刻该条件（规则要求 {want!r}）")
            continue
        col = df[field].fillna("").astype(str).str.strip().str.lower()
        mask &= col.eq(want)

    raw_amount_gt = str(row.get("amount_gt", "") or "").strip()
    if raw_amount_gt:
        if "amount" not in df.columns:
            problems.append("报告缺 'amount' 列，无法复刻 amount_gt")
        else:
            try:
                threshold = float(raw_amount_gt)
            except ValueError:
                problems.append(f"amount_gt 不是数字（{raw_amount_gt!r}）")
            else:
                mask &= pd.to_numeric(df["amount"], errors="coerce").abs().gt(threshold)

    return mask, problems


def validate_flag_rule(
    *,
    pattern: Any,
    match_type: Any,
    match_scope: Any,
    target_field: Any,
    enabled: Any,
    has_conditions: bool,
) -> str | None:
    """Return a human-readable problem with a Format A row, or None if it is fine.

    Only the failure modes ``_load_flag_rules_format_a`` swallows. Every one
    of them is silent in production: the row is loaded, indexed, and then
    either never fires or — worse — fires on everything.
    """
    pattern = str(pattern or "").strip()
    match_type = str(match_type or "").strip().lower() or "keyword"
    match_scope = str(match_scope or "").strip().lower() or "text"
    target_field = str(target_field or "").strip()
    enabled = "1" if enabled is None else (str(enabled).strip() or "1")

    if enabled == "0":
        return "enabled=0 —— loader 直接跳过这一行（counterparty.py:355），规则不加载"
    if target_field not in FLAG_TARGET_FIELDS:
        return (f"target_field={target_field!r} 不是 {'/'.join(FLAG_TARGET_FIELDS)} 之一 —— "
                f"标志会写进一个下游不读的列，等于死规则")
    if match_scope not in FLAG_MATCH_SCOPES:
        return (f"match_scope={match_scope!r} 不在 {'/'.join(FLAG_MATCH_SCOPES)} 中 —— "
                f"bucket_key 拼不出来，loader 静默丢弃该行（counterparty.py:383）")

    if match_scope == "all":
        if match_type == "keyword" and pattern:
            return ("match_scope=all 配 match_type=keyword —— loader 把 pattern 存进 "
                    "'keywords'，但 _apply_flag_rules 的 all_rules 分支只找 'pattern'，"
                    "文本条件被整段忽略，规则会命中所有满足 account_type/dr_cr/bank/"
                    "amount_gt 的行。要么改 match_type=regex，要么把 scope 换成 "
                    "text / text_or_counterparty")
        if not pattern:
            if match_type != "always":
                return (f"match_scope=all 且无 pattern 时 match_type 只能是 'always'"
                        f"（当前 {match_type!r}）—— loader 会去编译空 pattern，行为等同通配")
            if not has_conditions:
                return ("纯条件规则（match_scope=all 且无 pattern）且 account_type/dr_cr/bank "
                        "全为 '*'、amount_gt 为空 —— 会把整张表标成 is_home_loan/is_car_loan。"
                        "库里 HL009 是人工手写的既有规则，不是可照抄的候选形态")
        elif match_type == "always":
            return ("match_type=always 带上了 pattern —— 语义矛盾：'always' 的意思是"
                    "不加文本条件，loader 会把它当 regex 编译（counterparty.py:374）")
        if pattern:
            try:
                re.compile(_strip_inline_flags(pattern), re.IGNORECASE)
            except re.error as exc:
                return f"regex 编译失败（{pattern!r}）：{exc} —— loader 跳过该行（:378）"
        return None

    # ── match_scope is text / text_or_counterparty ──
    if not pattern:
        return "pattern 为空且 match_scope != 'all' —— loader 跳过该行（counterparty.py:357）"
    if match_type == "always":
        return ("match_type=always 却限定了 match_scope=" + match_scope + " —— "
                "'always' 只在 match_scope=all 时成立；这里 loader 会按 keyword 处理"
                "（counterparty.py:391-395），实际是在文本里找字面量")
    if match_type == "regex":
        try:
            re.compile(_strip_inline_flags(pattern), re.IGNORECASE)
        except re.error as exc:
            return f"regex 编译失败（{pattern!r}）：{exc} —— loader 跳过该行（:389）"
        return None
    if match_type != "keyword":
        return (f"match_type={match_type!r} 不是 {'/'.join(FLAG_MATCH_TYPES)} 之一 —— "
                f"loader 只认 'regex'，其余一律按 keyword 处理（counterparty.py:391）")
    keywords = split_variants(pattern)
    if not keywords:
        return f"pattern 拆不出任何 keyword（{pattern!r}）—— loader 跳过该行（:393）"
    if pattern[0].isdigit():
        return (f"keyword 以数字开头（{pattern!r}）—— keyword 走 \\b(?:…)\\b，"
                f"数字不穿透，'1{pattern}' 反而匹配不到（与 counterparty 文件的 "
                f"(?<![A-Za-z]) 边界相反，别照搬那边的结论）")
    return None
