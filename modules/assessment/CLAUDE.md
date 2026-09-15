# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本模块由独立的 `BS-CAT-Performance-Assessment` 项目合并而来（见
> `docs/superpowers/specs/2026-09-14-monorepo-merge-design.md` §5）。
> 输入来自 ARE 主链路，输出到仓库根的 `reports/`。

## 这个模块做什么

把 finv_category_V2 的分类结果与 illion 的标签逐类别对比，产出**可交付的分类性能报告**
（中文/英文 Markdown、docx、PDF、图表）。核心口径是 36 个类别的七项指标
（交集占比、两侧独有占比、一致率等），用于回答「新规则有没有让分类变好」。

## 唯一入口

```bash
python modules/assessment/scripts/run_report.py                    # 纯文本报告（md zh/en + docx + pdf）
python modules/assessment/scripts/run_report.py --with-charts      # 再带上 4 张图表
python modules/assessment/scripts/run_report.py --only md_zh       # 只跑一个生成器（可重复）
python modules/assessment/scripts/run_report.py --input <xlsx> --out-dir <dir>
```

`--only` 可选: `md_zh` `md_en` `docx` `pdf` `chart_dumbbell` `chart_heatmap` `chart_plot` `chart_dotplot`。

## 输入 / 输出契约

```
输入  reviews/<YYYY-MM-DD_HHMM>/label_compare_report.xlsx     ← ARE 的 scripts/label_compare.py 产出
输出  reports/<YYYY-MM-DD_HHMM>/                              ← 同一次运行的全部产物落在同一个时间戳目录
```

- 默认输入由 `scripts/paths.py` 解析；`--input` 可覆盖。
- `reviews/` 没有任何可用底稿时，回退到模块内 `input/category_difference_report_100.xlsx`
  （BPA 时代的历史底稿），使本模块能脱离 ARE 主链路独立运行。
- `--out-dir` 缺省是 `reports/<进程启动时间戳>/`。

### 底稿的 sheet 契约（硬约束）

| 表 | 用途 |
|----|------|
| `00_核心对比` | **主口径**：第 23~58 行 = 36 个类别的全量指标（33 列） |
| `01_差异诊断地图` | 类别矩阵（头部行见 `MATRIX_HEADER_ROW`）；含 Top 流向 |
| `02_业务聚类对比` | 板块级聚类矩阵 |
| `03_排查明细` | 逐样本排查 |

- **36 个类别是硬断言**：所有生成器都会校验，数量不符直接抛
  `ValueError: 类别数量异常: N (期望 36)`。板块构成：收入 3 / 支出 23 / 转账 2 / 负债 8。
- 底稿必须是 finv_category_V2 跑完流水线后导出的报告，**且含 `classification_status` 列**。

## 架构

```
scripts/
├── paths.py          ← reviews/ 与 reports/ 的唯一定位点；所有生成器的默认路径都从这里来
├── run_report.py     ← 唯一入口：以子进程编排下面 8 个生成器
└── generate_*.py     ← 8 个生成器，各自独立可执行，共用 --input/--output 契约
```

**8 个生成器**（`run_report.py` 的 `GENERATORS` 元组，顺序即执行顺序）：

| 名称 | 脚本 | 产物 | 图表组 |
|------|------|------|--------|
| `md_zh` | `generate_md_category_report.py` | `category_difference_report_v2.md` | |
| `md_en` | `generate_md_category_report_en.py` | `category_difference_report_v2_en.md` | |
| `docx` | `generate_docx_category_report.py` | `category_difference_report_100_zh.docx` | |
| `pdf` | `generate_full_category_report.py` | `full_category_performance_report_zh.pdf` | |
| `chart_dumbbell` | `generate_coverage_dumbbell.py` | `coverage_dumbbell_preview.png` | ✓ |
| `chart_heatmap` | `generate_coverage_heatmap.py` | `coverage_heatmap_preview.png` | ✓ |
| `chart_plot` | `generate_coverage_plot.py` | `coverage_gap_preview_b.png` | ✓ |
| `chart_dotplot` | `generate_dotplot_preview.py` | `dotplot_preview_a.png`、`dotplot_preview_b.png` | ✓ |

### 三条设计约定

1. **子进程而非 import**。这些生成器有大量模块级常量与全局状态，同进程内多次调用会
   互相污染；子进程还能把单个生成器的崩溃隔离在自身。
2. **单个生成器失败不阻断其余生成器**，最后汇总打印成功/失败清单，退出码 1。
3. **输入必须存在且含 `label_compare_report.xlsx`，否则直接报错退出，不猜**。

### 图表组的第二个产物参数

`generate_dotplot_preview.py` 产两个文件，`run_report.py` 用 `--output` / `--output-b`
区分（`build_command` 里按索引生成 `--output-a`/`--output-b`）。

## 路径解析的坑（`paths.py`）

**审核目录名格式并不统一**——`2026-09-09_1025`、`2026-08-11`、`20260811` 都出现过。
纯字符串序会因 `'-'`(0x2D) < `'0'`(0x30) 把 `20260811` 排到 `2026-09-09_1025` **之前**，
从而选中过期底稿（35 类、缺 `02_业务聚类对比` 表），导致 md/docx/图表全线失败。
`_review_sort_key()` 先抽掉非数字补足到 12 位（YYYYMMDDHHMM）再比较——**改动此处前先读该函数的 docstring**。

`latest_review_report()` 逐个目录回退而非只看最新目录：最新目录可能是只写了候选 CSV 的
半成品（`label_compare.py` 中途失败），此时应继续往前找可用底稿。

## 依赖

```bash
python -m pip install -r modules/assessment/requirements.txt
```

`openpyxl` 是全部生成器的硬依赖；`python-docx` / `reportlab` / `matplotlib` / `seaborn` /
`pandas` / `numpy` 只在部分生成器中**函数内延迟导入**——缺失时纯文本报告仍可用，
只有 `--with-charts` 或 docx/pdf 会失败，并在汇总里提示安装命令。

## 目录里的历史遗留

- `input/` — BPA 时代的历史底稿（`LEGACY_INPUT`），仅作 `reviews/` 缺失时的回退
- `output/`、`tmp/` — BPA 时代的旧产物，已被 `reports/` 取代，不再写入
- 顶层两个 `.pdf` — 参考文档，不是本模块的产物

## 常见问题

| 现象 | 原因 |
|------|------|
| `KeyError: 工作表「02_业务聚类对比」不存在` | 底稿选错了（多半是选到 35 类的旧底稿）——查 `paths.py` 的排序 |
| `ValueError: 类别数量异常: 35 (期望 36)` | 同上；36 类是硬断言 |
| `[ERR] 输入底稿不存在` | `reviews/` 下没有底稿，先跑 ARE 的 `scripts/label_compare.py` |
| 图表生成器失败 | 没装 matplotlib/seaborn，按提示装 `requirements.txt` |
