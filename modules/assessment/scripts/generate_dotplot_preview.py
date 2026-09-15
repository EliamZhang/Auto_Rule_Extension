"""生成「分类别一致率分面水平条形图」预览 PNG（matplotlib + seaborn），双风格对比。

用法: python scripts/generate_dotplot_preview.py --input reviews/<最新>/label_compare_report.xlsx \
      [--output output/dotplot_preview_a.png --output-b output/dotplot_preview_b.png]
关键优化点:
  1. 分区配色按条形逐个设置（seaborn 不直接支持逐条边色/填充色，绘制后统一重着色）
  2. 所有条形宽度统一（不编码样本量，仅用一致率一维信息）
  3. 标签仅一致率百分比，字号 9、粗体（B 版统一深灰墨色，A 版 = 边框色），紧贴条形右缘
  4. 参考线 50%/80% 虚线（zorder 低于条形），贯穿全部分面
  5. X 轴刻度固定 0–100%（间隔 20），右缘动态预留贴条标签空间
  6. 仅保留 X 轴主网格浅灰细线，面板纯白
  7. 行高按板块类别数压缩（收入 3 / 支出 23 / 转账 2 / 负债 8），组内按一致率降序
  8. matplotlib 3.11 注意：GridSpec 上设 hspace 会令 tight_layout 失效 → tight_layout 之后
     用 fig.subplots_adjust(hspace=...)；seaborn set_theme 会重置 font.sans-serif → 先
     set_theme 再覆写 rcParams
依赖: matplotlib + seaborn
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端：独立脚本与 docx 集成共用
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from matplotlib import font_manager
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from generate_docx_category_report import (  # noqa: E402
    Analysis, GROUP_OF, GROUP_ORDER, norm_cat, read_details, read_metrics, read_top_flows, to_float,
)

BASE_DIR = Path(__file__).resolve().parents[1]

# 风格 A：精致商务 —— 低饱和深色系 + 细边框
# 风格 B：清爽浅色 —— 无边框亮色 + 数字标签统一深灰
STYLES: Dict[str, Dict[str, Any]] = {
    "a": dict(
        palette={"red": ("#C00000", "#8B0000"), "amber": ("#C98200", "#A45D00"),
                 "green": ("#2e7d32", "#1B5E20")},
        bar_alpha=0.88, bar_width=0.66, bar_edge_lw=0.4,
        label_size=9, label_bold=True, label_pad=3.0, label_ink=None,
        title="Category Agreement Rate · Faceted by Business Segment",
        subtitle="Bar length = agreement rate (intersection / union); panel Avg = transaction-weighted "
                 "segment agreement rate, same as the report table; dashed lines at 50% / 80%; "
                 "dark red <50%, amber 50%–80%, deep green ≥80%",
        hspace=0.42,
        grid_color="#EDEDED", ref_color="#BDBDBD", ref_lw=0.8,
        sep_color="#F0F0F0", sep_on=True,
    ),
    "b": dict(
        # 哑光三色（dataviz validator 全 PASS）：砖红 #C9403A / 暖金 #D98A24 / 灰绿 #4C9460
        palette={"red": ("#C9403A", "#C9403A"), "amber": ("#D98A24", "#D98A24"),
                 "green": ("#4C9460", "#4C9460")},
        bar_alpha=0.90, bar_width=0.62, bar_edge_lw=0.0,
        label_size=9, label_bold=True, label_pad=3.0, label_ink="#3c3c3c",
        title="Agreement Rate by Business Segment",
        subtitle="Bar length = agreement rate (intersection / union); panel Avg = transaction-weighted "
                 "segment agreement rate, same as the report table; dashed lines at 50% / 80%",
        hspace=0.38,
        grid_color="#F2F2F2", ref_color="#C9C9C9", ref_lw=0.7,
        sep_color="#F0F0F0", sep_on=False,
    ),
}

# 板块名英文化
GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

REF_LINE = "#BDBDBD"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"


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


def zone_fill_edge(share: float, palette: Dict[str, Tuple[str, str]]) -> Tuple[str, str]:
    if share < 0.50:
        return palette["red"]
    if share < 0.80:
        return palette["amber"]
    return palette["green"]


def render(path: Path, items: List[Dict[str, Any]], style_key: str,
           segment_avg: Dict[str, float] | None = None, overall_avg: float | None = None,
           input_name: str = paths.REVIEW_REPORT_NAME) -> None:
    for fname in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"):
        if Path(fname).exists():
            try:
                font_manager.fontManager.addfont(fname)
            except Exception:
                pass
    # 顺序关键：seaborn set_theme 会重置 font.sans-serif，必须先调用再覆写
    sns.set_theme(style="white", rc={"axes.facecolor": "#FFFFFF", "figure.facecolor": "#FFFFFF"})
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
    plt.rcParams["axes.unicode_minus"] = False

    st = STYLES[style_key]
    palette = st["palette"]

    groups: List[Dict[str, Any]] = []
    for group in GROUP_ORDER:
        rows = [it for it in items if it["group"] == group]
        for it in rows:
            it["_share"] = to_float(it.get("交集占比（并集）"))
        rows.sort(key=lambda r: -r["_share"])
        # 面板 Avg 默认与 2.2 表同口径（交易加权板块一致率）；未传入时回退类别等权
        avg = segment_avg.get(group) if segment_avg else None
        if avg is None:
            avg = sum(r["_share"] for r in rows) / len(rows) if rows else 0
        groups.append({"name": group, "count": len(rows), "avg": avg, "rows": rows})

    red_n = sum(1 for g in groups for r in g["rows"] if r["_share"] < 0.50)
    amber_n = sum(1 for g in groups for r in g["rows"] if 0.50 <= r["_share"] < 0.80)
    green_n = sum(1 for g in groups for r in g["rows"] if r["_share"] >= 0.80)
    # 整体 Avg 同口径：交易加权（覆盖调整后一致率），未传入时回退类别等权
    if overall_avg is None:
        total_avg = sum(g["avg"] * g["count"] for g in groups) / len(items)
    else:
        total_avg = overall_avg

    # ---- 画布 figsize=(11, 13)，纵向 4 分面 + 底部说明 ----
    fig = plt.figure(figsize=(11, 13))
    fig.suptitle(st["title"], fontsize=15, fontweight="bold", color=INK, x=0.5, y=0.978)
    fig.text(0.5, 0.953, st["subtitle"], fontsize=9, color=INK_3, ha="center")

    heights = [g["count"] for g in groups]
    # hspace 不能放 GridSpec 上（matplotlib 3.11 会令 tight_layout 失效），tight_layout 后单独设置
    gs = fig.add_gridspec(len(groups), 1, height_ratios=heights)

    xmax = max(max(r["_share"] * 100 for r in g["rows"]) for g in groups) + 7  # 右缘预留标签空间

    for gi, g in enumerate(groups):
        ax = fig.add_subplot(gs[gi])
        rows = g["rows"]
        n = len(rows)
        names = [r["category"] for r in rows]
        pcts = [r["_share"] * 100 for r in rows]
        fills = [zone_fill_edge(r["_share"], palette)[0] for r in rows]
        edges = [zone_fill_edge(r["_share"], palette)[1] for r in rows]

        df = pd.DataFrame({"category": names, "pct": pcts})
        sns.barplot(data=df, x="pct", y="category", orient="h", ax=ax,
                    color="#DDDDDD", width=st["bar_width"], legend=False)
        # 逐条重着色：填充 + 边框（透明度/线宽按风格）
        for patch, fill, edge in zip(ax.patches, fills, edges):
            patch.set_facecolor(fill)
            patch.set_edgecolor(edge)
            patch.set_alpha(st["bar_alpha"])
            patch.set_linewidth(st["bar_edge_lw"])
        # 条形右侧紧贴标注：仅一致率百分比
        for patch, edge, r in zip(ax.patches, edges, rows):
            ax.text(patch.get_width() + st["label_pad"], patch.get_y() + patch.get_height() / 2,
                    f"{r['_share'] * 100:.1f}%", fontsize=st["label_size"],
                    fontweight="bold" if st["label_bold"] else "normal",
                    color=st["label_ink"] or edge, va="center")

        # 参考线（50% / 80% 浅灰虚线，zorder 低于条形）
        ax.axvline(50, color=st["ref_color"], ls="--", lw=st["ref_lw"], zorder=1)
        ax.axvline(80, color=st["ref_color"], ls="--", lw=st["ref_lw"], zorder=1)
        # 仅 X 轴主网格浅灰细线；面板纯白
        ax.grid(axis="x", color=st["grid_color"], lw=0.5, zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(st["grid_color"])
        ax.set_xlim(0, xmax)  # 0–100% 刻度；右缘容纳贴条标签
        ax.set_ylim(n - 0.62, -0.38)
        ax.set_xticks([0, 20, 40, 60, 80, 100])
        ax.tick_params(axis="x", labelsize=9, colors=INK_3)
        ax.tick_params(axis="y", length=0, labelsize=8, colors=INK)
        if gi < len(groups) - 1:
            ax.set_xticklabels([])
        # 分面标题：板块名称 + 板块一致率（交易加权，与 2.2 表同口径）
        gname = GROUP_EN.get(g["name"], g["name"])
        ax.set_title(f"{gname} · Avg {g['avg'] * 100:.1f}%", fontsize=12, color=INK,
                     loc="left", pad=6)
        if gi == len(groups) - 1:
            ax.set_xlabel("Agreement rate (intersection / union)", fontsize=10, color=INK_2, labelpad=3)

    # 分面间浅灰分隔线（画在下方分面的上缘内侧，ylim 上界为 n-0.62）
    if st["sep_on"]:
        for gi, g in enumerate(groups):
            if gi == 0:
                continue
            ax = fig.add_subplot(gs[gi])
            ax.axhline(y=len(g["rows"]) - 0.60, xmin=0, xmax=1, color=st["sep_color"], lw=0.5, zorder=3)

    fig.text(0.5, 0.012, f"Source: {input_name} · 36 categories · "
                         f"red {red_n} / amber {amber_n} / green {green_n} · avg agreement {total_avg * 100:.1f}%",
             fontsize=8.5, color=INK_3, ha="center")

    plt.tight_layout(rect=[0, 0.02, 1, 0.94])
    fig.subplots_adjust(hspace=st["hspace"])  # 分面间距（tight_layout 之后再设，避免被覆盖）
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Generated: {path}  [{style_key}]")
    print(f"  Zones: red {red_n} / amber {amber_n} / green {green_n}; avg {total_avg * 100:.1f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 分类别一致率分面水平条形图预览（双风格）")
    parser.add_argument("--input", type=Path, default=paths.default_review_report())
    parser.add_argument("--output", type=Path,
                        default=paths.report_output_dir() / "dotplot_preview_a.png")
    parser.add_argument("--output-b", type=Path,
                        default=paths.report_output_dir() / "dotplot_preview_b.png")
    args = parser.parse_args()
    items = read_categories(args.input)
    if len(items) != 36:
        raise ValueError(f"类别数量异常: {len(items)} (期望 36)")
    # 交易加权口径（与 2.2 表「一致率（类别精确）」同源）：
    # 面板 Avg = Analysis.segment_coverage().exact_rate（交集数量 / 板块并集，板块并集已扣除重复计数）；
    # 整体 Avg = 覆盖调整后一致率（报告总体口径）。
    wb = load_workbook(args.input, data_only=True, read_only=True)
    analysis = Analysis(read_metrics(wb["00_核心对比"]), items,
                        read_top_flows(wb["01_差异诊断地图"]), read_details(wb["03_排查明细"]))
    wb.close()
    segment_avg = {g: analysis.segment_coverage(g)["exact_rate"] for g in GROUP_ORDER}
    overall_avg = to_float(analysis.metrics.get("覆盖调整后一致率", {}).get("result"))
    render(args.output, items, "a", segment_avg, overall_avg, input_name=args.input.name)
    render(args.output_b, items, "b", segment_avg, overall_avg, input_name=args.input.name)


if __name__ == "__main__":
    main()
