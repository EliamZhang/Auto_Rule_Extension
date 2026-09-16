---
name: performance-report
description: 从 reviews/ 底稿生成分类性能报告（中文/英文 Markdown、docx、PDF、图表）。用户说"出报告"、"性能报告"、"分类性能"、"效果评估"、"对比报告"、"跑报告"、"/performance-report" 时触发。
---

# Performance Report Skill

`modules/assessment` 的流程入口。把 finv_category_V2 的分类结果与 illion 标签逐类别对比，
产出可交付的中文/英文报告与图表。

## 语言要求（强制）

- **所有对话输出必须使用中文**（简体中文）
- **脚本名、参数、产物文件名、类别名**保持英文原文

## 这个 skill 不做什么

- **不生成底稿。** 底稿来自 ARE 的 `scripts/label_compare.py`，本 skill 只消费它
- **不修改任何规则。** 报告是**只读**产物，用于回答「新规则有没有让分类变好」

## 前置条件

输入是 `reviews/<YYYY-MM-DD_HHMM>/label_compare_report.xlsx`。先确认它存在：

```bash
ls -la reviews/*/label_compare_report.xlsx
```

没有底稿就先跑 ARE 的质检层：

```bash
python scripts/label_compare.py --input input/<最新报告>.xlsx
```

> 若 `reviews/` 完全为空，`run_report.py` 会回退到模块内的历史底稿
> `modules/assessment/input/category_difference_report_100.xlsx`（BPA 时代）。
> **实测它本身就是 36 类口径**（5 张表齐全、第 23 行表头、24~59 行 36 个类别），
> 能正常出全部产物。真正的问题是这个文件**不在 git 里**（被根 `.gitignore` 的
> `category_difference_report*.xlsx` 通配排除），全新克隆时该路径不存在，
> 回退路径会打印 `[ERR] 输入底稿不存在` 并以退出码 2 结束。

## 执行

```bash
# 全套（含 4 张图表）：md zh/en + docx + pdf + 4 张图表
python modules/assessment/scripts/run_report.py --with-charts

# 只出纯文本；不加 --with-charts 且不指定 --only 时，图表组会被主动滤掉
python modules/assessment/scripts/run_report.py

# 只跑某一个生成器（可重复），调试用
python modules/assessment/scripts/run_report.py --only md_zh

# 指定底稿和输出目录
python modules/assessment/scripts/run_report.py --input <xlsx> --out-dir <dir>
```

`--only` 取值：`md_zh` `md_en` `docx` `pdf`
`chart_dumbbell` `chart_heatmap` `chart_plot` `chart_dotplot`。

## 产物

全部落在 `reports/<YYYY-MM-DD_HHMM>/`（同一次运行的产物在同一个时间戳目录）：

| 生成器 | 产物 |
|--------|------|
| `md_zh` | `category_difference_report_v2.md` ＋ 内嵌图 `md_chart_2_1_coverage.png`、`md_chart_2_2_dotplot.png` |
| `md_en` | `category_difference_report_v2_en.md` ＋ **同样两个文件名**的内嵌图（会覆盖 `md_zh` 写的那份） |

> ⚠️ 覆盖本身无害：两个 md 生成器里的图表函数逐字节相同，且**两张图都是英文标签**
> （`GROUP_EN = {"收入类":"Income", …}`）—— 中文报告嵌的也是英文图，不存在「中英混排」。
> 改任一侧的图表逻辑时要留意它们写的是同一个路径。
| `docx` | `category_difference_report_100_zh.docx` |
| `pdf` | `full_category_performance_report_zh.pdf` |
| `chart_dumbbell` | `coverage_dumbbell_preview.png` |
| `chart_heatmap` | `coverage_heatmap_preview.png` |
| `chart_plot` | `coverage_gap_preview_b.png` |
| `chart_dotplot` | `dotplot_preview_a.png`、`dotplot_preview_b.png` |

⚠️ **落盘文件数 ≠ 生成器数**：默认（不传 `--with-charts`）跑 4 个生成器、落 **6 个文件**
（4 份报告 + 2 张 md 内嵌图）；`--with-charts` 跑全部 8 个、落 **11 个**。
数文件对不上时先看这里，别当成生成器漏跑。

**单个生成器失败不阻断其余生成器**，最后汇总打印成功/失败清单，退出码 1。
看到退出码 1 先读汇总，不要直接重跑。

## 底稿的 sheet 契约（硬约束）

| 表 | 用途 |
|----|------|
| `00_核心对比` | **主口径**：第 **23** 行是表头（33 个有效列），第 **24~59** 行 = 36 个类别的全量指标 |
| `01_差异诊断地图` | 类别矩阵；含 Top 流向 |
| `02_业务聚类对比` | 板块级聚类矩阵 |
| `03_排查明细` | 逐样本排查 |
| `04_模型监控` | 分类引擎的覆盖/优先级监控统计（**生成器不使用该表**，仅随底稿附带） |

> 行号一律是 **1-based Excel 行号**（`generate_docx_category_report.py:239` 等几处
> docstring 写成「第 23~58 行」是 off-by-one，代码用的是 `min_row=23` 表头 +
> `min_row=24, max_row=59` 数据；`MATRIX_HEADER_ROW = 28` 同样是 1-based）。

**36 个类别是硬断言，但只有 5 个生成器真正校验它**：`docx` 与 4 个图表生成器会抛
`ValueError: 类别数量异常: N (期望 36)`；`md_zh` / `md_en` 靠 `read_categories` 固定读第 24~59 行
+ `reconcile()` / `reconcile_02()` 口径自校兜底，`pdf` 只靠固定行区间，这 3 个**没有**该校验。
板块构成：收入 3 / 支出 23 / 转账 2 / 负债 8。

## 常见问题

| 现象 | 原因 |
|------|------|
| `ValueError: 类别数量异常: 35 (期望 36)` | 底稿选错了 —— `reviews/` 里的**旧格式目录**（如 `reviews/20260811/`）才是 35 类；模块内 `input/` 那份回退底稿实测是 36 类 |
| `KeyError: 'Worksheet 02_业务聚类对比 does not exist.'` | 同上。⚠️ 这是 openpyxl 的**英文**原文；`generate_md_category_report.py:467` 那个中文包装 `工作簿中未找到工作表「…」` 在 `read_*` 路径上**不会**触发 |
| `[ERR] 输入底稿不存在` | `reviews/` 下没有底稿，先跑 `scripts/label_compare.py` |
| 图表生成器全部失败 | 先看是不是缺 `python-docx` —— 4 个图表生成器都 `from generate_docx_category_report import ...`。再装 matplotlib/seaborn：`python -m pip install -r modules/assessment/requirements.txt` |
| docx/pdf 失败但 md 成功 | `python-docx` / `reportlab` 缺失，同样装 requirements |

## 依赖

```bash
python -m pip install -r modules/assessment/requirements.txt
```

`openpyxl` 是全部生成器的硬依赖；`python-docx`（`generate_docx_category_report.py:21`）
与 `reportlab`（`generate_full_category_report.py:10`）是**模块级 import** —— 缺了对应生成器
直接起不来。**4 个图表生成器还都 `from generate_docx_category_report import ...`**
（取 `GROUP_OF` / `GROUP_ORDER` / `norm_cat` / `to_float`），所以它们**也硬依赖 `python-docx`**，
只装 matplotlib/seaborn 修不好。真正函数内延迟导入的只有 `matplotlib` / `seaborn` /
`numpy` / `pandas`（两个 md 生成器里）—— 缺失时纯文本 md 仍可用。

## 相关文档

- `modules/assessment/CLAUDE.md` — 模块级细节（8 个生成器、路径解析的坑）
- `modules/assessment/scripts/paths.py` — reviews/ 与 reports/ 的唯一定位点；**改动排序逻辑前先读
  `_review_sort_key()` 的 docstring**（目录名格式不统一，纯字符串序会选中过期底稿）
