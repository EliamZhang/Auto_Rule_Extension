"""生成「各类别覆盖率哑铃图（Illion vs finv）」按业务板块分面（Dumbbell Chart）。

用法: python scripts/generate_coverage_dumbbell.py --input reviews/<最新>/label_compare_report.xlsx \
      [--output output/coverage_dumbbell_preview.png]
设计规范:
  1. 四个业务板块纵向分面，面板高度按类别数分配；横轴为对数刻度 0.1%–30%（覆盖率右偏，线性下
     34/36 类别挤在 0–4%，log 拉开小类别间距，视觉长度反映倍率差异）
  2. 每类别一行：连线 + 左端点 Illion（深灰实心）/ 右端点 finv（蓝实心）
  3. 纵轴按差异绝对值降序（最大在上）；板块内 |diff| 前 3 的类别名加星号加粗
  4. diff = finv覆盖率 − Illion覆盖率（百分点），|diff| ≥ 0.5 时在行尾固定位置标注（+1.50 / −7.01）
  5. Illion 覆盖率为 0 的类别：左端（0.1% 处）空心圆 + 注释
  6. 重点类别（External Transfers / Wages / All Other Credits / Unknown Loans）行背景浅金高亮
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
from matplotlib.lines import Line2D
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from generate_docx_category_report import GROUP_OF, GROUP_ORDER, norm_cat, to_float  # noqa: E402

BASE_DIR = Path(__file__).resolve().parents[1]

# 端点配色（按规范）：Illion 深灰 / finv 蓝 / 连线浅灰
COLOR_ILLION = "#555555"
COLOR_FINV = "#1f77b4"
LINE_COLOR = "#bbbbbb"
# 重点类别行背景（浅金）
HIGHLIGHT_CATS = ["External Transfers", "Wages", "All Other Credits", "Unknown Loans"]
HIGHLIGHT_BG = "#FFF3D6"
# diff 标注阈值（百分点）
LABEL_THRESHOLD = 0.5
TOP_N_STAR = 3  # 板块内 |diff| 前 N 加星号

# 板块名英文化（与图 2.1 一致）
GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"
GRID = "#E5E5E5"


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


def render(path: Path, items: List[Dict[str, Any]],
           input_name: str = paths.REVIEW_REPORT_NAME) -> Dict[str, List[Dict[str, Any]]]:
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
            it["_diff"] = it["_fv"] - it["_il"]
        rows.sort(key=lambda r: -abs(r["_diff"]))  # 差异绝对值降序（最大在上）
        groups.append({"name": group, "rows": rows})

    XMIN, XMAX = 0.1, 30.0  # 横轴对数刻度 0.1%–30%；0 覆盖率映射到左端 XMIN
    LABEL_X = 34.0          # diff 标注固定列位（行尾，log 轴上 pp 差与位置解耦）

    # 纵向分面：支出类 23 行需要高度，按类别数分配面板高度（2×2 网格会压扁不可读）
    fig = plt.figure(figsize=(12, 13))
    fig.suptitle("Coverage by Category · Illion vs finv", fontsize=15, fontweight="bold", color=INK, x=0.5, y=0.978)
    fig.text(0.5, 0.953,
             "Each row: gray dot = Illion coverage, blue dot = finv coverage; line length = gap on log scale "
             "(small categories expand, big categories compress). Sorted by |gap| descending. "
             "Highlighted rows = key categories.",
             fontsize=9, color=INK_3, ha="center")

    heights = [len(g["rows"]) for g in groups]
    # hspace 不能放 GridSpec 上（matplotlib 3.11 会令 tight_layout 失效），tight_layout 后单独设置
    gs = fig.add_gridspec(len(groups), 1, height_ratios=heights)

    for gi, g in enumerate(groups):
        ax = fig.add_subplot(gs[gi])
        rows = g["rows"]
        n = len(rows)
        # 板块内 |diff| 前 3（且 |diff| ≥ 阈值）加星号
        top3 = {r["category"] for r in
                sorted((r for r in rows if abs(r["_diff"]) >= LABEL_THRESHOLD),
                       key=lambda r: -abs(r["_diff"]))[:TOP_N_STAR]}

        # 重点类别行背景（浅金高亮）
        for i, r in enumerate(rows):
            if r["category"] in HIGHLIGHT_CATS:
                ax.axhspan(i + 0.5, i - 0.5, color=HIGHLIGHT_BG, zorder=0)

        # 每类别：连线 + 两端点 + diff 标注（行尾固定位置）
        for i, r in enumerate(rows):
            il, fv = r["_il"], r["_fv"]
            il_plot = il if il > 0 else XMIN  # 0 覆盖率映射到左端
            ax.plot([il_plot, fv], [i, i], color=LINE_COLOR, lw=1.5, zorder=2)
            if il <= 0:
                # Illion 覆盖率为 0：左端空心圆 + 注释
                ax.scatter([XMIN], [i], s=55, facecolors="white", edgecolors=COLOR_ILLION,
                           linewidths=1.5, zorder=3)
                ax.text(XMIN * 1.5, i - 0.28, "0%", fontsize=7, color=INK_3, va="top", ha="left")
            else:
                ax.scatter([il], [i], s=55, color=COLOR_ILLION, zorder=3)
            ax.scatter([fv], [i], s=55, color=COLOR_FINV, zorder=3)
            if abs(r["_diff"]) >= LABEL_THRESHOLD:
                ax.text(LABEL_X, i, f"{r['_diff']:+.2f}", fontsize=8.5,
                        va="center", ha="left", color="#3c3c3c",
                        fontweight="bold" if r["category"] in top3 else "normal")

        # 纵轴：类别名（top3 加星号前缀）；横轴：对数刻度 0.1/0.3/1/3/10/30%
        ax.set_yticks(range(n))
        ax.set_yticklabels([f"* {r['category']}" if r["category"] in top3 else r["category"]
                            for r in rows], fontsize=8, color=INK)
        ax.set_xscale("log")
        ax.set_xticks([0.1, 0.3, 1, 3, 10, 30])
        ax.set_xticklabels(["0.1%", "0.3%", "1%", "3%", "10%", "30%"])
        ax.grid(axis="x", color=GRID, ls="--", lw=0.5, zorder=1)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(GRID)
        ax.set_xlim(XMIN * 0.8, LABEL_X * 1.15)  # 右缘容纳行尾 diff 标注
        ax.set_ylim(n - 0.55, -0.45)
        ax.tick_params(axis="x", labelsize=9, colors=INK_3)
        ax.tick_params(axis="y", length=0)
        if gi < len(groups) - 1:
            ax.set_xticklabels([])

        gname = GROUP_EN.get(g["name"], g["name"])
        ax.set_title(f"{gname} · {n} categories", fontsize=12, color=INK, loc="left", pad=6)
        # 每面板图例（灰点 = Illion / 蓝点 = finv）
        ax.legend(handles=[Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_ILLION,
                                  markeredgecolor=COLOR_ILLION, markersize=7, label="Illion"),
                           Line2D([0], [0], marker="o", color="none", markerfacecolor=COLOR_FINV,
                                  markeredgecolor=COLOR_FINV, markersize=7, label="finv")],
                  loc="upper right", frameon=False, fontsize=9)
        if gi == len(groups) - 1:
            ax.set_xlabel("Coverage (% of all transactions)", fontsize=10, color=INK_2, labelpad=3)

    fig.text(0.5, 0.012,
             f"Source: {input_name} · 36 categories · log scale 0.1%–30% · "
             f"diff = finv coverage − Illion coverage (pp, exact values at row end) · "
             f"* = top 3 |diff| in segment · gold rows = key categories · 0% = no Illion coverage",
             fontsize=8.5, color=INK_3, ha="center")

    plt.tight_layout(rect=[0, 0.02, 1, 0.94])
    fig.subplots_adjust(hspace=0.38)  # 分面间距（tight_layout 之后再设，避免被覆盖）
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Generated: {path}")

    # 返回各板块行数据（供说明文字使用）
    return {g["name"]: g["rows"] for g in groups}


def summarize(group_rows: Dict[str, List[Dict[str, Any]]]) -> None:
    """说明文字：差异绝对值前 10 类别 + 缺失/异常说明。"""
    all_rows = [r for rows in group_rows.values() for r in rows]
    all_rows.sort(key=lambda r: -abs(r["_diff"]))
    print("\n=== 差异绝对值前 10 类别（diff = finv覆盖率 − Illion覆盖率，百分点）===")
    for i, r in enumerate(all_rows[:10], 1):
        print(f"{i:>2}. {r['category']:<25} {r['_diff']:+7.2f}  (Illion {r['_il']:5.2f}% → finv {r['_fv']:5.2f}%)")
    zero = [r for r in all_rows if r["_il"] <= 0]
    if zero:
        names = "、".join(r["category"] for r in zero)
        print(f"\n缺失/异常说明：{names} 的 Illion 覆盖率为 0（仅 finv 有覆盖），图中以空心圆标记。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 各类别覆盖率哑铃图（illion vs finv，按板块分面）")
    parser.add_argument("--input", type=Path, default=paths.default_review_report())
    parser.add_argument("--output", type=Path,
                        default=paths.report_output_dir() / "coverage_dumbbell_preview.png")
    args = parser.parse_args()
    items = read_categories(args.input)
    if len(items) != 36:
        raise ValueError(f"类别数量异常: {len(items)} (期望 36)")
    group_rows = render(args.output, items, input_name=args.input.name)
    summarize(group_rows)


if __name__ == "__main__":
    main()
