#!/usr/bin/env python3
"""Extract empty-category merchants from merchant_kb.csv and split into 20 JSON files."""

import argparse
import csv
import json
import math
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings import BASE_DIR, FINAL_OUTPUT

# KB 里有单行 keywords 字段超过 csv 默认 128KB 上限的记录
# （如 "Australia Post" 的 5,486 个变体 → 137KB），不放宽会抛 _csv.Error。
csv.field_size_limit(10_000_000)

CSV_PATH = FINAL_OUTPUT
OUT_DIR = BASE_DIR / "knowledge-base-split"
NUM_PARTS = 20


def main():
    # 本脚本没有参数，但**必须**有 argparse：否则任何调用（包括 `--help`）都会直接
    # 执行——而 main() 第一步就是 shutil.rmtree(OUT_DIR)，误触即删目录。
    parser = argparse.ArgumentParser(
        description=f"从 {CSV_PATH.name} 提取空 category 商户，切成 {NUM_PARTS} 个 JSON "
                    f"输出到 {OUT_DIR.name}/（该目录会被清空重建）",
    )
    parser.parse_args()

    if not CSV_PATH.exists():
        print(f"Error: {CSV_PATH} not found")
        return

    # Clear output dir
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    # Extract empty-category merchants
    empty = []
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("category", "").strip():
                empty.append({
                    "merchant_name": row["merchant_name"],
                    "category": "",
                })

    total = len(empty)
    chunk = math.ceil(total / NUM_PARTS)
    print(f"Empty-category merchants: {total}")

    for i in range(NUM_PARTS):
        start = i * chunk
        end = min((i + 1) * chunk, total)
        part = empty[start:end]
        fname = f"merchant_kb_part_{(i + 1):02d}.json"
        fpath = OUT_DIR / fname
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(part, f, ensure_ascii=False)
        print(f"  {fname}: {len(part)} records")

    print("Done.")


if __name__ == "__main__":
    main()
