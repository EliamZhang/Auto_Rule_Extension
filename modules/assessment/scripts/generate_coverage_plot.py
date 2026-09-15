"""生成「各类别覆盖率对比（illion vs finv）双子条图」预览 PNG（matplotlib），与图 2.1 同一视觉语言。

用法: python scripts/generate_coverage_plot.py --input reviews/<最新>/label_compare_report.xlsx \
      [--output output/coverage_gap_preview_b.png]
关键点:
  1. 每类别一行：上半条 = Illion 覆盖率（蓝）、下半条 = finv 覆盖率（砖红），两色并排直接对比
  2. 覆盖率 = 该侧识别交易数 / 全部交易（与底稿 illion覆盖率 / finv覆盖率 列一致）
  3. 组内按 finv 覆盖率降序（顶部 = 我方覆盖最大的类别）
  4. 分面布局与图 2.1 完全同构：4 板块、高度比按类别数、白底、微软雅黑
  5. 行内不标数值（双子条行高有限），数值见报告 2.3 表与附录
依赖: matplotlib
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端：独立脚本与 docx 集成共用
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from generate_docx_category_report import GROUP_OF, GROUP_ORDER, norm_cat, to_float  # noqa: E402

BASE_DIR = Path(__file__).resolve().parents[1]

# 双子条配色：finv = 砖红（matte 系品牌色）、Illion = 蓝；CVD ΔE 23.9 已验证全 PASS
COLOR_FINV = "#C9403A"    # finv（下半条）
COLOR_ILLION = "#2a78d6"  # Illion（上半条）

# 板块名英文化（与图 2.1 一致）
GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"
GRID = "#F2F2F2"
BAR_ALPHA = 0.92


def read_categories(path: Path) -> List[Dict[str, Any]]:
    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb["00_核心对比"]
    headers = [str(v or "") for v in next(ws.iter_rows(min_row=23, max_row=23, min_col=1, max_col=33, values_only=True))]
    items: List[Dict[str, Any]] = []
    for row in ws.iter_rows(min_row=24, max_row=59, min_col=1, max_col=33, values_only=True):
        category = norm_cat(row[0])
        if not category or not any(v not in (None, "") for v in row):
            continue
        item = {headers[i]: row[i] for i in range(len(headers))}
        item["category"] = category
        item["group"] = GROUP_OF.get(category, "其他")
        items.append(item)
    wb.close()
    return items


def render(path: Path, items: List[Dict[str, Any]], input_name: str = paths.REVIEW_REPORT_NAME) -> None:
    for fname in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(fname).exists():
            try:
                font_manager.fontManager.addfont(fname)
            except Exception:
                pass
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    groups: List[Dict[str, Any]] = []
    for group in GROUP_ORDER:
        rows = [it for it in items if it["group"] == group]
        for it in rows:
            it["_il"] = to_float(it.get("illion覆盖率")) * 100
            it["_fv"] = to_float(it.get("finv覆盖率")) * 100
        rows.sort(key=lambda r: -r["_fv"])  # 组内按 finv 覆盖率降序（顶部 = 我方覆盖最大）
        groups.append({"name": group, "rows": rows})

    max_x = max(max(r["_il"], r["_fv"]) for g in groups for r in g["rows"]) or 1.0
    xlim = max_x * 1.10
    step = 5.0 if xlim > 10 else (2.0 if xlim > 4 else 1.0)
    xmax_round = int(xlim // step * step) + step

    fig = plt.figure(figsize=(11, 13))
    fig.suptitle("Coverage by Category · Illion vs finv", fontsize=15, fontweight="bold", color=INK, x=0.5, y=0.978)
    fig.text(0.5, 0.953,
             "Each row: top bar = Illion coverage, bottom bar = finv coverage. "
             "Coverage = transactions tagged by each side / all transactions. Exact values in the report tables.",
             fontsize=9, color=INK_3, ha="center")

    heights = [len(g["rows"]) for g in groups]
    # hspace 不能放 GridSpec 上（matplotlib 3.11 会令 tight_layout 失效），tight_layout 后单独设置
    gs = fig.add_gridspec(len(groups), 1, height_ratios=heights)

    for gi, g in enumerate(groups):
        ax = fig.add_subplot(gs[gi])
        rows = g["rows"]
        n = len(rows)
        ys = list(range(n))
        # 每类别一行两根条：上半 = Illion（y + 0.25），下半 = finv（y - 0.25）
        ax.barh([y + 0.25 for y in ys], [r["_il"] for r in rows], height=0.5,
                color=COLOR_ILLION, alpha=BAR_ALPHA, zorder=3)
        ax.barh([y - 0.25 for y in ys], [r["_fv"] for r in rows], height=0.5,
                color=COLOR_FINV, alpha=BAR_ALPHA, zorder=3)
        ax.set_yticks(ys)
        ax.set_yticklabels([r["category"] for r in rows], fontsize=8, color=INK)

        ax.grid(axis="x", color=GRID, lw=0.5, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(GRID)
        ax.set_xlim(0, xmax_round)
        ax.set_ylim(n - 0.55, -0.45)
        ax.set_xticks([i * step for i in range(int(xmax_round // step) + 1)])
        ax.tick_params(axis="x", labelsize=9, colors=INK_3)
        ax.tick_params(axis="y", length=0)
        if gi < len(groups) - 1:
            ax.set_xticklabels([])

        gname = GROUP_EN.get(g["name"], g["name"])
        ax.set_title(f"{gname} · {len(rows)} categories", fontsize=12, color=INK, loc="left", pad=6)
        if gi == len(groups) - 1:
            ax.set_xlabel("Coverage (% of all transactions)", fontsize=10, color=INK_2, labelpad=3)

    fig.legend(
        handles=[Patch(facecolor=COLOR_ILLION, alpha=BAR_ALPHA, label="Illion coverage"),
                 Patch(facecolor=COLOR_FINV, alpha=BAR_ALPHA, label="finv coverage")],
        loc="upper right", frameon=False, fontsize=10)

    fig.text(0.5, 0.012,
             f"Source: {input_name} · 36 categories · "
             f"coverage range 0 to {max_x:.1f}%",
             fontsize=8.5, color=INK_3, ha="center")

    plt.tight_layout(rect=[0, 0.02, 1, 0.94])
    fig.subplots_adjust(hspace=0.38)  # 分面间距（tight_layout 之后再设，避免被覆盖）
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Generated: {path}")
    print(f"  Coverage range: 0 .. {max_x:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 各类别覆盖率对比（illion vs finv）双子条图")
    parser.add_argument("--input", type=Path, default=paths.default_review_report())
    parser.add_argument("--output", type=Path,
                        default=paths.report_output_dir() / "coverage_gap_preview_b.png")
    args = parser.parse_args()
    items = read_categories(args.input)
    if len(items) != 36:
        raise ValueError(f"类别数量异常: {len(items)} (期望 36)")
    render(args.output, items, input_name=args.input.name)


if __name__ == "__main__":
    main()
