#!/usr/bin/env python3
"""
Search merchant_kb.csv for merchant names or keywords.

merchant_kb.csv is ~2.5M rows / 241MB. This script uses grep (subprocess)
for sub-second pre-filtering, then parses only matched lines.

Usage:
    python scripts/search_merchant.py --search "DORSETT GOLD COAST HOTEL" --diagnose
    python scripts/search_merchant.py --search "BETR" --diagnose
    python scripts/search_merchant.py --search "LAVERTON" --fuzzy --max-results 20
    python scripts/search_merchant.py --search "Naked for Satan" --field merchant_name
    python scripts/search_merchant.py --search "BETR" --json
"""

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

# Fix Windows console encoding for Chinese characters
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def grep_file(filepath: str, pattern: str, ignore_case: bool = True) -> list[str]:
    """Run grep on file and return matching lines."""
    # Escape special regex chars for literal search, but allow basic patterns
    # For safety, use fixed-string matching (-F)
    args = ["grep", "-F"]
    if ignore_case:
        args.append("-i")
    args.extend([pattern, filepath])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")
        elif result.returncode == 1:
            return []  # No matches
        else:
            print(f"  [WARN] grep error: {result.stderr}", file=sys.stderr)
            return []
    except subprocess.TimeoutExpired:
        print("  [WARN] grep timed out", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("  [WARN] grep not found, falling back to Python scan", file=sys.stderr)
        return None  # Signal to fall back


def parse_matched(lines: list[str], fieldnames: list[str] | None = None) -> list[dict]:
    """Parse grep-matched CSV lines into dicts."""
    if not lines:
        return []
    reader = csv.DictReader(lines, fieldnames=fieldnames)
    if fieldnames is None:
        # First row is header
        header = next(reader)
        fieldnames = list(header.keys())
        # Re-create reader with the data rows only (skip header)
        reader = csv.DictReader(lines[1:], fieldnames=fieldnames)

    rows = []
    for row in reader:
        rows.append(dict(row))
    return rows


def score_match(term: str, target: str) -> float:
    """Score how well term matches target."""
    term_u = term.upper().strip()
    target_u = target.upper().strip()

    if term_u == target_u:
        return 1.0
    if term_u in target_u:
        return 0.95
    if target_u in term_u:
        return 0.90
    return SequenceMatcher(None, term_u, target_u).ratio()


def search(
    filepath: str,
    term: str,
    field: str = "all",
    fuzzy: bool = False,
    max_results: int = 30,
    min_score: float = 0.6,
) -> tuple[list[dict], int, int]:
    """
    Search merchant_kb.csv using grep + parse.
    Returns (matching_rows, total_lines_approx, empty_category_count).
    """
    # Get approximate total lines for stats (fast: wc -l)
    total = 0
    try:
        wc = subprocess.run(
            ["wc", "-l", filepath],
            capture_output=True, text=True, timeout=10,
        )
        if wc.returncode == 0:
            total = int(wc.stdout.strip().split()[0]) - 1  # minus header
    except Exception:
        total = 2_500_000  # fallback estimate

    term_upper = term.upper().strip()

    # Strategy: grep for the term, then parse and score
    lines = grep_file(filepath, term)

    if lines is None:
        # grep not available — fall back to streaming Python scan
        return _fallback_scan(filepath, term, field, fuzzy, max_results, min_score)

    if not lines:
        return [], total, 0

    # Parse matched lines (first line is CSV header from grep output)
    # Re-read header from file
    with open(filepath, "r", encoding="utf-8-sig") as f:
        header_line = f.readline().strip()

    header = next(csv.reader(io.StringIO(header_line)))
    # Parse only matched lines (exclude header if grep included it)
    data_lines = [l for l in lines if not l.startswith("merchant_name,")]
    if not data_lines:
        return [], total, 0

    rows = parse_matched(data_lines, fieldnames=header)

    # Score and filter
    results = []
    empty_cat = 0
    for row in rows:
        merchant = (row.get("merchant_name") or "").upper()
        keywords = (row.get("keywords") or "").upper()
        category = (row.get("category") or "").strip()
        if not category:
            empty_cat += 1

        best_score = 0.0
        match_field = None

        if field in ("merchant_name", "all"):
            s = score_match(term, merchant)
            if s > best_score and s >= (0.0 if not fuzzy else min_score):
                best_score = s
                match_field = "merchant_name"

        if field in ("keywords", "all"):
            kw_list = [k.strip() for k in keywords.split("|")]
            for kw in kw_list:
                s = score_match(term, kw)
                if s > best_score and s >= (0.0 if not fuzzy else min_score):
                    best_score = s
                    match_field = "keywords"

        if not fuzzy and best_score >= 1.0:
            results.append({**row, "_match_field": match_field, "_score": best_score})
        elif fuzzy and match_field and best_score >= min_score:
            results.append({**row, "_match_field": match_field, "_score": round(best_score, 3)})

    results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return results[:max_results], total, empty_cat


def _fallback_scan(filepath, term, field, fuzzy, max_results, min_score):
    """Fallback: streaming Python scan when grep is unavailable."""
    term_upper = term.upper().strip()
    results = []
    total = 0
    empty_cat = 0

    with open(filepath, "r", encoding="utf-8-sig") as f:
        for row in reader:
            total += 1
            merchant = (row.get("merchant_name") or "").upper()
            keywords = (row.get("keywords") or "").upper()
            category = (row.get("category") or "").strip()
            if not category:
                empty_cat += 1

            best = 0.0
            mf = None

            if field in ("merchant_name", "all"):
                s = score_match(term, merchant)
                if s > best and s >= (0.0 if not fuzzy else min_score):
                    best, mf = s, "merchant_name"

            if field in ("keywords", "all"):
                for kw in [k.strip() for k in keywords.split("|")]:
                    s = score_match(term, kw)
                    if s > best and s >= (0.0 if not fuzzy else min_score):
                        best, mf = s, "keywords"

            if mf and best >= (1.0 if not fuzzy else min_score):
                results.append({**row, "_match_field": mf, "_score": round(best, 3)})

    results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return results[:max_results], total, empty_cat


def diagnose(filepath: str, term: str) -> dict:
    """Full diagnosis: search and generate recommendation."""
    term_upper = term.upper().strip()

    # Step 1: exact search
    exact_results, total, empty_cat = search(filepath, term, field="all", fuzzy=False, max_results=5)

    exact_merchant = [r for r in exact_results if r.get("_match_field") == "merchant_name"]
    exact_keyword = [r for r in exact_results if r.get("_match_field") == "keywords"]

    # Step 2: fuzzy only if no exact
    fuzzy_results = []
    if not exact_merchant and not exact_keyword:
        fuzzy_results, _, _ = search(filepath, term, field="all", fuzzy=True, max_results=20)

    diag = {
        "search_term": term,
        "total_merchants": total,
        "empty_category_count": empty_cat,
        "exact_merchant_match": len(exact_merchant) > 0,
        "exact_keyword_match": len(exact_keyword) > 0,
        "exact_matches": exact_merchant + exact_keyword,
        "fuzzy_matches": fuzzy_results,
    }

    # Recommendation
    if exact_merchant:
        m = exact_merchant[0]
        has_cat = bool((m.get("category") or "").strip())
        diag["recommendation"] = {
            "action": "merchant_exists_check_keywords",
            "merchant_name": m["merchant_name"],
            "has_category": has_cat,
            "category": m.get("category", ""),
            "keywords": m.get("keywords", ""),
            "category_source": m.get("category_source", ""),
            "note": (
                "商户存在且有分类。检查 transaction text 中的写法是否被 keywords 覆盖；"
                "若未覆盖则补充 keywords 变体。"
                if has_cat
                else "商户存在但 category 为空。根据 illion 标签和交易语义补充分类。"
            ),
        }
    elif exact_keyword:
        m = exact_keyword[0]
        has_cat = bool((m.get("category") or "").strip())
        diag["recommendation"] = {
            "action": "keyword_exists_check_why_not_matched",
            "merchant_name": m["merchant_name"],
            "has_category": has_cat,
            "category": m.get("category", ""),
            "keywords": m.get("keywords", ""),
            "note": (
                "Keyword 已存在于 merchant_kb。排查为何 Aho-Corasick 未匹配"
                "（text 中写法变体未被 keywords 覆盖？编码/截断？）。"
                if has_cat
                else "Keyword 存在但商户 category 为空，建议补充分类。"
            ),
        }
    elif fuzzy_results:
        top = fuzzy_results[0]
        s = top.get("_score", 0)
        diag["recommendation"] = {
            "action": "similar_merchants_review",
            "top_match": top["merchant_name"],
            "top_category": top.get("category", ""),
            "top_keywords": top.get("keywords", ""),
            "score": s,
            "total_similar": len(fuzzy_results),
            "note": (
                f"找到 {len(fuzzy_results)} 个相似商户（最佳 {s:.0%}）。"
                "判断是否为同一商户：是→补充 keywords，否→建议新增商户。"
            ),
        }
    else:
        diag["recommendation"] = {
            "action": "new_merchant",
            "note": (
                f"未找到匹配。这是一个新商户/品牌。建议：收集交易 text 中的常见写法"
                f"作为 keywords，确定 category，添加到 merchant_kb.csv。"
                f"（库中 {empty_cat:,} 个商户 category 为空可补全）"
            ),
        }

    return diag



def format_output(diag: dict, json_output: bool = False):
    """Print results (plain text for Windows console compatibility)."""
    if json_output:
        clean = {
            k: v for k, v in diag.items()
            if k not in ("exact_matches", "fuzzy_matches")
        }
        clean["exact_matches"] = [
            {kk: vv for kk, vv in m.items() if not kk.startswith("_")}
            for m in diag["exact_matches"]
        ]
        clean["fuzzy_matches"] = [
            {kk: vv for kk, vv in m.items() if not kk.startswith("_")}
            for m in diag["fuzzy_matches"]
        ]
        print(json.dumps(clean, ensure_ascii=False, indent=2))
        return

    term = diag["search_term"]
    rec = diag["recommendation"]

    print(f"\n{'='*70}")
    print(f"  merchant_kb search: \"{term}\"")
    print(f"{'='*70}")

    em = diag["exact_merchant_match"]
    ek = diag["exact_keyword_match"]

    if em:
        m = diag["exact_matches"][0]
        cat = m.get("category", "").strip() or "(empty)"
        print(f"\n  [OK] Exact merchant_name match")
        print(f"     {m['merchant_name']}")
        print(f"     category: {cat}  |  source: {m.get('category_source', '')}")
        kws = m.get("keywords", "")
        print(f"     keywords: {kws[:150]}{'...' if len(kws) > 150 else ''}")

    if ek:
        m = [x for x in diag["exact_matches"] if x.get("_match_field") == "keywords"]
        if m:
            m = m[0]
            cat = m.get("category", "").strip() or "(empty)"
            print(f"\n  [OK] Exact keyword match")
            print(f"     {m['merchant_name']}  ->  {cat}")
            kws = m.get("keywords", "")
            print(f"     keywords: {kws[:150]}{'...' if len(kws) > 150 else ''}")

    fuzzy = diag["fuzzy_matches"]
    if fuzzy and not em and not ek:
        print(f"\n  [search] Fuzzy matches ({len(fuzzy)}):")
        for i, m in enumerate(fuzzy[:15]):
            cat = m.get("category", "").strip() or "(empty)"
            s = m.get("_score", 0)
            print(f"     {i+1}. [{m.get('_match_field','?')}] {m['merchant_name']} -> {cat}  ({s:.0%})")

    if not em and not ek and not fuzzy:
        print(f"\n  [NO] No match -- likely a new merchant")

    print(f"\n  [info] [{rec['action']}]")
    print(f"     {rec['note']}")

    cat_pct = diag["empty_category_count"] / max(diag["total_merchants"], 1) * 100
    print(f"\n  [stats] DB: {diag['total_merchants']:,} merchants, "
          f"{diag['empty_category_count']:,} without category ({cat_pct:.1f}%)")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(
        description="搜索 merchant_kb.csv (~2.5M行)，使用 grep 加速",
    )
    parser.add_argument("--search", "-s", required=True, help="搜索词")
    parser.add_argument(
        "--field", "-f",
        choices=["merchant_name", "keywords", "all"],
        default="all",
    )
    parser.add_argument("--fuzzy", action="store_true", help="模糊匹配")
    parser.add_argument("--diagnose", "-d", action="store_true", help="完整诊断")
    parser.add_argument("--max-results", type=int, default=30)
    parser.add_argument("--merchant-file", help="merchant_kb.csv 路径")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    if args.merchant_file:
        merchant_file = args.merchant_file
    else:
        script_dir = Path(__file__).resolve().parent
        merchant_file = script_dir.parent / "raw" / "initial_rule" / "merchant_kb.csv"

    if not os.path.exists(merchant_file):
        print(f"[NO] 找不到文件: {merchant_file}", file=sys.stderr)
        sys.exit(1)

    if args.diagnose:
        diag = diagnose(str(merchant_file), args.search)
        format_output(diag, json_output=args.json)
    else:
        results, total, empty_cat = search(
            str(merchant_file), args.search,
            field=args.field, fuzzy=args.fuzzy, max_results=args.max_results,
        )
        if args.json:
            clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]
            print(json.dumps({"total": total, "empty_category": empty_cat, "matches": clean},
                             ensure_ascii=False, indent=2))
        else:
            if not results:
                print(f"\n[NO] 未找到 \"{args.search}\"")
                print(f"   提示: 使用 --fuzzy 或 --diagnose\n")
            else:
                print(f"\n[OK] {len(results)} 个匹配 (共 {total:,} 商户, {empty_cat:,} 缺分类):\n")
                for r in results:
                    cat = r.get("category", "").strip() or "(空)"
                    print(f"   [{r.get('_match_field','?')}] {r['merchant_name']} → {cat}")
                    kws = r.get("keywords", "")
                    print(f"   keywords: {kws[:120]}{'...' if len(kws) > 120 else ''}\n")


if __name__ == "__main__":
    main()
