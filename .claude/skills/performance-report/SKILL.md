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

## 生成后复核（必做）

`成功 8/8` 只证明**进程没崩**，不证明**报告说得对**。正文里的断言句是模板 + 本次数据插值
拼出来的（见下节），历史产物里出现过「报告断言与正上方表格互相矛盾」而汇总依然全绿的情形。
所以每次出完报告，必须先跑下面这段再做交付：

```bash
PYTHONUTF8=1 python - <<'PY'
import sys, pathlib, re, openpyxl, pdfplumber
from docx import Document
sys.path.insert(0, "modules/assessment/scripts")
from paths import latest_review_report          # 不要自己 sorted() —— 见下方说明

draft = latest_review_report()
a2 = openpyxl.load_workbook(draft, read_only=True)["00_核心对比"]["A2"].value or ""
date = re.search(r"生成时间[:：]\s*(\d{4}-\d{2}-\d{2})", str(a2)).group(1)

# reports/ 的目录名格式统一（YYYY-MM-DD_HHMM，定宽补零），字符串序在这里是安全的；
# reviews/ 不是，所以底稿必须走 paths.latest_review_report()。
out = sorted(p for p in pathlib.Path("reports").iterdir() if p.is_dir())[-1]
_docx = Document(out / "category_difference_report_100_zh.docx")
texts = {
    "md_zh": (out / "category_difference_report_v2.md").read_text(encoding="utf-8"),
    "md_en": (out / "category_difference_report_v2_en.md").read_text(encoding="utf-8"),
    # docx 的封面日期在**表格单元格**里，只遍历 paragraphs 会漏掉（会误报成 0 次）
    "docx": "\n".join([p.text for p in _docx.paragraphs]
                      + [c.text for t in _docx.tables for r in t.rows for c in r.cells]),
    "pdf": "\n".join(p.extract_text() or "" for p in pdfplumber.open(out / "full_category_performance_report_zh.pdf").pages),
}
print("底稿:", draft.parent.name, "| 生成时间:", date)
for name, t in texts.items():
    print(f"  {name:6} 含生成时间 {t.count(date)} 次")   # 四份都必须 > 0
print("  pdf 含底稿名:", texts["pdf"].count(draft.name), "次")   # 必须 > 0
PY
```

> ⚠️ **不要用 `sorted(glob("reviews/*/"))[-1]` 挑底稿** —— `'-'`(0x2D) < `'0'`(0x30)，
> 纯字符串序会把 `20260811` 排到 `2026-09-09_1025` **后面**，于是复核用的是一份 35 类的过期底稿，
> 得出的日期结论全错。这条坑在 `paths.py` 的 `_review_sort_key()` docstring 里有详细说明，
> 复核脚本必须复用 `paths.latest_review_report()`，不能另起一套排序。

**四条硬判据**（不满足就是产物有问题，回去查生成器，不要手工改报告文件）：

1. **四份报告的封面日期相同，且等于底稿 `00_核心对比!A2` 的「生成时间」** —— 不是运行日期
2. **PDF 页脚显示的是本次底稿的真实文件名**（PDF 是唯一在页脚印源文件名的产物）
3. **每句「最大 / 主要 / 高于」都能对着紧邻的表格复算出来** —— 打开 PDF 第 2 页与 md 摘要，
   逐个核：谁最大、往哪边偏、差几个百分点。这一步**不能自动化**，只能人看
4. **落盘 11 个文件**（`--with-charts`），对不上先看 `## 产物` 的计数说明

> 底稿没换、只改了生成器时，**不能只看「跑通了」**：`run_report.py` 的退出码与报告内容无关。
> 改完断言句后，至少把第 2 页的差异来源句和第 2.2 节的板块句读一遍。

### 修断言句时：怎么验「本次数据走不到」的那个分支

报告里多数断言是二分/多分分支（mismatch 是不是第一大、覆盖率差是否够 1 个百分点、哪一侧更高）。
**当前底稿只会命中其中一支**，另一支改了也跑不到，光看产物等于没验。别去改底稿
（`00_核心对比` 的指标行与类别表 24~59 互相牵连，`reconcile()` / `reconcile_02()` 会自校失败），
正确做法是**在内存里只覆盖驱动决策的那几个属性**，跑完整 `build_report`：

```python
import sys, pathlib; sys.path.insert(0, "modules/assessment/scripts")
from openpyxl import load_workbook
import generate_md_category_report as gen
from paths import latest_review_report

draft = latest_review_report()
wb = load_workbook(draft, read_only=True, data_only=True)
metrics = gen.read_metrics(wb[gen.SHEET_00]); categories = gen.read_categories(wb[gen.SHEET_00])
top_flows = gen.read_top_flows(wb[gen.SHEET_01]); matrix = gen.read_matrix(wb[gen.SHEET_01])
date = gen.read_generated_date(wb[gen.SHEET_00]); wb.close()

an = gen.MdAnalysisFull(metrics, categories, top_flows, matrix,
                        gen.read_samples(draft, per_category=5), date,
                        detail_rows=gen.read_row_count(draft, gen.SHEET_03))
# 只覆盖决策变量。保持 mismatch + finv_only + illion_only == diff_total，否则章节间会自相矛盾
an.mismatch, an.finv_only_total, an.illion_only_total = 5859.0, 12000.0, 5989.0
an.illion_coverage, an.finv_coverage = 0.85, 0.90      # 想验「更广」分支就改这两项

doc = gen.MdDoc(pathlib.Path("/tmp/out.md"))
gen.build_report(doc, an, None, None, input_name=draft.name, generated_date=date)
print(doc.render())        # 或写盘后 grep 目标句子
```

驱动变量速查（都是 `MdAnalysis` 的普通实例属性，可直接赋值）：

| 想验的分支 | 覆盖这些属性 |
|-----------|-------------|
| 「并非覆盖不足」/「并非分类规则分歧」 | `mismatch` `finv_only_total` `illion_only_total`（三者之和保持 `diff_total`） |
| 「略高于 / 基本持平」、覆盖方向翻转 | `illion_coverage` `finv_coverage`（差值 ≥1.0pp 才走「更广 / 略高于」） |
| §2.1 收尾的归因句 | 同上第一行 —— 它与摘要的 `mismatch_is_top1` 同源 |

> 四个生成器的**入口形态不同**，上面的片段只对 `md_zh` 可直接照抄。**驱动变量名四者一致**
> （`mismatch` / `finv_only_total` / `illion_only_total` / `illion_coverage` / `finv_coverage`），
> 差别只在怎么把分析对象造出来：
>
> | 生成器 | 分析对象 | 入口 |
> |--------|---------|------|
> | `md_zh` | `MdAnalysisFull(metrics, categories, top_flows, matrix, samples, date, detail_rows=)` | `build_report(doc, an, …)` |
> | `md_en` | 同上（**类名相同**） | **`build_report_en(doc, an, …)`** |
> | `docx` | **`Analysis(metrics, categories, top_flows, details)`** —— 没有 matrix/samples，改吃 `read_details(ws)` | `build_report(doc, an, …)` |
> | `pdf` | **没有分析类** | **`build_report_v2(input_path, output_path)`** —— 吃路径不吃对象，要在调用前 monkeypatch 模块级 `read_core_metrics` |
>
> 动笔前先 `grep -n "^class\|^def build_report\|^def read_" ` 确认当前签名，别照抄别的生成器。

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

## 报告叙事的来源与已知陷阱

### 正文不是模型写的

四份文本报告的正文 = **生成器源码里手写的句子模板** + 从底稿插值的数字，**没有任何 LLM 参与**：

- 报告里每一句话都能在 `generate_md_category_report.py` / `generate_md_category_report_en.py` /
  `generate_docx_category_report.py` / `generate_full_category_report.py` 里找到对应模板。
  **措辞不对就改模板**——不存在「调提示词」这一步。
- 数字随底稿变，**措辞只在源码写死的那几种之间切换**。看到别扭的转折、或与表格打架的断言，
  一律先怀疑模板，而不是底稿。
- 报告里**没有、也不应该有模型判断**：凡是读起来像「分析」的句子，都是人预先写好的。
  所以「报告这么说」≠「数据支持这个说法」——这正是上一节复核存在的理由。

### ⚠️ 文案改动必须 4 处同步

`md_zh` / `md_en` / `docx` / `pdf` **没有任何共享模块**——四个脚本只 import 同目录的 `paths`，
互不 import，同一句话逐字内联复制在四处（`md_zh` 与 `docx` 甚至逐字节相同）。

**改任何一句正文，必须同时改这 4 个文件。** 这是真实踩过的坑：`md_en` 的 §2.1 收尾句先被改成
条件分支，`md_zh` / `docx` 落后很久——同一份数据下英文按排名说话、中文仍是无条件断言。

> 「抽一个共享决策模块」被评估过并**否决**：本模块没有任何测试基础设施（无 pytest / conftest /
> tests 目录），抽出来也没法单测，去重收益小于 diff 变大带来的审阅成本。
> 所以这条约束**只能靠文档和人来守**。同类证据还有 PDF 的 `read_detail()`：
> 它曾用**位置索引**取 `illion Category` / `finv Category`，底稿加过列之后静默取错列
> （23,848 行里 19,939 行的 illion 侧落不进任何板块），报告照样跑通。

常改句子的定位锚（用这些串去 grep，**不要记行号**——四个文件的行号互不相关且一直在动）：

| 段落 | `md_zh` / `docx` | `md_en` | `pdf` |
|------|------------------|---------|-------|
| 摘要·覆盖率对比 | `略高于` / `基本持平于` | `broadly on a par with` / `pp ahead of` | `基本持平` |
| 摘要·主要问题 | `并非覆盖不足` / `并非分类规则分歧` | `not coverage quantity` / `not label disagreement` | `最大的差异来源` |
| §2.1 开头 | `在覆盖率上` | `On coverage` | — |
| §2.1 收尾 | `但在共同覆盖范围内的一致性较高` | `agreement within the shared set` | — |
| §2.2 板块总述 | `而非识别质量缺陷` | `more than weak recognition` | — |
| §3.5 建议表 | `本节为人工撰写的固定检查清单` | `fixed, hand-written checklist` | `本节为人工撰写的固定检查清单` |

### 已修的陷阱（2026-09-24）

这批模板长期存在同一类问题：**断言写在结论分支之外**，无论数据怎么变都照说。
最严重时 PDF 会说出与正上方表格相反的话。已修：

| # | 陷阱 | 修法 |
|---|------|------|
| T1 | PDF 封面日期写死 `2026-08-19` | 补 `read_generated_date()`，读底稿 `00_核心对比!A2` |
| T8 | PDF 页脚写死旧底稿名 `category_difference_report(2).xlsx` | 改取 `--input` 的 `Path.name` |
| T2 | PDF 断言「仅 finv 有值…是最大差异来源」，而表格里 `双方分类不一致` 更大 | 按三者**实际排名**选最大项再成句 |
| T3 | PDF 写死「finv 覆盖范围高于 Illion」 | 按两侧覆盖率大小条件化，并带差值 |
| T4 | 摘要分支结论后紧跟无条件的「当前主要问题并非覆盖不足」 | 跟随 `mismatch_is_top1` 分支 |
| T5 | 「覆盖率基本持平于」无条件；且摘要用 `0.05` **分数**阈值、§2.1 用 1.0 **个百分点** | 统一到 1.0 pp，共用 `cov_parity` |
| T6 | §2.1 收尾断言「整体差异源于分类标签分歧」，无视上方刚打印的真实排名 | 移植 `driver_txt` 条件分支 |
| T7 | PDF 死代码 v1 builder 内含硬编码数据值 `6,525` 与写死的日期/方向 | 删除 8 个顶层 def：`build_report`、`add_category_block`、`category_metric_table`、`category_conclusion`、`detail_category_stats`、`flow_rows_for_category`、`clean`、`first_existing_sheet` |
| T9 | PDF `read_detail()` 用位置索引取 `illion Category` / `finv Category`，底稿加列后静默取错列 | 改按**列名**解析别名 |
| T10 | §2.1 开头「较 X 高出 N 个百分点，覆盖范围略广」只按差值大小分档，不看持平判定 —— 两侧真正持平时会说出「高出 0.00 个百分点，覆盖范围略广」 | 与摘要共用 `cov_parity`；收尾句另用 `cov_concl_txt` |
| — | 「差异主要集中在 X、Y 类」写死类别，而 X/Y 需按数据排名 | 改为按板块差异笔数排序取前三 |

> 另：摘要里那句「差异交易主要集中在…」**不能**把 Top 20 流向的两侧笔数相加——跨板块流向会被
> 两侧各计一次，排名会被转账类这种大板块系统性拉高。正确口径是 `segment_summary(group)["total"]`
> （取自完整 37×37 类别矩阵，并集语义）。

### 故意**没**修的部分（下次改到这里时请留意）

以下断言仍是人工写死的，**不随数据变化**，交付时要靠人判断是否仍然成立：

- **`transfer_segment_headline`**（`generate_md_category_report.py` 等）——硬编码
  「最大分歧是 ET → IT」「Illion 判对外 / finv 判对内」的**方向与因果**，且末句
  「其余差异以 ET 单边识别为主」不看数据。数字是动态的，归因是写死的。
- **§2.2 收尾的质量判语**：`一致率偏低反映的是{worst_side}覆盖面更广，而非识别质量缺陷`
  ——无论 `worst_side` 是谁、单边占比多高都照说，且**没有阈值门控**。
- **`一致率最高的三个类别为…，这些类别多为规则清晰的负债与固定支出类交易`** ——
  类别名是算出来的，后半句定性是写死的。
- **类别专项段落（Rent / Gambling / Retail 等）的处置建议** ——这些**有**阈值门控
  （如 `trans_in >= detail_n * 0.55`），比上面几条安全；但阈值与文案的对应是人工设定的经验值，
  且**所有分支都不满足时整段静默省略**——读者看不到「本次没有主因判断」这件事。

> 这些是**人工判断**，不是数据推导。改它们之前先想清楚：是想让它随数据变（那就要加门控分支），
> 还是明确保留为经验之谈（那就像 §3.5 一样加一条 `add_callout` 声明它是静态清单）。

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
