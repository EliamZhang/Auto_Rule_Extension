"""
Validation layer for candidate rules.

Takes Claude-generated candidate rules, validates syntax, checks schema against
original rule files, detects overlaps with existing rules, and applies
engine-specific constraints derived from finv_category_V2 source code.

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


# =============================================================================
# Basic validators (syntax, schema, overlap, value)
# =============================================================================

def _validate_regex(pattern: str, flags: int = re.IGNORECASE) -> tuple[bool, str]:
    """Check if a regex pattern compiles. Returns (ok, error_message)."""
    try:
        re.compile(pattern, flags)
        return True, ""
    except re.error as e:
        return False, f"Regex compile error: {e}"


def _validate_csv_schema(
    candidate_df: pd.DataFrame,
    original_df: pd.DataFrame,
) -> tuple[bool, str]:
    """Check that candidate CSV columns match original rule CSV."""
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
    """Check if a new pattern overlaps with existing rules."""
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
) -> list[str]:
    """Validate data-type and value constraints for a candidate row."""
    issues = []
    if "match_type" in row.index:
        mt = str(row["match_type"]).strip().lower()
        if mt and mt not in ("keyword", "regex"):
            issues.append(f"VALUE: match_type '{mt}' is not 'keyword' or 'regex'")
    if "rule_type" in row.index:
        rt = str(row["rule_type"]).strip().lower()
        if rt and rt not in ("keyword", "regex"):
            issues.append(f"VALUE: rule_type '{rt}' is not 'keyword' or 'regex'")
    if "confidence" in row.index:
        try:
            conf = float(row["confidence"])
            if conf < 0 or conf > 1:
                issues.append(f"VALUE: confidence {conf} is not in range [0, 1]")
        except (ValueError, TypeError):
            pass
    return issues


# =============================================================================
# Engine-specific constraint validators
# =============================================================================
# Each returns list of (severity, message) tuples.
# Severity: ERROR = will not work, WARNING = may not work as expected,
#           INFO = best-practice suggestion.
# All constraints are derived from finv_category_V2 source code analysis.
# =============================================================================

def _check_transfer(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """transfer_engine: normalize_text() = .lower(), str.contains() no case flag.

    Source: transfer_engine/domain/classification.py
    """
    issues = []
    if match_type == "regex":
        upper_words = re.findall(r"\b[A-Z]{2,}\b", pattern)
        if upper_words:
            issues.append((
                "ERROR",
                f"transfer regex 含大写词 {upper_words}，引擎已将文本转小写，"
                "这些词永远不会匹配。请改为小写。"
            ))
        if re.search(r"[A-Z]", pattern):
            issues.append((
                "WARNING",
                "transfer regex: 文本已转小写，pattern 中的大写字母可能匹配不到。"
            ))
    if "dr_cr" in row.index:
        dr_cr = str(row.get("dr_cr", "")).strip().lower()
        if dr_cr and dr_cr not in ("debit", "credit"):
            issues.append((
                "ERROR",
                f"transfer dr_cr='{dr_cr}' 无效，必须是 'debit'、'credit' 或留空。"
            ))
    return issues


def _check_catch_all(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """catch_all_engine: clean_text() → uppercase [A-Z0-9 ] only.

    Source: catch_all_engine/engine.py, classification_core/text.py
    """
    issues = []
    if match_type == "keyword":
        if not pattern.isupper():
            issues.append((
                "WARNING",
                "catch_all keyword 建议全大写，引擎使用 clean_text() 转大写后匹配。"
            ))
        if re.search(r"[^A-Z0-9 ]", pattern):
            issues.append((
                "ERROR",
                f"catch_all keyword 含特殊字符，clean_text() 只保留 [A-Z0-9 ]。"
            ))
    if match_type == "regex" and re.search(r"[a-z]", pattern):
        issues.append((
            "WARNING",
            "catch_all regex: 引擎在 clean_text() 结果上匹配（全大写），"
            "小写字母可能匹配不到。"
        ))
    if "confidence" in row.index:
        try:
            conf = float(row["confidence"])
            if conf > 0.90:
                issues.append((
                    "WARNING",
                    f"catch_all confidence={conf} 偏高，现有规则范围 0.70-0.85。"
                ))
        except (ValueError, TypeError):
            pass
    return issues


def _cell(row: pd.Series, column: str) -> str:
    """取候选行某个单元格的文本。

    空 CSV 字段会被 pandas 读成 NaN，`str(NaN)` == "nan" 是个非空字符串 ——
    直接用 `str(row.get(...)).strip()` 判空永远判不出来，所以这里统一归一成空串。
    """
    if column not in row.index:
        return ""
    value = row.get(column)
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _check_institution_style(
    pattern: str,
    match_type: str,
    row: pd.Series,
    *,
    label: str,
    category: str,
    conf_min: float,
    conf_max: float,
    institution_count: int,
) -> list[tuple[str, str]]:
    """双层 institution 引擎（rent v2.0 / gambling v1.0）共用的校验。

    institution 层 (`source=institution`)：Aho-Corasick 全词匹配，**最长 keyword 胜出**。
        pattern 列是 `|` 分隔的 keyword 变体表，**match_type 被忽略**（引擎一律按
        literal keyword 处理）；confidence 列同样**不被读取**，引擎用代码常量
        `INSTITUTION_CONFIDENCE = 0.95`。counterparty 取该行的 counterparty 列，
        为空则回落到 rule_name。
    rule 层 (`source=rule` 或该列缺省)：clean_text() → keyword(全词) / regex，
        最高 confidence 胜出。
    两层的文本侧都是 `[A-Z0-9 ]`（institution 层额外剥离通道前缀）。

    Source: {rent,gambling}_engine/engine.py + classification_core/merchant_institution.py
    """
    issues = []

    source = _cell(row, "source").lower()
    is_institution = source == "institution"

    # 加载器静默丢弃 rule_name / category / pattern 任一为空的行（load_rules 开头
    # 的 continue），不报错——候选行缺字段时会无声消失。
    missing = [c for c in ("rule_name", "category", "pattern") if c in row.index and not _cell(row, c)]
    for column in missing:
        issues.append((
            "ERROR",
            f"{label} 行缺少 {column}，load_rules() 会静默丢弃整行（不报错）。"
        ))
    if "pattern" in missing:
        # pattern 为空时后面的字符集/match_type/confidence 检查都没有意义，
        # 只会基于 "nan" 这个字符串报出误导性的「含特殊字符」。
        return issues

    if is_institution:
        # institution 行的 pattern 是 `|` 分隔的变体表——`|` 是分隔符，不是关键词
        # 的一部分，因此字符集检查必须**逐变体**做，否则每条 institution 行都会
        # 被误报「含特殊字符」。
        keywords = [v.strip() for v in pattern.split("|") if v.strip()]
        if not keywords:
            issues.append((
                "ERROR",
                f"{label} institution 行的 pattern 拆分后没有任何 keyword 变体。"
            ))
        for kw in keywords:
            if not kw.isupper():
                issues.append((
                    "WARNING",
                    f"{label} institution keyword '{kw}' 建议全大写，引擎先 clean_text() 再匹配。"
                ))
            if re.search(r"[^A-Z0-9 ]", kw):
                issues.append((
                    "ERROR",
                    f"{label} institution keyword '{kw}' 含特殊字符，clean_text() 只保留 [A-Z0-9 ]。"
                ))

        # 引擎对 institution 行不看 match_type，pattern 一律按 `|` 分隔的 literal
        # keyword 变体处理。写 regex 会变成一条匹配不到正则原文的死规则。
        if match_type != "keyword":
            issues.append((
                "ERROR",
                f"{label} institution 行的 match_type='{match_type}' 无效：引擎只按 keyword 处理，"
                f"pattern 会被当成字面量插入自动机（regex 将永远匹配不到）。"
            ))
        if len(keywords) > 50:
            issues.append((
                "WARNING",
                f"{label} institution 行有 {len(keywords)} 个 keyword 变体，超过 "
                f"MAX_VARIANTS_PER_INSTITUTION=50，超出的会被静默截断。"
            ))
    else:
        if match_type == "keyword":
            if not pattern.isupper():
                issues.append((
                    "WARNING",
                    f"{label} keyword 建议全大写，引擎使用 clean_text() 转大写后匹配。"
                ))
            if re.search(r"[^A-Z0-9 ]", pattern):
                issues.append((
                    "ERROR",
                    f"{label} keyword 含特殊字符，clean_text() 只保留 [A-Z0-9 ]。"
                ))
        elif match_type == "regex" and re.search(r"[a-z]", pattern):
            issues.append((
                "WARNING",
                f"{label} regex: 引擎在 clean_text() 结果上匹配（全大写），小写字母可能匹配不到。"
            ))

    if "category" in row.index:
        cat = _cell(row, "category")
        if cat and cat != category:
            issues.append((
                "ERROR",
                f"{label} category='{cat}' 无效，必须是 '{category}'。"
            ))

    if "confidence" in row.index:
        try:
            conf = float(row["confidence"])
        except (ValueError, TypeError):
            conf = None
        if conf is not None:
            if is_institution:
                # 引擎不读该列，恒用常量 0.95；写别的值不会改变行为，但会误导读者。
                if abs(conf - 0.95) > 1e-9:
                    issues.append((
                        "WARNING",
                        f"{label} institution confidence={conf}，但引擎对该层忽略此列、"
                        f"恒用代码常量 0.95。现有 {institution_count:,} 条 institution 规则均写 0.95，"
                        f"建议照写以保持一致。"
                    ))
            elif conf > conf_max or conf < conf_min:
                issues.append((
                    "WARNING",
                    f"{label} confidence={conf} 超出 rule 层现有范围 {conf_min}-{conf_max}。"
                ))
    return issues


def _check_rent(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """rent_engine v2.0 —— 双层结构，详见 _check_institution_style。"""
    return _check_institution_style(
        pattern, match_type, row,
        label="rent", category="Rent",
        conf_min=0.75, conf_max=0.90, institution_count=21794,
    )


def _check_gambling(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """gambling_engine v1.0 —— 与 rent 同一套双层机制，但有两处行为差异：

    * institution 行是**从 merchant_kb.csv 搬出来的** Gambling 商户（2026-09-02），
      命中后还会让位给：fee/dishonour 的认领，或 initial 已用**不短于**本次命中的
      keyword 认领的行（复刻搬迁前 KB 自动机跨全类别按长度排名的结果）。
    * rule 层走 `exclude_prior_claimed`，**对所有更早引擎的认领让位**（rent 的
      rule 层则可以重新认领）—— 因此新增 rule 行在 transfer/initial/dishonour
      已认领的行上不会生效。
    """
    return _check_institution_style(
        pattern, match_type, row,
        label="gambling", category="Gambling",
        conf_min=0.70, conf_max=0.90, institution_count=1822,
    )


def _check_fee(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """fee_engine: normalize_text() preserves case, re.compile(pattern, re.IGNORECASE).

    Source: fee_engine/domain/classification.py
    """
    issues = []
    if not pattern:
        return issues
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        issues.append((
            "ERROR",
            f"fee regex 编译失败（引擎使用 re.compile(pattern, re.IGNORECASE)）: {e}"
        ))
        return issues
    if not pattern.startswith("^"):
        issues.append((
            "INFO",
            "fee regex 建议以 ^ 锚定文本起始位置，与现有规则风格一致。"
        ))
    if "category" in row.index:
        cat = str(row.get("category", "")).strip()
        if cat and cat not in ("fee", "Fees", "Overdrawn"):
            issues.append((
                "ERROR",
                f"fee category='{cat}' 无效，必须是 'fee' 或 'Overdrawn'。"
            ))
    if "priority" in row.index:
        try:
            int(row["priority"])
        except (ValueError, TypeError):
            issues.append(("ERROR", "fee priority 必须是整数。"))
    return issues


def _check_all_other_credit(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """all_other_credit_engine: only keyword rules are matched.

    Source: all_other_credit_engine/engine.py
    """
    issues = []
    rule_type = str(row.get("rule_type", "keyword")).strip().lower()
    if rule_type == "regex":
        issues.append((
            "ERROR",
            "all_other_credit 引擎仅实现 keyword 匹配，regex 规则会被加载但永远不会被匹配！"
            " 请改用 rule_type='keyword'。"
        ))
    return issues


def _check_dishonour(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """dishonour_engine: keyword(re.escape) OR regex + required_terms(AND).

    Source: dishonour_engine/engine.py, classification_core/rules.py
    """
    issues = []
    rule_type = str(row.get("rule_type", match_type)).strip().lower()
    if rule_type == "regex":
        required_terms = str(row.get("required_terms", "")).strip()
        if not required_terms:
            issues.append((
                "WARNING",
                "dishonour regex: required_terms 为空，建议至少提供一个 term 增加特异性。"
            ))
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                issues.append(("ERROR", f"dishonour regex 编译失败: {e}"))
    return issues


def _check_income(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """income_engine: clean_text_with_seams() → uppercase, regex only, grouped by pattern_group.

    Source: income_engine/domain/classification.py (2026-08 起预处理为 clean_text_with_seams)
    """
    issues = []
    VALID_GROUPS = {
        "strong_wage", "medium_income", "repeat_employer_like",
        "repeat_employer_like_exclusion", "salary_packaging", "centrelink",
        "self_employed_gig", "wage_advance", "return_like",
        "hard_negative", "soft_negative",
        # 2026-08 新增的 7 组（income_pattern_rules.csv 实际内容）
        "transfer_from", "pay_signal", "behavior_exclusion",
        "gig_exclusion_extra", "gig_family_exclusion",
        "gig_personal_transfer", "gig_personal_exclusion",
    }
    if "pattern_group" in row.index:
        group = str(row.get("pattern_group", "")).strip()
        if group and group not in VALID_GROUPS:
            issues.append((
                "ERROR",
                f"income pattern_group='{group}' 无效，有效值: {sorted(VALID_GROUPS)}。"
            ))
    if pattern:
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            issues.append(("ERROR", f"income regex 编译失败: {e}"))
        if re.search(r"[a-z]", pattern):
            issues.append((
                "WARNING",
                "income: 引擎在 clean_text_with_seams() 结果上匹配（全大写），小写字母可能匹配不到。"
            ))
    return issues


def _check_liability(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """liability_engine: multi-file engine with different schemas per target_file.

    Source: liability_engine/domain/counterparty.py
    """
    issues = []
    target_file = str(row.get("target_file", "")).strip()
    if "counterparty_keyword_rules" in target_file:
        if match_type == "keyword" and not pattern.isupper():
            issues.append((
                "WARNING",
                "liability counterparty: 引擎在 uppercase 文本上做边界匹配，"
                "keyword 建议全大写。"
            ))
        # loader 只读 `rule_type` 列，而本 CSV 的表头是 `match_type` —— 写
        # match_type=regex 的行会被当 keyword 处理（转大写 + re.escape），
        # 永远匹配不到。现有 CSV 里那条 `DT\.[A-Za-z0-9]+\s+Sunshine` 就是这样。
        if match_type == "regex":
            issues.append((
                "ERROR",
                "liability counterparty_keyword_rules.csv 没有 rule_type 列，"
                "regex 行会被当 keyword 处理成死规则。要加 regex 必须先给 CSV 加列。"
            ))
        for variant in str(pattern).split(";"):
            variant = variant.strip()
            if not variant:
                continue
            if variant[0].isdigit():
                issues.append((
                    "ERROR",
                    f"liability counterparty keyword 以数字开头（{variant!r}）："
                    f"边界是 (?<![A-Za-z])...(?!A-Za-z) 而非 \\b，数字可穿透，"
                    f"会误伤 '1{variant}' 这类文本。"
                ))
    elif "credit_card_rules" in target_file:
        if pattern:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                issues.append(("ERROR", f"liability credit_card regex 编译失败: {e}"))
    elif "home_loan_car_loan_rules" in target_file:
        if "match_scope" in row.index:
            ms = str(row.get("match_scope", "")).strip()
            if ms and ms not in ("text", "text_or_counterparty", "all"):
                issues.append(("ERROR", f"liability match_scope='{ms}' 无效。"))
    return issues


def _check_initial(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """initial_engine: clean_text() → uppercase [A-Z0-9 ], Aho-Corasick + whole-word.

    Source: initial_engine/domain/classification.py
    """
    issues = []
    if match_type == "keyword":
        if not pattern.isupper():
            issues.append((
                "WARNING",
                "initial keyword 建议全大写，引擎在 clean_text() 转大写后的文本上匹配。"
            ))
        if re.search(r"[^A-Z0-9 |]", pattern):
            issues.append((
                "WARNING",
                "initial keyword: 匹配文本只保留 [A-Z0-9 ]，特殊字符不会出现在文本中；"
                "且 keyword 加载时不再自动 clean_text（2026-08 起），必须预清洗成大写规范形式。"
            ))
    if "category" in row.index:
        if str(row.get("category", "")).strip() == "Financial Institutions":
            issues.append((
                "ERROR",
                "initial: category='Financial Institutions' 的行在加载时被直接丢弃，"
                "此规则永远不会生效。请改用 liability 引擎。"
            ))
    return issues


ENGINE_CONSTRAINT_VALIDATORS = {
    "transfer": _check_transfer,
    "catch_all": _check_catch_all,
    "fee": _check_fee,
    "rent": _check_rent,
    "gambling": _check_gambling,
    "all_other_credit": _check_all_other_credit,
    "dishonour": _check_dishonour,
    "income": _check_income,
    "liability": _check_liability,
    "initial": _check_initial,
}


def _apply_engine_constraints(
    engine_id: str,
    pattern: str,
    match_type: str,
    row: pd.Series,
    rule_name: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """Run engine-specific checks. Returns (issues, error_count, warning_count)."""
    validator = ENGINE_CONSTRAINT_VALIDATORS.get(engine_id)
    if validator is None:
        return [], 0, 0
    raw = validator(pattern, match_type, row)
    result = []
    err_count = 0
    warn_count = 0
    for sev, msg in raw:
        result.append({"severity": sev, "message": msg, "rule_name": rule_name})
        if sev == "ERROR":
            err_count += 1
        elif sev == "WARNING":
            warn_count += 1
    return result, err_count, warn_count


# =============================================================================
# Main validation entry point
# =============================================================================

def validate_candidates(
    review_dir: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Validate all candidate CSV files in the review directory."""
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
            "engine_constraint_errors": 0,
            "engine_constraint_warnings": 0,
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
            "engine_constraint_errors": [],
            "engine_constraint_warnings": [],
            "valid_candidates": [],
        }

        existing_rules_cache: dict[str, pd.DataFrame] = {}
        schema_errors_by_file: dict[str, str] = {}

        for rf in rule_files:
            rule_path = resolve_rule_path(rules_base, engine_id, eng_cfg, rf)
            if rule_path.exists():
                try:
                    existing_rules_cache[rf] = pd.read_csv(rule_path, encoding="utf-8-sig")
                    ok, err = _validate_csv_schema(candidates, existing_rules_cache[rf])
                    if not ok:
                        schema_errors_by_file[rf] = err
                except Exception as e:
                    log.warning("  Could not load %s: %s", rf, e)
            else:
                log.warning("  Rule file not found: %s", rule_path)

        if len(schema_errors_by_file) == len(rule_files) and rule_files:
            all_errs = "; ".join(f"{rf}: {err}" for rf, err in schema_errors_by_file.items())
            engine_report["schema_errors"].append(all_errs)
            log.warning("  Schema mismatch against all rule files")

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
                        "rule_name": rule_name, "pattern": pattern, "error": err,
                    })

            # 2. Pattern length
            if len(pattern) < 3:
                issues.append(f"SYNTAX: Pattern too short ({len(pattern)} chars)")

            # 3. Boundary check for regex
            if match_type == "regex" and pattern:
                if not (r"\b" in pattern or pattern.startswith("^") or pattern.endswith("$")):
                    issues.append("STYLE: Regex missing word boundaries or anchors")

            # 4. Determine target file
            target_file = None
            if "target_file" in row.index:
                tf = str(row["target_file"]).strip()
                if tf and tf.lower() != "nan" and tf in existing_rules_cache:
                    target_file = tf
            elif len(rule_files) == 1:
                target_file = rule_files[0]
            elif rule_files:
                target_file = rule_files[0]

            # 5. Overlap check
            if target_file and target_file in existing_rules_cache:
                cfg_for_file = large_files_cfg.get(target_file, {})
                if not cfg_for_file.get("skip_pattern_loading"):
                    overlaps = _check_overlaps(pattern, match_type, existing_rules_cache[target_file])
                    for ov in overlaps:
                        issues.append(f"OVERLAP: {ov}")
                        engine_report["overlap_warnings"].append({
                            "rule_name": rule_name, "pattern": pattern,
                            "target_file": target_file, "overlaps": [ov],
                        })

            # 6. Value constraints
            value_issues = _validate_value_constraints(row, rule_name)
            for vi in value_issues:
                issues.append(vi)
                engine_report["value_errors"].append({
                    "rule_name": rule_name, "pattern": pattern, "error": vi,
                })

            # 7. ⭐ Engine-specific constraint validation
            constr_issues, err_n, warn_n = _apply_engine_constraints(
                engine_id, pattern, match_type, row, rule_name,
            )
            for ci in constr_issues:
                sev = ci["severity"]
                msg = ci["message"]
                issues.append(f"ENGINE_{sev}: {msg}")
                if sev == "ERROR":
                    engine_report["engine_constraint_errors"].append({
                        "rule_name": rule_name, "pattern": pattern, "message": msg,
                    })
                else:
                    engine_report["engine_constraint_warnings"].append({
                        "rule_name": rule_name, "pattern": pattern,
                        "severity": sev, "message": msg,
                    })

            if issues:
                log.warning("  %s: %s", rule_name, ", ".join(issues))
            else:
                engine_report["valid_candidates"].append({
                    "rule_name": rule_name, "pattern": pattern,
                    "match_type": match_type, "target_file": target_file,
                })

        report["engines"][engine_id] = engine_report
        s = report["summary"]
        s["total_candidates"] += len(candidates)
        s["syntax_errors"] += len(engine_report["syntax_errors"])
        s["schema_errors"] += len(engine_report["schema_errors"])
        s["overlap_warnings"] += len(engine_report["overlap_warnings"])
        s["value_errors"] += len(engine_report["value_errors"])
        s["engine_constraint_errors"] += len(engine_report["engine_constraint_errors"])
        s["engine_constraint_warnings"] += len(engine_report["engine_constraint_warnings"])

    return report


# =============================================================================
# CLI
# =============================================================================

def main() -> None:
    setup_logging("validate")
    parser = argparse.ArgumentParser(
        description="Validate candidate rules: syntax, schema, overlap, and engine constraints."
    )
    parser.add_argument("--review_dir", required=True)
    parser.add_argument("--output")
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
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

    log.info("Summary: %s", json.dumps(report["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
