"""
候选规则测试脚本 — 在真实数据上测试规则表现。

读取用户确认后的候选规则和原始输入数据，对每条规则在未分类交易上
测试覆盖（gain），在已分类交易上检测冲突（conflict），提取样本
供人工抽查。

与 baseline.py 的区别：
- baseline.py 需要先 save 基线快照，侧重"影响面分析"
- test_rules.py 直接从 .xlsx 读取，更轻量，侧重"实际测试"

用法：
    python scripts/test_rules.py \
        --rules reviews/2026-08-10/confirmed_rules.json \
        --input input/report2.xlsx \
        --output reviews/2026-08-10/test_report.json
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
    get_engine_priority,
    log,
    setup_logging,
)


# ── 数据读取 ──────────────────────────────────────────────────────────────────

def _read_input(input_path: Path) -> pd.DataFrame:
    """读取交易数据（.xlsx 或 .csv），返回统一 DataFrame。

    复用 analyze_gaps.py 的读取模式。
    """
    suffix = input_path.suffix.lower()

    if suffix in (".xlsx", ".xlsm", ".xls"):
        xlsx = pd.ExcelFile(input_path)
        if "transactions" in xlsx.sheet_names:
            df = pd.read_excel(xlsx, sheet_name="transactions")
        else:
            df = pd.read_excel(xlsx, sheet_name=0)
        log.info("已加载 .xlsx: %s 行, sheets: %s", f"{len(df):,}", xlsx.sheet_names)
        return df

    if suffix == ".csv":
        df = pd.read_csv(input_path, encoding="utf-8-sig")
        log.info("已加载 .csv: %s 行", f"{len(df):,}")
        return df

    raise ValueError(f"不支持的文件格式: {suffix}。需要 .xlsx 或 .csv")


# ── 匹配引擎 ──────────────────────────────────────────────────────────────────

def _match_series(
    pattern: str,
    match_type: str,
    series: pd.Series,
) -> pd.Series:
    """向量化模式匹配（参考 baseline.py 实现）。

    使用 pandas str.contains，比逐行 Python 循环快几个数量级。
    """
    if not pattern or series.empty:
        return pd.Series([False] * len(series), index=series.index)

    if match_type == "regex":
        return series.str.contains(pattern, case=False, regex=True, na=False)
    else:
        return series.str.contains(pattern, case=False, regex=False, na=False)


def _validate_pattern(pattern: str, match_type: str) -> tuple[bool, str]:
    """验证模式是否合法。返回 (是否合法, 错误信息)。"""
    if not pattern or not pattern.strip():
        return False, "模式为空"
    if match_type == "regex":
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return False, f"正则编译错误: {e}"
    return True, ""


# ── 主逻辑 ────────────────────────────────────────────────────────────────────

def test_rules(
    rules_json_path: Path,
    input_path: Path,
    output_path: Path,
    config: dict[str, Any],
    max_samples: int = 10,
) -> dict[str, Any]:
    """对确认后的候选规则在输入数据上执行测试。

    Args:
        rules_json_path: 确认规则 JSON 文件路径
        input_path: 原始 .xlsx 输入数据
        output_path: 测试报告输出路径
        config: 项目配置
        max_samples: 每条规则最多提取的样本数

    Returns:
        测试报告字典
    """
    # ── 1. 加载规则 ──
    with open(rules_json_path, encoding="utf-8") as f:
        rules_data = json.load(f)

    # 支持两种格式：数组 或 带 confirmed/edited/rejected 的对象
    if isinstance(rules_data, list):
        rules = rules_data
    else:
        # 合并 confirmed + edited（使用编辑后的新值）
        rules = list(rules_data.get("confirmed", []))
        for edited_rule in rules_data.get("edited", []):
            rules.append(edited_rule)

    if not rules:
        log.warning("没有需要测试的规则（规则列表为空）")
        empty_report = {
            "summary": {
                "total_rules": 0,
                "total_gain": 0,
                "total_conflicts": 0,
                "conflict_rate": 0.0,
                "high_conflict_rules": [],
                "skipped_rules": 0,
            },
            "per_rule": [],
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(empty_report, f, ensure_ascii=False, indent=2)
        return empty_report

    log.info("加载了 %d 条候选规则", len(rules))

    # ── 2. 加载输入数据 ──
    df = _read_input(input_path)
    total = len(df)

    if "classification_status" not in df.columns:
        raise ValueError("输入必须包含 'classification_status' 列。请使用 finv_category_V2 流水线输出的 .xlsx 报告。")

    # 拆分已分类/未分类
    uncl_mask = df["classification_status"].str.strip().str.lower() == "unclassified"
    classified_mask = ~uncl_mask

    # 重置索引，确保 DataFrame 和后续向量化匹配的 Series 索引对齐
    uncl_df = df[uncl_mask].reset_index(drop=True)
    cl_df = df[classified_mask].reset_index(drop=True)

    log.info("总交易: %s | 已分类: %s | 未分类: %s",
             f"{total:,}", f"{classified_mask.sum():,}", f"{uncl_mask.sum():,}")

    # 准备向量化匹配所需 Series（索引与对应 DataFrame 一致）
    uncl_texts = pd.Series(uncl_df["text"].astype(str).values, dtype="string")
    cl_texts = pd.Series(cl_df["text"].astype(str).values, dtype="string")

    # ── 3. 逐规则测试 ──
    HIGH_CONFLICT_COUNT = 5       # 冲突超过此数 → 高风险
    HIGH_CONFLICT_RATE = 0.10     # 冲突率超过此比例 → 高风险

    per_rule_results: list[dict[str, Any]] = []
    total_gain = 0
    total_real_conflicts = 0
    skipped = 0
    high_conflict_rules: list[str] = []

    for idx, rule in enumerate(rules):
        pattern = str(rule.get("pattern", ""))
        match_type = str(rule.get("match_type", "keyword")).lower()
        rule_name = str(rule.get("rule_name", f"rule_{idx}"))
        engine = str(rule.get("engine", "unknown"))
        category = str(rule.get("category", ""))
        confidence = rule.get("confidence", None)

        # 验证模式
        is_valid, err_msg = _validate_pattern(pattern, match_type)
        if not is_valid:
            log.warning("  ⚠ 跳过 %s: %s", rule_name, err_msg)
            per_rule_results.append({
                "rule_name": rule_name,
                "engine": engine,
                "pattern": pattern,
                "match_type": match_type,
                "category": category,
                "confidence": confidence,
                "error": err_msg,
                "gain": {"count": 0, "samples": []},
                "conflict": {"count": 0, "real_conflict_count": 0, "details": []},
            })
            skipped += 1
            continue

        eng_priority = get_engine_priority(engine, config)

        # ── 3a. 增益测试（未分类交易） ──
        uncl_match_mask = _match_series(pattern, match_type, uncl_texts)
        gain_count = int(uncl_match_mask.sum())

        # 提取样本
        gain_sample_indices = uncl_match_mask[uncl_match_mask].index[:max_samples]
        gain_samples: list[str] = []
        for i in gain_sample_indices:
            text = str(uncl_df.loc[i, "text"]) if i in uncl_df.index else ""
            if text:
                gain_samples.append(text[:200])

        # ── 3b. 冲突测试（已分类交易） ──
        cl_match_mask = _match_series(pattern, match_type, cl_texts)
        conflict_count = int(cl_match_mask.sum())

        conflict_details: list[dict[str, Any]] = []
        real_conflict_count = 0

        if conflict_count > 0:
            matched_cl = cl_df[cl_match_mask]

            for _, row in matched_cl.iterrows():
                original_engine = str(row.get("classification_engine", "unknown"))
                original_category = str(row.get("finv_category", ""))
                original_priority = get_engine_priority(original_engine, config)

                # 真冲突：新规则的引擎优先级更高（数字更大 = 会覆盖）
                is_real_conflict = eng_priority > original_priority

                if is_real_conflict:
                    real_conflict_count += 1

                # 最多保留 20 条冲突详情
                if len(conflict_details) < 20:
                    conflict_details.append({
                        "text": str(row.get("text", ""))[:200],
                        "original_engine": original_engine,
                        "original_category": original_category,
                        "new_category": category,
                        "is_real_conflict": is_real_conflict,
                    })

        # ── 3c. 高风险判定 ──
        is_high_conflict = (
            real_conflict_count > HIGH_CONFLICT_COUNT
            or (gain_count > 0 and real_conflict_count / gain_count > HIGH_CONFLICT_RATE)
            or (gain_count == 0 and real_conflict_count > 0)  # 零增益但有冲突 → 高风险
        )
        if is_high_conflict:
            high_conflict_rules.append(rule_name)

        # ── 3d. 组装结果 ──
        rule_result = {
            "rule_name": rule_name,
            "engine": engine,
            "pattern": pattern,
            "match_type": match_type,
            "category": category,
            "confidence": confidence,
            "gain": {
                "count": gain_count,
                "samples": gain_samples,
            },
            "conflict": {
                "count": conflict_count,
                "real_conflict_count": real_conflict_count,
                "details": conflict_details,
            },
            "is_high_conflict": is_high_conflict,
        }
        per_rule_results.append(rule_result)

        total_gain += gain_count
        total_real_conflicts += real_conflict_count

        # 日志输出
        flag = " ⚠️高风险" if is_high_conflict else ""
        log.info("  %s: 增益=%d, 冲突=%d(实际%d)%s",
                 rule_name, gain_count, conflict_count, real_conflict_count, flag)

    # ── 4. 汇总 ──
    tested_rules = len(rules) - skipped
    if total_gain > 0:
        conflict_rate = total_real_conflicts / total_gain
    elif total_real_conflicts > 0:
        conflict_rate = 1.0  # 有冲突但零增益 → 标记为 100% 冲突率
    else:
        conflict_rate = 0.0

    report: dict[str, Any] = {
        "summary": {
            "total_rules": len(rules),
            "tested_rules": tested_rules,
            "skipped_rules": skipped,
            "total_gain": total_gain,
            "total_conflicts": total_real_conflicts,
            "conflict_rate": round(conflict_rate, 4),
            "high_conflict_rules": high_conflict_rules,
        },
        "per_rule": per_rule_results,
    }

    # ── 5. 写入输出 ──
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("→ 测试报告: %s", output_path)
    log.info("  测试规则: %d 条 (跳过 %d)", tested_rules, skipped)
    log.info("  新增覆盖: %s 笔", f"{total_gain:,}")
    log.info("  潜在冲突: %s 笔 (冲突率 %.2f%%)", f"{total_real_conflicts:,}", conflict_rate * 100)
    if high_conflict_rules:
        log.warning("  ⚠️ %d 条高风险规则: %s", len(high_conflict_rules), ", ".join(high_conflict_rules))

    return report


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging("test_rules")
    parser = argparse.ArgumentParser(
        description="在真实数据上测试候选规则，输出增益/冲突/样本报告。"
    )
    parser.add_argument(
        "--rules", required=True,
        help="确认后的规则 JSON 文件（数组或带有 confirmed/edited 字段的对象）。"
    )
    parser.add_argument(
        "--input", required=True,
        help="原始 .xlsx 交易分类报告。"
    )
    parser.add_argument(
        "--output", required=True,
        help="测试报告 JSON 输出路径。"
    )
    parser.add_argument(
        "--max-samples", type=int, default=10,
        help="每条规则最多提取的样本数（默认 10）。"
    )
    parser.add_argument(
        "--config", default=None,
        help="config.json 路径（默认自动检测）。"
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    config = load_config(project_root)

    test_rules(
        rules_json_path=Path(args.rules),
        input_path=Path(args.input),
        output_path=Path(args.output),
        config=config,
        max_samples=args.max_samples,
    )


if __name__ == "__main__":
    main()
