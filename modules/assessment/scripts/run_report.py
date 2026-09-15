#!/usr/bin/env python3
"""modules/assessment 的唯一入口 —— 一次产出全套分类性能报告。

用法:
    python modules/assessment/scripts/run_report.py
    python modules/assessment/scripts/run_report.py --input <xlsx> --out-dir <dir>
    python modules/assessment/scripts/run_report.py --with-charts
    python modules/assessment/scripts/run_report.py --only md_zh --only docx

输入:
    reviews/<YYYY-MM-DD_HHMM>/label_compare_report.xlsx
    （由 ARE 的 scripts/label_compare.py 产出；--input 可覆盖）

输出:
    reports/<YYYY-MM-DD_HHMM>/（--out-dir 可覆盖）

设计约定（见 docs/superpowers/specs/2026-09-14-monorepo-merge-design.md §5.1）:

- 以**子进程**调用各生成器，而非 import。这些脚本有大量模块级常量与全局状态，
  同进程内多次调用会互相污染；子进程还能把单个生成器的崩溃隔离在自身。
- **单个生成器失败不阻断其余生成器**，最后汇总打印成功/失败清单。
- 输入目录必须存在且含 label_compare_report.xlsx，否则直接报错退出，不猜。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402

# (名称, 脚本名, 输出文件名列表, 是否属于图表组)
# 顺序即执行顺序：先出纯文本报告（快，便于尽早发现问题），再出 docx / pdf 与图表。
GENERATORS = (
    ("md_zh", "generate_md_category_report.py",
     ("category_difference_report_v2.md",), False),
    ("md_en", "generate_md_category_report_en.py",
     ("category_difference_report_v2_en.md",), False),
    ("docx", "generate_docx_category_report.py",
     ("category_difference_report_100_zh.docx",), False),
    ("pdf", "generate_full_category_report.py",
     ("full_category_performance_report_zh.pdf",), False),
    ("chart_dumbbell", "generate_coverage_dumbbell.py",
     ("coverage_dumbbell_preview.png",), True),
    ("chart_heatmap", "generate_coverage_heatmap.py",
     ("coverage_heatmap_preview.png",), True),
    ("chart_plot", "generate_coverage_plot.py",
     ("coverage_gap_preview_b.png",), True),
    ("chart_dotplot", "generate_dotplot_preview.py",
     ("dotplot_preview_a.png", "dotplot_preview_b.png"), True),
)

SCRIPTS_DIR = Path(__file__).resolve().parent


def build_command(script: str, input_path: Path, out_dir: Path, names: tuple[str, ...]) -> list[str]:
    command = [sys.executable, str(SCRIPTS_DIR / script), "--input", str(input_path)]
    for index, name in enumerate(names):
        # 第 1 个用 --output，第 2 个用 --output-b（generate_dotplot_preview.py 的约定）
        flag = "--output" if index == 0 else f"--output-{chr(ord('a') + index)}"
        command += [flag, str(out_dir / name)]
    return command


def main() -> int:
    parser = argparse.ArgumentParser(
        description="一次产出全套 BS-CAT 分类性能报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", type=Path, default=None,
                        help="数据底稿 xlsx（默认取 reviews/ 下最新一次的 label_compare_report.xlsx）")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="输出目录（默认 reports/<当前时间戳>/）")
    parser.add_argument("--with-charts", action="store_true",
                        help="同时生成 4 张图表预览（需要 matplotlib / seaborn）")
    parser.add_argument("--only", action="append", default=[], metavar="NAME",
                        help=f"只跑指定生成器，可重复。可选: {', '.join(g[0] for g in GENERATORS)}")
    args = parser.parse_args()

    input_path = args.input or paths.default_review_report()
    if not input_path.exists():
        print(f"[ERR] 输入底稿不存在: {input_path}", file=sys.stderr)
        if args.input is None:
            latest = paths.latest_review_dir()
            if latest is None:
                print(f"      reviews/ 下没有任何审核目录（{paths.REVIEWS_DIR}）。"
                      f" 请先跑 ARE 的 scripts/label_compare.py，或用 --input 指定底稿。",
                      file=sys.stderr)
            else:
                print(f"      reviews/ 最新的审核目录是 {latest.name}，但它及更早的目录里都没有"
                      f" {paths.REVIEW_REPORT_NAME}。", file=sys.stderr)
        return 2

    out_dir = args.out_dir or paths.report_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    selected = [g for g in GENERATORS if not args.only or g[0] in args.only]
    if args.only:
        unknown = set(args.only) - {g[0] for g in GENERATORS}
        if unknown:
            print(f"[ERR] 未知生成器: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
    if not args.with_charts and not args.only:
        selected = [g for g in selected if not g[3]]

    print(f"[run] 输入  : {input_path}")
    print(f"[run] 输出  : {out_dir}")
    print(f"[run] 生成器: {', '.join(g[0] for g in selected)}")
    print()

    results: list[tuple[str, bool, float, str]] = []
    for name, script, names, _is_chart in selected:
        command = build_command(script, input_path, out_dir, names)
        print(f"[{name}] 开始 …", flush=True)
        started = time.time()
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
        elapsed = time.time() - started
        ok = completed.returncode == 0
        results.append((name, ok, elapsed, "" if ok else (completed.stderr or "").strip()))
        print(f"[{name}] {'完成' if ok else '失败'} ({elapsed:.1f}s)", flush=True)
        if not ok:
            tail = "\n".join((completed.stderr or "").strip().splitlines()[-3:])
            if tail:
                print(f"         {tail}", flush=True)

    succeeded = [r for r in results if r[1]]
    failed = [r for r in results if not r[1]]

    print()
    print("=" * 60)
    print(f"成功 {len(succeeded)}/{len(results)}")
    for name, _ok, elapsed, _err in succeeded:
        print(f"  [OK]   {name:<16} {elapsed:.1f}s")
    for name, _ok, elapsed, err in failed:
        print(f"  [FAIL] {name:<16} {elapsed:.1f}s")
        if err:
            print(f"         {err.splitlines()[-1]}")
    if failed:
        print()
        print("提示: 图表类生成器需要 matplotlib / seaborn:"
              " python -m pip install -r modules/assessment/requirements.txt")
    print(f"产物目录: {out_dir}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
