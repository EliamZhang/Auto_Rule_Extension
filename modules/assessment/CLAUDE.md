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
python modules/assessment/scripts/run_report.py                    # 纯文本报告（md zh/en + docx + pdf；md 另带 2 张内嵌图）
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
  ⚠️ 但该文件**不在 git 里**——它被根 `.gitignore` 的 `category_difference_report*.xlsx`
  通配排除，`git ls-files modules/assessment/input` 为空、也没有 `.gitkeep`。所以全新克隆时
  这条回退路径形同虚设：`run_report.py` 会打印 `[ERR] 输入底稿不存在` 并返回 2。
  要恢复独立运行能力，需把它纳入 git（`git add -f modules/assessment/input/category_difference_report_100.xlsx`）。
- `--out-dir` 缺省是 `reports/<进程启动时间戳>/`。

### 底稿的 sheet 契约（硬约束）

| 表 | 用途 |
|----|------|
| `00_核心对比` | **主口径**：第 23 行是表头（33 个有效列），第 **24~59** 行 = 36 个类别的全量指标 |
| `01_差异诊断地图` | 类别矩阵（头部行见 `MATRIX_HEADER_ROW`）；含 Top 流向 |
| `02_业务聚类对比` | 板块级聚类矩阵 |
| `03_排查明细` | 逐样本排查 |
| `04_模型监控` | 生成器**不读取**该表（`generate_full_category_report.py` 只在正文文本里提到它） |

- **36 个类别是硬断言，但只覆盖 8 个生成器中的 5 个**：`docx` 与 4 个图表生成器会校验，
  数量不符直接抛 `ValueError: 类别数量异常: N (期望 36)`；`md_zh` / `md_en` 靠 `read_categories`
  固定读第 24~59 行 + `reconcile()` / `reconcile_02()` 口径自校兜底，`pdf` 只靠固定行区间，
  这 3 个**没有**该校验。板块构成：收入 3 / 支出 23 / 转账 2 / 负债 8。
- 底稿必须是 finv_category_V2 跑完流水线后导出的报告。ARE 主链路约定底稿含
  `classification_status` 列，但**本模块不读该列**，缺了也不报错。

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
| `md_zh` | `generate_md_category_report.py` | `category_difference_report_v2.md` ＋ 内嵌图 `md_chart_2_1_coverage.png`、`md_chart_2_2_dotplot.png` | |
| `md_en` | `generate_md_category_report_en.py` | `category_difference_report_v2_en.md` ＋ 同样两张内嵌图 | |
| `docx` | `generate_docx_category_report.py` | `category_difference_report_100_zh.docx` | |
| `pdf` | `generate_full_category_report.py` | `full_category_performance_report_zh.pdf` | |
| `chart_dumbbell` | `generate_coverage_dumbbell.py` | `coverage_dumbbell_preview.png` | ✓ |
| `chart_heatmap` | `generate_coverage_heatmap.py` | `coverage_heatmap_preview.png` | ✓ |
| `chart_plot` | `generate_coverage_plot.py` | `coverage_gap_preview_b.png` | ✓ |
| `chart_dotplot` | `generate_dotplot_preview.py` | `dotplot_preview_a.png`、`dotplot_preview_b.png` | ✓ |

> ⚠️ **落盘文件数不等于生成器数**：默认 4 个生成器产出 **6 个文件**，加上
> `--with-charts` 的 4 个图表组是 **11 个**（`-` 为默认就有，`+` 仅 `--with-charts`）：
>
> | # | 文件 | 来源 |
> |---|------|------|
> | 1 | `category_difference_report_v2.md` | `md_zh` |
> | 2 | `category_difference_report_v2_en.md` | `md_en` |
> | 3 | `category_difference_report_100_zh.docx` | `docx` |
> | 4 | `full_category_performance_report_zh.pdf` | `pdf` |
> | 5 | `md_chart_2_1_coverage.png` | `md_zh` / `md_en` **共用同一文件名** |
> | 6 | `md_chart_2_2_dotplot.png` | 同上 |
> | +7~11 | `coverage_dumbbell_preview.png`、`coverage_heatmap_preview.png`、`coverage_gap_preview_b.png`、`dotplot_preview_a.png`、`dotplot_preview_b.png` | 4 个图表组 |
>
> 后两个是 md 正文内嵌的图，`md_zh` / `md_en` 各自写一份到**同一个路径**，
> 所以 `md_en` 会覆盖 `md_zh` 的那张（当前两次渲染内容一致，无实际影响，
> 但改任一侧的图表逻辑时要留意）。`run_report.py` 从不传 `--no-charts`，
> 所以「纯文本报告」默认也带这 2 张内嵌图。
> 两个 md 生成器各自还有 3 个 `run_report.py` **不透传**的参数：`--samples-per-category`
> （默认 5）、`--check`（只做口径校验、不写文件）、`--no-charts`（跳过图表渲染）。

### 三条设计约定

1. **子进程而非 import**。这些生成器有大量模块级常量与全局状态，同进程内多次调用会
   互相污染；子进程还能把单个生成器的崩溃隔离在自身。
2. **单个生成器失败不阻断其余生成器**，最后汇总打印成功/失败清单，退出码 1。
3. **缺底稿时报错退出，不猜**：默认输入按上节解析（`reviews/` 最新可用底稿，全无则回退
   模块内 `LEGACY_INPUT`）；连回退底稿也不存在时才打印 `[ERR] 输入底稿不存在` 并以退出码 2 结束。

### 图表组的第二个产物参数

`generate_dotplot_preview.py` 产两个文件，`run_report.py` 用 `--output` / `--output-b`
区分（`build_command` 里第 1 个产物用 `--output`、第 2 个才是 `--output-b`，**没有 `--output-a`**）。

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

`openpyxl` 是全部生成器的硬依赖；`python-docx`（`generate_docx_category_report.py`）与
`reportlab`（`generate_full_category_report.py`）是**模块级 import**——缺了对应生成器直接起不来。
真正**函数内延迟导入**的只有 `matplotlib` / `seaborn` / `numpy` / `pandas`（在两个 md 生成器里延迟；
4 个图表生成器是模块级 import，但它们本来就只在 `--with-charts` 时才跑）。因为每个生成器都是
独立子进程，缺失时纯文本报告仍可用，只有 `--with-charts` 或 docx/pdf 会失败，并在汇总里提示安装命令。

## 目录里的历史遗留

- `input/` — BPA 时代的历史底稿（`LEGACY_INPUT`），仅作 `reviews/` 缺失时的回退。
  ⚠️ **未纳入 git**，全新克隆时这条回退路径不可用 —— 详见上方「输入解析」一节的说明
- `output/`、`tmp/` — BPA 时代的旧产物，已被 `reports/` 取代，不再写入。
  ✅ `output/` 下 6 个旧产物（`category_difference_report_100_zh.docx`、`_v4.docx`、`_v5.docx`、
  `category_difference_report_v2.md`、`_en.md`、`pdf/full_category_performance_report_zh.pdf`）
  已于 **2026-09-15 取消 git 追踪**（`git rm -r --cached`，文件仍留在磁盘上），
  并在根 `.gitignore` 加了 `modules/assessment/output/` 规则
- 顶层两个 `.pdf` — 参考文档，不是本模块的产物

## 常见问题

| 现象 | 原因 |
|------|------|
| `KeyError: 工作表「02_业务聚类对比」不存在` | 底稿选错了（多半是选到 35 类的旧底稿）——查 `paths.py` 的排序 |
| `ValueError: 类别数量异常: 35 (期望 36)` | 同上；`docx` 与 4 个图表生成器有这条硬断言 |
| `[ERR] 输入底稿不存在` | `reviews/` 下没有底稿，且模块内 `input/` 回退底稿也不存在（未纳入 git）。先跑 ARE 的 `scripts/label_compare.py`，或用 `--input` 指定底稿 |
| 图表生成器失败 | 没装 matplotlib/seaborn，按提示装 `requirements.txt` |
