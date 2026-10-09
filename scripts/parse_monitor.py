"""
BSCAT 监控看板解析层（看板 → 可消费的数据文件）。

读取 ``input/bscat_monitor.html``（自包含单文件看板，载荷是内嵌的
``const DATA = {...}``），把它拆成三个下游可直接消费的文件：

- ``monitor_data.json``   载荷去掉样本的轻量版（todo / metrics / categories /
                          catDetails 的流向 / rulesTable / ...）—— 阶段二读它做靶向。
- ``monitor_samples.csv`` 全部内嵌样本拍平成逐笔交易表，列名对齐 finv 流水线报告的
                          ``transactions`` sheet 子集，可直接喂给 analyze_gaps.py /
                          baseline.py / test_rules.py（三者的 --input 都支持 .csv）。
                          ⚠️ 只含**差异行**（illion≠bscat 或一侧为空），不是全量交易。
- ``monitor_flows.csv``   流向汇总（category × type × from → to，带 count/amount/users），
                          是「流向有多少笔」的计数真值来源。

看板里两个概念：
- ``full=True`` 的类别：该类别的**全部**差异行都内嵌（样本=该流向全量）；
- ``full=False`` 的类别：每个流向只有前几条预览（样本=下界，不足以支撑频率结论）。
``detail_scope`` 列标记这一点。

用法：
    python scripts/parse_monitor.py \\
        --input input/bscat_monitor.html \\
        --output reviews/2026-10-09/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from common import (
    load_category_catalog,
    log,
    setup_logging,
)

# 看板里表示「该侧没有分类」的占位符（from=illion 侧，to=bscat 侧）
UNCLASSIFIED = "（未分类）"

# 载荷里必须存在的顶层字段；缺任何一个都说明看板生成器改版了，
# 宁可立刻报错也不要静默产出空候选。
REQUIRED_KEYS = ("dataDate", "todo", "categories", "catDetails", "metrics", "summary")

# 样本表列（对齐 finv 报告的 transactions 子集 + 看板诊断列）
SAMPLE_COLUMNS = [
    "text",
    "dr_cr",
    "amount",
    "date",
    "classification_status",
    "finv_category",
    "classification_engine",
    "category",
    "third_party",
    "counterparty",
    "uid",
    "flow_type",
    "flow_from",
    "flow_to",
    "detail_scope",
]

FLOW_COLUMNS = [
    "category",
    "type",
    "from",
    "to",
    "count",
    "amount",
    "users",
    "embedded_samples",
    "detail_scope",
]

_DR_MAP = {"支": "debit", "收": "credit"}


# ── 载荷提取 ─────────────────────────────────────────────────────────────────

def extract_payload(html: str) -> dict[str, Any]:
    """从看板 HTML 里提取 ``const DATA = {...}`` 的载荷。

    JSON 感知的花括号扫描，而不是正则：载荷内部含转义引号与嵌套对象，
    贪婪/懒惰正则都会在第一个 ``}`` 或字符串里的花括号上出错。
    """
    marker = "const DATA"
    marker_pos = html.find(marker)
    if marker_pos < 0:
        raise ValueError(f"看板里找不到 `{marker}`，文件可能不是 bscat_monitor 看板。")

    start = html.find("{", marker_pos)
    if start < 0:
        raise ValueError("找到 `const DATA` 但后面没有 `{`。")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(html[start : i + 1])

    raise ValueError("载荷的大括号没有闭合，文件可能被截断。")


def _validate(payload: dict[str, Any]) -> None:
    missing = [k for k in REQUIRED_KEYS if k not in payload]
    if missing:
        raise ValueError(
            f"载荷缺少顶层字段 {missing} —— 看板生成器可能改版了，"
            "请核对 input/bscat_monitor.html 的来源版本。"
        )
    if not isinstance(payload.get("todo", {}).get("items"), list):
        raise ValueError("载荷的 `todo.items` 不是列表 —— 看板生成器可能改版了。")
    if not isinstance(payload.get("catDetails"), dict):
        raise ValueError("载荷的 `catDetails` 不是对象 —— 看板生成器可能改版了。")


# ── 拍平 ─────────────────────────────────────────────────────────────────────

def _primary_engine(category: str, catalog: dict[str, Any]) -> str:
    """finv_category → 主引擎（近似）。

    差异行上我们不知道当初是哪个引擎认领的，只能用 category_catalog 的
    owner_engine_id 首项反推。baseline / test_rules 的 priority_conflict 判定
    因此是近似值（见 SKILL.md「关键语义降级」）。
    """
    return catalog.get("category_to_primary_engine", {}).get(category, "")


def flatten_samples(payload: dict[str, Any], catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """把 catDetails → types → flows → samples 拍平成逐笔交易行。"""
    rows: list[dict[str, Any]] = []

    for category, detail in payload["catDetails"].items():
        scope = "full" if detail.get("full") else "preview"
        for type_block in detail.get("types", []):
            flow_type = str(type_block.get("key", ""))
            for flow in type_block.get("flows", []):
                flow_from = str(flow.get("from", ""))
                flow_to = str(flow.get("to", ""))
                # to 是 bscat 侧结果；占位符 → 未分类
                finv_cat = "" if flow_to == UNCLASSIFIED else flow_to
                # from 是 illion 标签；占位符 → 无标签
                illion_cat = "" if flow_from == UNCLASSIFIED else flow_from
                status = "unclassified" if not finv_cat else "classified"
                engine = _primary_engine(finv_cat, catalog) if finv_cat else ""

                for sample in flow.get("samples", []):
                    rows.append({
                        "text": str(sample.get("text", "")),
                        "dr_cr": _DR_MAP.get(str(sample.get("dr", "")), str(sample.get("dr", ""))),
                        "amount": sample.get("amount", ""),
                        "date": str(sample.get("date", "")),
                        "classification_status": status,
                        "finv_category": finv_cat,
                        "classification_engine": engine,
                        "category": illion_cat,
                        "third_party": str(sample.get("tp", "")),
                        "counterparty": str(sample.get("cp", "")),
                        "uid": str(sample.get("uid", "")),
                        "flow_type": flow_type,
                        "flow_from": flow_from,
                        "flow_to": flow_to,
                        "detail_scope": scope,
                    })

    return rows


def flatten_flows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """流向汇总：一行 = 一个 (category, type, from→to) 流向。"""
    rows: list[dict[str, Any]] = []
    for category, detail in payload["catDetails"].items():
        scope = "full" if detail.get("full") else "preview"
        for type_block in detail.get("types", []):
            flow_type = str(type_block.get("key", ""))
            for flow in type_block.get("flows", []):
                rows.append({
                    "category": category,
                    "type": flow_type,
                    "from": str(flow.get("from", "")),
                    "to": str(flow.get("to", "")),
                    "count": flow.get("count", 0),
                    "amount": flow.get("amount", 0),
                    "users": flow.get("users", ""),
                    "embedded_samples": len(flow.get("samples", [])),
                    "detail_scope": scope,
                })
    return rows


def strip_samples(payload: dict[str, Any]) -> dict[str, Any]:
    """载荷去掉 samples 的轻量版（保留 flows，供阶段二读）。"""
    light = {k: v for k, v in payload.items() if k != "catDetails"}
    light["catDetails"] = {
        category: {
            **{k: v for k, v in detail.items() if k != "types"},
            "types": [
                {
                    **{k: v for k, v in type_block.items() if k != "flows"},
                    "flows": [
                        {k: v for k, v in flow.items() if k != "samples"}
                        for flow in type_block.get("flows", [])
                    ],
                }
                for type_block in detail.get("types", [])
            ],
        }
        for category, detail in payload["catDetails"].items()
    }
    return light


# ── 摘要打印 ─────────────────────────────────────────────────────────────────

def print_summary(payload: dict[str, Any], catalog: dict[str, Any], n_samples: int) -> None:
    """终端摘要：全局指标 + 待办 + 建议目标引擎。"""
    log.info("看板日期: %s（生成于 %s）", payload.get("dataDate"), payload.get("generatedAt"))
    log.info("总交易: %s | 差异: %s | 内嵌差异样本: %s",
             f"{payload.get('total', 0):,}", f"{payload.get('diffTotal', 0):,}", f"{n_samples:,}")

    metrics = payload.get("metrics", {})
    log.info(
        "全局: illion 覆盖 %.1f%% | bscat 覆盖 %.1f%% | 一致率 %.1f%% | 漏识别 %.1f%% | 多识别 %.1f%% | 未分类 %.1f%%",
        metrics.get("illion_coverage", {}).get("value", 0) * 100,
        metrics.get("bscat_coverage", {}).get("value", 0) * 100,
        metrics.get("global_agreement", {}).get("value", 0) * 100,
        metrics.get("miss", {}).get("value", 0) * 100,
        metrics.get("extra", {}).get("value", 0) * 100,
        metrics.get("unknown", {}).get("value", 0) * 100,
    )

    items = payload["todo"].get("items", [])
    if not items:
        log.info("待办: 无（看板本期没有触发任何规则）")
        return

    log.info("待办: %d 项", len(items))
    if items:
        log.info("  %-4s %-32s %-30s %s", "级别", "类别", "miss/diff/extra", "建议目标")
    for item in items:
        mix = item.get("type_mix", {})
        owners = catalog.get("categories", {}).get(item.get("name", ""), {}).get("owner_engine_id", "")
        hint = owners if mix.get("miss", 0) > 0 else f"（无 miss 桶，补规则不可解；owner={owners}）"
        log.info(
            "  %-4s %-32s %5d/%5d/%5d   %s",
            item.get("level", ""),
            item.get("name", ""),
            mix.get("miss", 0), mix.get("diff", 0), mix.get("extra", 0),
            hint,
        )


# ── 主逻辑 ───────────────────────────────────────────────────────────────────

def parse_monitor(input_path: Path, output_dir: Path, project_root: Path) -> dict[str, Any]:
    """解析看板 → monitor_data.json + monitor_samples.csv + monitor_flows.csv。"""
    import pandas as pd

    html = input_path.read_text(encoding="utf-8", errors="replace")
    payload = extract_payload(html)
    _validate(payload)

    catalog = load_category_catalog(project_root)
    samples = flatten_samples(payload, catalog)
    flows = flatten_flows(payload)

    output_dir.mkdir(parents=True, exist_ok=True)

    data_path = output_dir / "monitor_data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(
            {"input_file": str(input_path), **strip_samples(payload)},
            f, ensure_ascii=False, indent=2,
        )

    samples_path = output_dir / "monitor_samples.csv"
    pd.DataFrame(samples, columns=SAMPLE_COLUMNS).to_csv(
        samples_path, index=False, encoding="utf-8-sig",
    )

    flows_path = output_dir / "monitor_flows.csv"
    pd.DataFrame(flows, columns=FLOW_COLUMNS).to_csv(
        flows_path, index=False, encoding="utf-8-sig",
    )

    log.info("→ %s", data_path)
    log.info("→ %s（%s 行样本）", samples_path, f"{len(samples):,}")
    log.info("→ %s（%s 条流向）", flows_path, f"{len(flows):,}")
    log.info("  ⚠ 样本只覆盖**差异行**，不是全量交易；full=True 类别为该流向全量，否则仅为预览。")

    print_summary(payload, catalog, len(samples))
    return payload


def main() -> None:
    setup_logging("parse_monitor")
    parser = argparse.ArgumentParser(
        description="解析 BSCAT 监控看板（bscat_monitor.html），产出下游可消费的数据文件。"
    )
    parser.add_argument("--input", required=True, help="看板 HTML（input/bscat_monitor.html）")
    parser.add_argument("--output", required=True, help="输出目录（reviews/<date>/）")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    try:
        parse_monitor(Path(args.input), Path(args.output), project_root)
    except (ValueError, json.JSONDecodeError) as exc:
        log.error("解析失败: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
