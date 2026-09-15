#!/usr/bin/env python3
"""
将 merchant_kb_categorized.json 中的分类更新到 merchant_kb.csv。

用法:
    python update_category.py [--json <path>] [--kb <path>]

输入:
    - knowledge-base-classify/merchant_kb_categorized.json  (分类数据)
    - raw/initial_rule/merchant_kb.csv                     (待更新的 KB)

输出:
    - merchant_kb.csv  (原子地原地更新 category 列)
"""

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings import BASE_DIR, FINAL_OUTPUT

DEFAULT_JSON = BASE_DIR / "knowledge-base-classify" / "merchant_kb_categorized.json"

# KB 带 UTF-8 BOM；用 utf-8 读会把首列读成 '﻿merchant_name'。
ENCODING = "utf-8-sig"


def load_categorized(json_path: Path) -> dict[str, str]:
    """读取 JSON 分类文件，返回 {merchant_name: category} 映射。"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return {
        item["merchant_name"].strip(): item["category"].strip()
        for item in data
        if item.get("category", "").strip()
    }


def update_csv(csv_path: Path, cat_map: dict[str, str]) -> dict:
    """按 merchant_name 匹配并更新 CSV 的 category 列。"""
    with open(csv_path, "r", encoding=ENCODING, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if "category" not in fieldnames:
        raise ValueError(f"{csv_path} 缺少 category 列，实际列: {fieldnames}")

    updated = 0
    skipped = 0

    for row in rows:
        name = row["merchant_name"].strip()
        if name in cat_map:
            new_cat = cat_map[name]
            if row.get("category", "").strip() != new_cat:
                row["category"] = new_cat
                updated += 1
            else:
                skipped += 1

    # 原子替换：74MB 的 KB 写到一半崩溃会毁掉文件
    target = os.path.abspath(csv_path)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(target), prefix=".merchant_kb_", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding=ENCODING, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_path, target)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return {
        "total_csv": len(rows),
        "in_map": len(cat_map),
        "matched": updated + skipped,
        "updated": updated,
        "skipped_unchanged": skipped,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON,
                        help=f"分类 JSON 路径（默认 {DEFAULT_JSON}）")
    parser.add_argument("--kb", type=Path, default=FINAL_OUTPUT,
                        help=f"待更新的 KB 路径（默认 {FINAL_OUTPUT}）")
    args = parser.parse_args()

    if not args.json.exists():
        raise SystemExit(f"分类 JSON 不存在: {args.json}")
    if not args.kb.exists():
        raise SystemExit(f"KB 不存在: {args.kb}")

    cat_map = load_categorized(args.json)
    print(f"分类映射: {len(cat_map)} 条")

    result = update_csv(args.kb, cat_map)

    print(f"CSV 总行数: {result['total_csv']}")
    print(f"匹配到: {result['matched']} 行")
    print(f"已更新: {result['updated']} 行")
    print(f"未变化(跳过): {result['skipped_unchanged']} 行")


if __name__ == "__main__":
    main()
