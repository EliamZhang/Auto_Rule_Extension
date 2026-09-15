"""Merge merchant_category_kb CSV files into merchant_kb.csv.

Matching by merchant_name (case/whitespace-insensitive):
  - New merchant → appended at end.
  - Existing merchant → keywords merged (dedup, new ones appended),
    category **overwritten** with the last source's value (later files win on conflict).
"""

import argparse
import csv
import os
import re
import sys
import tempfile
from pathlib import Path

# Raise Python's default 128 KiB CSV field-size cap so merchants with thousands
# of keyword variants (e.g. AUSTRALIA POST, ~146 KB) don't crash the reader.
csv.field_size_limit(2**31 - 1)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from settings import BASE_DIR, FINAL_OUTPUT

COLUMNS = ["merchant_name", "keywords", "category"]
KEYWORD_SEPARATOR = "|"


def norm(name: str) -> str:
    """Case- and whitespace-insensitive merchant name key."""
    return re.sub(r"\s+", " ", (name or "").strip()).casefold()


def split_keywords(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    return [kw for part in raw.split(KEYWORD_SEPARATOR) if (kw := part.strip())]


def load_sources(paths: list[Path]) -> dict[str, dict[str, str]]:
    """Load one or more source CSVs into a single dict.
    Later files overwrite earlier ones for the same merchant.
    """
    merged: dict[str, dict[str, str]] = {}
    for p in paths:
        count = 0
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                key = norm(row.get("merchant_name", ""))
                if key:
                    merged[key] = {
                        "merchant_name": (row.get("merchant_name") or "").strip(),
                        "keywords": (row.get("keywords") or "").strip(),
                        "category": (row.get("category") or "").strip(),
                    }
                    count += 1
        print(f"  [load] {p.name}: {count:,} rows")
    return merged


def merge(
    source_rows: dict[str, dict[str, str]],
    target_path: Path,
) -> dict[str, int]:
    if not source_rows:
        print("[merge] source is empty — nothing to do")
        return {"total": 0, "updated": 0, "inserted": 0}

    matched: set[str] = set()
    stats = {"total": len(source_rows), "updated": 0, "inserted": 0}
    tmp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", delete=False, dir=target_path.parent,
        ) as tmp:
            tmp_path = Path(tmp.name)
            writer = csv.DictWriter(tmp, fieldnames=COLUMNS)
            writer.writeheader()

            # ── Pass 1: walk target, merge matching rows ──
            with target_path.open("r", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    key = norm(row.get("merchant_name", ""))
                    src = source_rows.get(key)
                    if src:
                        matched.add(key)
                        # Merge keywords: keep existing order, append new ones at end
                        existing_kws = split_keywords(row.get("keywords", ""))
                        src_kws = split_keywords(src["keywords"])
                        seen = {norm(kw) for kw in existing_kws}
                        for kw in src_kws:
                            if norm(kw) not in seen:
                                existing_kws.append(kw)
                                seen.add(norm(kw))
                        row["keywords"] = KEYWORD_SEPARATOR.join(existing_kws)
                        # Overwrite category
                        row["category"] = src["category"]
                        stats["updated"] += 1
                    writer.writerow(row)

            # ── Pass 2: append unmatched source rows ──
            for key, src in source_rows.items():
                if key not in matched:
                    writer.writerow(src)
                    stats["inserted"] += 1

        # Atomic replace
        os.replace(tmp_path, target_path)
        tmp_path = None  # prevent cleanup
        return stats

    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge merchant_category_kb.csv → merchant_kb.csv",
    )
    parser.add_argument(
        "--source", type=Path, nargs="+",
        default=[BASE_DIR / "merchant_category_kb.csv"],
        help="Source CSV file(s) (default: merchant_category_kb.csv). "
             "Multiple files accepted; later files win on conflict.",
    )
    parser.add_argument(
        "--target", type=Path, default=FINAL_OUTPUT,
        help=f"Target KB CSV to merge into (default: {FINAL_OUTPUT})",
    )
    args = parser.parse_args()

    print(f"[merge] sources: {[p.name for p in args.source]}")
    print(f"[merge] target : {args.target}")

    source_rows = load_sources(args.source)
    stats = merge(source_rows, args.target)
    print(
        f"[merge] source rows: {stats['total']:,}  |  "
        f"updated: {stats['updated']:,}  |  "
        f"inserted: {stats['inserted']:,}"
    )


if __name__ == "__main__":
    main()
