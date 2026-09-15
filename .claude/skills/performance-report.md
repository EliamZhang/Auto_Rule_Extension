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
> 那份是 35 类的旧口径，**会直接抛错**，不要依赖这条回退路径。

## 执行

```bash
# 默认：纯文本报告（md zh/en + docx + pdf），4 张图表
python modules/assessment/scripts/run_report.py --with-charts

# 只出纯文本，跳过依赖重的图表
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
| `md_zh` | `category_difference_report_v2.md` |
| `md_en` | `category_difference_report_v2_en.md` |
| `docx` | `category_difference_report_100_zh.docx` |
| `pdf` | `full_category_performance_report_zh.pdf` |
| `chart_dumbbell` | `coverage_dumbbell_preview.png` |
| `chart_heatmap` | `coverage_heatmap_preview.png` |
| `chart_plot` | `coverage_gap_preview_b.png` |
| `chart_dotplot` | `dotplot_preview_a.png`、`dotplot_preview_b.png` |

**单个生成器失败不阻断其余生成器**，最后汇总打印成功/失败清单，退出码 1。
看到退出码 1 先读汇总，不要直接重跑。

## 底稿的 sheet 契约（硬约束）

| 表 | 用途 |
|----|------|
| `00_核心对比` | **主口径**：第 23~58 行 = 36 个类别的全量指标（33 列） |
| `01_差异诊断地图` | 类别矩阵；含 Top 流向 |
| `02_业务聚类对比` | 板块级聚类矩阵 |
| `03_排查明细` | 逐样本排查 |

**36 个类别是硬断言**，所有生成器都会校验，数量不符直接抛
`ValueError: 类别数量异常: N (期望 36)`。板块构成：收入 3 / 支出 23 / 转账 2 / 负债 8。

## 常见问题

| 现象 | 原因 |
|------|------|
| `ValueError: 类别数量异常: 35 (期望 36)` | 底稿选错了 —— 多半是选到 BPA 时代的 35 类旧底稿 |
| `KeyError: 工作表「02_业务聚类对比」不存在` | 同上 |
| `[ERR] 输入底稿不存在` | `reviews/` 下没有底稿，先跑 `scripts/label_compare.py` |
| 图表生成器全部失败 | 没装 matplotlib/seaborn：`python -m pip install -r modules/assessment/requirements.txt` |
| docx/pdf 失败但 md 成功 | `python-docx` / `reportlab` 缺失，同样装 requirements |

## 依赖

```bash
python -m pip install -r modules/assessment/requirements.txt
```

`openpyxl` 是全部生成器的硬依赖；`python-docx` / `reportlab` / `matplotlib` /
`seaborn` / `pandas` / `numpy` 只在部分生成器中**函数内延迟导入** ——
缺失时纯文本 md 仍可用。

## 相关文档

- `modules/assessment/CLAUDE.md` — 模块级细节（8 个生成器、路径解析的坑）
- `scripts/paths.py` — reviews/ 与 reports/ 的唯一定位点；**改动排序逻辑前先读
  `_review_sort_key()` 的 docstring**（目录名格式不统一，纯字符串序会选中过期底稿）
