"""
将 initial_keyword_updates.csv 中的新 keyword 变体追加到 merchant_kb.csv 已有商户。

用法：
    python scripts/apply_keyword_updates.py --review_dir reviews/2026-08-11_1642/
    python scripts/apply_keyword_updates.py --review_dir reviews/2026-08-11_1642/ --dry-run

要求：
    - initial_keyword_updates.csv 包含: merchant_name, new_keyword, status
    - status=confirmed 的条目才会被处理
    - 自动做 case-insensitive 商户名匹配
    - 自动去重（已存在的 keyword 不重复追加）
    - 自动备份（.bak 后缀）
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd


def main():
    parser = argparse.ArgumentParser(description="Apply keyword updates to merchant_kb.csv")
    parser.add_argument("--review_dir", required=True, help="Directory containing initial_keyword_updates.csv")
    parser.add_argument("--kb_path", default="raw/initial_rule/merchant_kb.csv", help="Path to merchant_kb.csv")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no changes")
    args = parser.parse_args()

    review_dir = Path(args.review_dir)
    kb_path = Path(args.kb_path)
    updates_file = review_dir / "initial_keyword_updates.csv"

    if not updates_file.exists():
        print(f"[SKIP] {updates_file} not found, nothing to do")
        return

    # Load updates
    updates = pd.read_csv(updates_file, dtype=str).fillna("")
    updates = updates[updates["status"].str.strip().str.lower() == "confirmed"]
    if updates.empty:
        print("[SKIP] No confirmed keyword updates")
        return

    print(f"Processing {len(updates)} confirmed keyword updates...")

    # Load KB
    kb = pd.read_csv(kb_path, dtype=str).fillna("")
    print(f"KB: {len(kb)} rows")

    # Backup
    if not args.dry_run:
        bak = str(kb_path) + ".keyword_bak"
        shutil.copy(kb_path, bak)
        print(f"Backup: {bak}")

    updated = 0
    skipped_existing = 0
    not_found = []

    for _, row in updates.iterrows():
        name_from_csv = str(row["merchant_name"]).strip()
        new_kw_raw = str(row["new_keyword"]).strip()

        # Case-insensitive match
        mask = kb["merchant_name"].str.strip().str.lower() == name_from_csv.lower()
        if not mask.any():
            # Try keyword-based fuzzy match
            found_idx = None
            for _, kb_row in kb.iterrows():
                kb_kws = str(kb_row["keywords"])
                for candidate in new_kw_raw.split(";"):
                    if candidate.strip() and candidate.strip() in kb_kws:
                        found_idx = kb_row.name
                        break
                if found_idx is not None:
                    break
            if found_idx is not None:
                mask = kb.index == found_idx
            else:
                not_found.append(name_from_csv)
                print(f"  [NOT FOUND] {name_from_csv}")
                continue

        idx = kb[mask].index[0]
        existing = str(kb.at[idx, "keywords"])
        new_keywords = [k.strip() for k in new_kw_raw.split(";") if k.strip()]
        added = []

        for kw in new_keywords:
            if kw not in existing.split("|"):
                existing = existing + "|" + kw if existing else kw
                added.append(kw)

        if added:
            if not args.dry_run:
                kb.at[idx, "keywords"] = existing
            updated += 1
            print(f"  [OK] {kb.at[idx, 'merchant_name']}: +{' | '.join(added)}")
        else:
            skipped_existing += 1
            print(f"  [SKIP] {kb.at[idx, 'merchant_name']}: keywords already present")

    # Save
    if not args.dry_run and updated > 0:
        kb.to_csv(kb_path, index=False, encoding="utf-8-sig")
        print(f"\nSaved: {updated} merchants updated in {kb_path}")

    print(f"\nSummary: updated={updated}, skipped(existing)={skipped_existing}, not_found={len(not_found)}")
    if not_found:
        print("Not found (check merchant_name spelling/case in CSV):")
        for m in not_found:
            print(f"  - {m}")

    if args.dry_run:
        print("[DRY-RUN] No changes made.")


if __name__ == "__main__":
    main()
