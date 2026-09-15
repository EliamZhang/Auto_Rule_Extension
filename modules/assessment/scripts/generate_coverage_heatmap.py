"""生成「各类别覆盖率热力矩阵（illion / finv / diff）」按业务板块分面。

用法: python scripts/generate_coverage_heatmap.py --input reviews/<最新>/label_compare_report.xlsx \
      [--output output/coverage_heatmap_preview.png]
设计:
  1. 四个业务板块纵向分面；每面板 3 列：illion 覆盖率 / finv 覆盖率 / diff（finv − Illion，pp）
  2. 覆盖率列：格子内数据条，条长 = 覆盖率（线性比例 0–30%，用户选定「直接呈现大小」），
     数值显示在条内（条够长时白字）或条右端外侧
  3. diff 列：发散色（蓝 = Illion 更广 / 白 = 相等 / 橙 = finv 更广），色深 = |diff|（≥3pp 饱和，
     固定上限让「更广」一侧颜色更明显），数值带正负号，|diff| ≥ 1 加粗
  4. 行序 = 板块内 |diff| 降序；板块内 |diff| 前 3 的类别名加 *；重点类别标签加粗
  5. 每个板块一个浅灰边框卡片，面板间距 0.35（板块间清晰区分，小面板也不重叠）
  6. 底部一行：覆盖率线性比例尺条（0–30%）+ diff 方向图例（全部置于 GridSpec 内，tight_layout 兼容）
依赖: matplotlib + numpy
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端：独立脚本与 docx 集成共用
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap, ListedColormap
from matplotlib.patches import Patch
from openpyxl import load_workbook
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402
from generate_docx_category_report import GROUP_OF, GROUP_ORDER, norm_cat, to_float  # noqa: E402

BASE_DIR = Path(__file__).resolve().parents[1]

# 覆盖率列：格子内数据条（条长 = 覆盖率，线性比例 0–30%，用户选定「直接呈现大小」）
# 全图蓝橙体系：illion = 蓝、finv = 橙（与 diff 列「illion 更广=蓝 / finv 更广=橙」呼应，
# CVD ΔE 24.7 已验证 PASS）
BAR_COLOR_ILLION = "#2a78d6"
BAR_COLOR_FINV = "#eb6834"
SCALE_BAR_COLOR = "#8C8C8C"  # 底部比例尺：中性灰（仅长度参照，不表达身份）
PANEL_BG = "#FBF7EE"        # 格子浅金底（仅用于体现格子结构）
BAR_MAX = 30.0              # 条长比例上限（当前最大覆盖率 22.88%）
# diff 列发散色：蓝 = Illion 更广 / 白 = 相等 / 橙 = finv 更广（已通过 CVD 验证）
CMAP_DIFF = LinearSegmentedColormap.from_list("diff", ["#2a78d6", "#FFFFFF", "#eb6834"])

# 重点类别（行标签加粗）
HIGHLIGHT_CATS = ["External Transfers", "Wages", "All Other Credits", "Unknown Loans"]
TOP_N_STAR = 3  # 板块内 |diff| 前 N 加星号

# 板块名英文化（与图 2.1 一致）
GROUP_EN: Dict[str, str] = {"收入类": "Income", "支出类": "Expenses", "转账类": "Transfers", "负债类": "Liabilities"}

INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#898781"
TEXT_DARK = "#3c3c3c"


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
            it["_diff"] = it["_fv"] - it["_il"]
        rows.sort(key=lambda r: -abs(r["_diff"]))  # 差异绝对值降序
        groups.append({"name": group, "rows": rows})

    # diff 列对称色标上限固定 3.0：绝大多数差异在 ±3pp 内，色深 = |diff| 且 ≥3pp 即饱和，
    # 让「更广」一侧颜色更明显（动态上限 7.5 会让 +2.23 之类只有 30% 色深）
    diff_vmax = 3.0

    fig = plt.figure(figsize=(12, 13.5))
    fig.suptitle("Category Coverage Comparison · Illion vs finv", fontsize=15, fontweight="bold",
                 color=INK, x=0.5, y=0.992)
    fig.text(0.5, 0.960,
             "Left two columns: horizontal bar = coverage share of all transactions "
             "(blue = Illion, orange = finv), bar length linear 0–30%, value at bar end. "
             "Right column: finv − Illion (pp); orange = finv wider, blue = Illion wider.",
             fontsize=9, color=INK_3, ha="center")
    fig.text(0.5, 0.936,
             "* = top 3 |diff| in segment · bold rows = key categories · "
             f"diff color saturates at ±3 pp · Source: {input_name} · 36 categories",
             fontsize=8.5, color=INK_3, ha="center")

    heights = [len(g["rows"]) for g in groups]
    # 五行网格：4 面板 + 底部色标/图例行；hspace 0.35 拉开板块间距（小面板也清晰分隔）
    gs = fig.add_gridspec(len(groups) + 1, 1, height_ratios=heights + [0.9], hspace=0.35)

    for gi, g in enumerate(groups):
        rows = g["rows"]
        n = len(rows)
        # 每面板拆两个子区：左 = 覆盖率 2 列（log 色标），右 = diff 1 列（发散色标）
        sub = gs[gi].subgridspec(1, 2, width_ratios=[2, 1], wspace=0.04)
        ax_m = fig.add_subplot(sub[0])
        ax_d = fig.add_subplot(sub[1])

        main_data = np.array([[r["_il"], r["_fv"]] for r in rows])
        diff_data = np.array([[r["_diff"]] for r in rows])

        # 覆盖率列：格子浅金底 + 数据条（条长线性 ∝ 覆盖率，illion = 蓝 / finv = 橙）+ 条端数值
        ax_m.imshow(np.full((n, 2), 0.5), cmap=ListedColormap([PANEL_BG]), aspect="auto", vmin=0, vmax=1)
        ax_m.axvline(1.0, color="#E8E0CE", lw=0.8, zorder=1)  # 两列间分隔线
        for i in range(n):
            for j in range(2):
                v = main_data[i, j]
                if v > 0:
                    bw = min(v / BAR_MAX, 1.0) * 0.94  # 条长占格宽比例（左右留边）
                    ax_m.barh(i, bw, left=j + 0.03, height=0.62,
                              color=BAR_COLOR_ILLION if j == 0 else BAR_COLOR_FINV, alpha=0.9, zorder=2)
                    if bw >= 0.45:
                        # 条内白字（条够长时）；否则条右端外侧深字
                        ax_m.text(j + 0.03 + bw / 2, i, f"{v:.2f}", ha="center", va="center",
                                  fontsize=9.5, color="#FFFFFF", zorder=3)
                    else:
                        ax_m.text(j + 0.03 + bw + 0.02, i, f"{v:.2f}", ha="left", va="center",
                                  fontsize=9.5, color=TEXT_DARK, zorder=3)
                else:
                    ax_m.text(j + 0.03, i, "0.00", ha="left", va="center", fontsize=9.5,
                              color=TEXT_DARK, zorder=3)
        ax_m.set_xlim(-0.03, 2.03)
        ax_m.set_ylim(n - 0.5, -0.5)

        # diff 列：发散色块（蓝 = Illion 更广 / 橙 = finv 更广）+ 数值（|diff| ≥ 1 加粗）
        ax_d.imshow(diff_data, cmap=CMAP_DIFF, vmin=-diff_vmax, vmax=diff_vmax, aspect="auto")
        for i in range(n):
            d = diff_data[i, 0]
            txt_color = "#FFFFFF" if abs(d) >= diff_vmax * 0.55 else TEXT_DARK
            ax_d.text(0, i, f"{d:+.2f}", ha="center", va="center", fontsize=10, color=txt_color,
                      fontweight="bold" if abs(d) >= 1.0 else "normal")

        # 列标题与行标签
        top3 = {r["category"] for r in
                sorted((r for r in rows if abs(r["_diff"]) >= 0.5), key=lambda r: -abs(r["_diff"]))[:TOP_N_STAR]}
        ax_m.set_xticks([0, 1])
        ax_m.set_xticklabels(["illion %", "finv %"], fontsize=10, color=INK_2)
        ax_d.set_xticks([0])
        ax_d.set_xticklabels(["diff (pp)"], fontsize=10, color=INK_2)
        ax_m.set_yticks(range(n))
        ax_m.set_yticklabels([f"* {r['category']}" if r["category"] in top3 else r["category"]
                              for r in rows], fontsize=9, color=INK)
        for label, r in zip(ax_m.get_yticklabels(), rows):
            if r["category"] in HIGHLIGHT_CATS:
                label.set_fontweight("bold")
        ax_d.set_yticks([])

        # 面板浅灰边框：每个板块成为独立卡片，板块间清晰区分
        for spine in ax_m.spines.values():
            spine.set_visible(True)
            spine.set_color("#E2DCCF")
            spine.set_linewidth(0.8)
        for spine in ax_d.spines.values():
            spine.set_visible(True)
            spine.set_color("#E2DCCF")
            spine.set_linewidth(0.8)
        if gi < len(groups) - 1:
            ax_m.set_xticklabels([])
            ax_d.set_xticklabels([])

        gname = GROUP_EN.get(g["name"], g["name"])
        ax_m.set_title(f"{gname} · {n} categories", fontsize=12, color=INK, loc="left", pad=6)

    # 底部行：左 = 覆盖率线性比例尺（数据条长度对照 0–30%），右 = diff 方向图例
    sub_b = gs[len(groups)].subgridspec(1, 2, width_ratios=[0.72, 0.28], wspace=0.05)
    ax_cbar = fig.add_subplot(sub_b[0])
    ax_cbar.barh([0], [BAR_MAX], height=0.55, color=SCALE_BAR_COLOR, alpha=0.85)
    ax_cbar.set_xlim(0, BAR_MAX)
    ax_cbar.set_ylim(-0.5, 0.5)
    ax_cbar.set_yticks([])
    ax_cbar.set_xticks([0, 5, 10, 15, 20, 25, 30])
    ax_cbar.set_xticklabels(["0%", "5%", "10%", "15%", "20%", "25%", "30%"], fontsize=8.5, color=INK_3)
    ax_cbar.tick_params(length=2, colors=INK_3)
    for spine in ax_cbar.spines.values():
        spine.set_visible(False)
    ax_cbar.set_title("coverage bar length", fontsize=8.5, color=INK_3, loc="left", pad=2)
    ax_leg = fig.add_subplot(sub_b[1])
    ax_leg.axis("off")
    ax_leg.legend(handles=[Patch(facecolor="#2a78d6", alpha=0.85, label="Illion wider"),
                           Patch(facecolor="#eb6834", alpha=0.85, label="finv wider")],
                  loc="center left", frameon=False, fontsize=9)

    # 嵌套 subgridspec 会触发 tight_layout 的保守警告（布局实际正确应用），忽略
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        plt.tight_layout(rect=[0, 0.008, 1, 0.92])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="#FFFFFF")
    plt.close(fig)
    print(f"Generated: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 各类别覆盖率热力矩阵（illion / finv / diff）")
    parser.add_argument("--input", type=Path, default=paths.default_review_report())
    parser.add_argument("--output", type=Path,
                        default=paths.report_output_dir() / "coverage_heatmap_preview.png")
    args = parser.parse_args()
    items = read_categories(args.input)
    if len(items) != 36:
        raise ValueError(f"类别数量异常: {len(items)} (期望 36)")
    render(args.output, items, input_name=args.input.name)


if __name__ == "__main__":
    main()
