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


def _check_rent(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """rent_engine: clean_text() → uppercase [A-Z0-9 ], keyword(全词) / regex, 最高 confidence 胜出.

    Source: rent_engine/engine.py
    """
    issues = []
    if match_type == "keyword":
        if not pattern.isupper():
            issues.append((
                "WARNING",
                "rent keyword 建议全大写，引擎使用 clean_text() 转大写后匹配。"
            ))
        if re.search(r"[^A-Z0-9 ]", pattern):
            issues.append((
                "ERROR",
                f"rent keyword 含特殊字符，clean_text() 只保留 [A-Z0-9 ]。"
            ))
    if match_type == "regex" and re.search(r"[a-z]", pattern):
        issues.append((
            "WARNING",
            "rent regex: 引擎在 clean_text() 结果上匹配（全大写），小写字母可能匹配不到。"
        ))
    if "category" in row.index:
        cat = str(row.get("category", "")).strip()
        if cat and cat != "Rent":
            issues.append((
                "ERROR",
                f"rent category='{cat}' 无效，必须是 'Rent'。"
            ))
    if "confidence" in row.index:
        try:
            conf = float(row["confidence"])
            if conf > 0.90:
                issues.append((
                    "WARNING",
                    f"rent confidence={conf} 偏高，现有规则范围约 0.70-0.90。"
                ))
        except (ValueError, TypeError):
            pass
    return issues


def _check_fee(pattern: str, match_type: str, row: pd.Series) -> list[tuple[str, str]]:
    """fee_engine: normalize_text() preserves case, re.compile() no flags.

    Source: fee_engine/domain/classification.py
    """
    issues = []
    if not pattern:
        return issues
    try:
        re.compile(pattern)
    except re.error as e:
        issues.append((
            "ERROR",
            f"fee regex 编译失败（引擎使用 re.compile(pattern) 无 flags）: {e}"
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
    """income_engine: clean_text() → uppercase, regex only, grouped by pattern_group.

    Source: income_engine/domain/classification.py
    """
    issues = []
    VALID_GROUPS = {
        "strong_wage", "medium_income", "repeat_employer_like",
        "repeat_employer_like_exclusion", "salary_packaging", "centrelink",
        "self_employed_gig", "wage_advance", "return_like",
        "hard_negative", "soft_negative",
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
                "income: 引擎在 clean_text() 结果上匹配（全大写），小写字母可能匹配不到。"
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
                "liability counterparty: 引擎在 uppercase 文本上做 \\b 全词匹配，"
                "keyword 建议全大写。"
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
                "initial keyword 建议全大写，引擎使用 clean_text() 转大写后匹配。"
            ))
        if re.search(r"[^A-Z0-9 |]", pattern):
            issues.append((
                "WARNING",
                "initial keyword: clean_text() 只保留 [A-Z0-9 ]，特殊字符会被移除。"
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
