# 三项目合并 + Liability 联网更新 — 设计文档

- **日期**: 2026-09-14
- **状态**: 已评审，待实施
- **涉及仓库**:
  - `D:/project/Auto_Rule_Extension`（ARE，主仓库）
  - `D:/project/Merchant-Extraction-new`（ME）
  - `D:/project/BS-CAT-Performance-Assessment`（BPA）

---

## 1. 背景

ARE 是 finv_category_V2 分类流水线的规则维护系统（9 个引擎的规则发现→生成→验证→入库）。
另外两个项目一直在独立演进，但实测表明它们与 ARE 本就在同一条流水线上：

```
Merchant-Extraction-new  →  Auto_Rule_Extension  →  BS-CAT-Performance-Assessment
   造商户 KB                 发现缺口、生成规则         评估分类效果、出报告
   （产出 initial 引擎规则）  （产出 9 引擎规则）        （消费 ARE 的分析报告）
```

**关键验证**：ARE 的 `scripts/label_compare.py` 产出的 5 个 sheet（`00_核心对比` / `01_差异诊断地图`
/ `02_业务聚类对比` / `03_排查明细` / `04_模型监控`），与 BPA 全部 8 个脚本读取的 sheet 完全同名同构。
BPA 的输入 xlsx 就是 ARE 阶段一的产物，目前靠人工拷贝流转。

本次工作要达成三件事：

1. 把 ME、BPA 合并进 ARE 成为**单一仓库**
2. 自动化 `raw/` ← `finv_category_V2` 的规则同步（当前靠手工复制）
3. 新增 liability 放贷商规则的**联网发现**能力

---

## 2. 现状实测

### 2.1 规则副本已双向漂移

对 `raw/` 与 `finv_category_V2` 的 18 个受管规则文件逐一比对 sha256：

| 规则文件 | raw/ | finv_category_V2 | 漂移方向 |
|---|---|---|---|
| `rent_rules.csv` | 15 行 / 5 列 / 703 B | **21,809 行 / 7 列 / 2,906,530 B** | finv 远超，**且 schema 已变** |
| `merchant_kb.csv` | 74,461,168 B | 74,308,664 B | 两边不同 |
| `catch_all_rules.csv` | **456 行** | 440 行 | ⚠️ **raw 领先 16 行**（待推送） |
| `counterparty_keyword_rules.csv` | 349 行 | **350 行** | finv 领先 1 行 |
| 其余 14 个 | — | — | 一致 |

**结论**：漂移是**双向**的。只做"从 finv 拉取"会抹掉 raw/ 里 16 条未推送的 catch_all 规则。

### 2.2 merchant_kb.csv 三方分叉

同一个知识库存在三份互不相同的副本：

| 位置 | 行数 | 时间 |
|---|---|---|
| ARE `raw/initial_rule/` | 876,279 | 08-27 |
| ME 根目录 | 876,083 | 08-21 |
| finv `initial_engine/` | 74,308,664 B（未逐行比对） | — |

ARE 与 ME 两份的逐行差异：共同 876,051 行，仅 ARE 有 228 行，仅 ME 有 32 行，
**同名但内容不同 460 行**。

### 2.3 各项目的既有缺陷

**Merchant-Extraction-new**
- `merchant_kb.csv` 实际是 3 列 / 876,251 行，但 `label_merchants.py` 与 `verify_merchants.py`
  的 `validate_kb_fieldnames()` 要求 6/7 列，**对当前文件会直接抛 ValueError 拒绝运行**
- `merge_category.py` 整个脚本从未入库
- `update_category.py:74` 硬编码 `C:\Users\zhangyuliang02\Desktop\Merchant Extraction`（指向他人机器）
- CLAUDE.md 描述的是旧版 "~2.5M rows / 13 列"，与实际严重不符

**BS-CAT-Performance-Assessment**
- 8 个脚本 / 9,891 行，**无入口脚本**，靠人工按顺序跑
- 36 类业务常量在 docx 版与两个 md 版中**三份复制**，代码注释自承"改了不会同步"
- docx 版仍以被截断的 `03_排查明细` 作统计分母（md 版已修正）
- 硬编码 `C:/Windows/Fonts/*` 字体路径 6 处

**Auto_Rule_Extension**
- `merchant_kb.csv`（74 MB）与 `merchant_kb.csv.keyword_bak`（53 MB）被 git 跟踪，`.git` 膨胀至 145 MB
- CLAUDE.md 的 rent 章节写"当前 15 条规则 / 5 列"，与实际（21,809 条 / 7 列）不符
- `validate_candidates.py` 的 rent 校验基于过时的 5 列 schema

---

## 3. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | 合并为**单一仓库**，ME/BPA 作为 `modules/` 子目录 | 消除 KB 多副本、报告手工拷贝；一次 commit 覆盖"改规则+改KB+改报告" |
| D2 | KB 移出 git + `filter-repo` 清理历史 | 74 MB 二进制每次变更都让仓库 +74 MB；可回溯性由 `reviews/` + `baseline/` 保证 |
| D3 | `sync_rules.py` 做**三方对比**，且**只拉不推** | 漂移是双向的，无基线无法判定方向；推送必须走审批后的 `apply_rules.py --sync_to` |
| D4 | liability 联网更新 = **发现新放贷商 + 补充已有 alias** | 用户明确范围 |
| D5 | 联网结果**只自动发现，人工审批后写入** | 与项目铁律"任何规则写入操作前必须经过人工确认"一致 |
| D6 | 实现形态为**混合**：脚本做数据侧，Claude Skill 做联网侧 | 与现有框架分工一致（`analyze_gaps.py` 出数据，Claude 生成规则） |

---

## 4. 目标架构

### 4.1 目录结构

`scripts/` 保持现有布局不动——它被 SKILL 文档与 145 行 CLAUDE.md 大量引用，挪动收益低、破坏面大。
新增 `modules/` 承载三个运维项目。

```
D:/project/Auto_Rule_Extension/          ← 唯一 git 仓库
├── CLAUDE.md                            总上下文：模块地图 + 跨模块契约
├── config.json                          共享配置：引擎定义 + 模块路径 + finv.root
├── .gitignore                           新增：merchant_kb.csv、reports/*
│
├── raw/                                 【规则事实源】9 引擎规则副本
│   ├── initial_rule/merchant_kb.csv     ← 唯一 KB（移出 git）
│   └── liability_rule/                  ← liability_enrich 的写入目标
├── input/                               【数据入口】分类报告 .xlsx
├── reviews/                             【产物】每次运行的审核产物
├── baseline/                            【产物】基线快照
├── reports/                             【产物】对外报告（原 BPA output/）
│
├── scripts/                             【主链路】ARE：发现→生成→验证→落库
│   ├── common.py                        ← 扩 load_module_config()
│   ├── sync_rules.py                    ★ 新增
│   └── ...（其余 13 个脚本不动）
│
├── modules/
│   ├── merchant_kb/                     ME：KB 构建与维护（纯标准库）
│   │   ├── CLAUDE.md                    ← 迁入并修正过时描述
│   │   ├── settings.py  utils.py
│   │   ├── build_knowledge_base.py  dedup_keywords.py
│   │   ├── label_merchants.py  verify_merchants.py
│   │   ├── merge_category.py            ← 补齐入库（从未提交过）
│   │   └── merge_manual_entries.py  split_uncategorized.py  update_category.py
│   │
│   ├── assessment/                      BPA：分类性能评估与报告
│   │   ├── CLAUDE.md
│   │   ├── run_report.py                ★ 新增唯一入口
│   │   └── generate_*.py                （8 个脚本原样迁入）
│   │
│   └── liability_enrich/                ★ 新增：联网更新放贷商
│       ├── CLAUDE.md
│       ├── gap_source.py
│       ├── evidence.py
│       └── sources.py
│
└── .claude/skills/
    ├── auto-rule-extension.md           （现有）
    ├── classify-merchants.md            ← 从 ME 迁入
    ├── merchant-kb-maintenance.md       ★ 新增：把 ME 零散脚本串成流程
    ├── performance-report.md            ★ 新增：BPA 出报告
    └── liability-enrichment.md          ★ 新增：联网更新
```

### 4.2 模块边界原则

1. **`modules/` 之间不互相 import**。唯一例外：`liability_enrich` 复用 `merchant_kb/utils.py`
   的 DeepSeek 客户端（`post_json` / `call_with_retry`）。等出现第二个消费者再抽共享模块。
2. **路径统一收敛到顶层 `config.json`**。ME 的 `settings.py` 保留 KB 专属常量
   （停用词、缩写白名单、ABR 过滤规则），仅把 `FINAL_OUTPUT` 指向 `raw/initial_rule/merchant_kb.csv`。
3. **依赖不混装**。ME 保持纯标准库；BPA 的 `python-docx` / `matplotlib` / `reportlab` 依赖
   由 `modules/assessment/requirements.txt` 单独声明。

---

## 5. 数据流契约

合并的核心价值在于消灭四处手工搬运：

| # | 现状 | 目标契约 |
|---|---|---|
| 1 | BPA 读 `input/category_difference_report_100.xlsx`，靠人工从 ARE 拷贝 | BPA 默认输入改为 `reviews/<latest>/label_compare_report.xlsx`；`run_report.py` 自动定位最新 review |
| 2 | ME 根目录与 ARE `raw/initial_rule/` 各持一份 KB，已分叉 | KB 唯一位置 `raw/initial_rule/merchant_kb.csv`；ME 脚本直接读写 |
| 3 | 新商户/新 alias 靠人脑记住去 ME 跑 | `liability_enrich` 产出 `reviews/<date>/liability_candidates.csv`，复用现有 validate→test→apply |
| 4 | `raw/` ← `finv_category_V2` 靠手工复制 | `scripts/sync_rules.py` 三方对比 + 拉取（见 §6） |

### 5.1 `reviews/<latest>` 与 `run_report.py` 的定义

- **`<latest>` 的判定**：取 `reviews/` 下目录名按字符串倒序的第一个（目录名格式为
  `YYYY-MM-DD_HHMM`，字符串序即时间序）。无可用目录时报错退出，不猜。
- **`run_report.py` 的职责**：作为 `modules/assessment/` 的唯一入口，按固定顺序调用
  docx / md / pdf 生成器，统一从 `reviews/<latest>/label_compare_report.xlsx` 取输入、
  输出到 `reports/<date>/`。替代当前"人工记住 8 个脚本的执行顺序"。
  单个生成器失败不阻断其余生成器，最后汇总打印成功/失败清单。

### 5.2 顺带修正的两处不一致

- **文件名**：ARE 存 `label_compare_report.xlsx`，BPA 找 `category_difference_report*.xlsx`。
  同内容两个名字，统一为 `label_compare_report.xlsx`。
- **明细截断**：`label_compare.py:2410` 的 `EXCEL_MAX_DATA_ROWS` 默认值导致 `03_排查明细`
  只写入 1,048,572 行（全量差异 2,869,569 条）。BPA 的 md 版已改用前 3 个 sheet 做统计绕开，
  docx 版仍受影响。合并后需重新确定该默认值。

---

## 6. `scripts/sync_rules.py` 设计

### 6.1 命令

```bash
python scripts/sync_rules.py status                        # 漂移总览（默认动作）
python scripts/sync_rules.py pull                          # finv → raw/，自动备份
python scripts/sync_rules.py adopt                         # 把当前状态登记为新基线
python scripts/sync_rules.py status --engine rent --diff   # 单文件行级 diff
```

### 6.2 输出形态

```
引擎        文件                              状态            建议
rent        rent_rules.csv                    finv 领先       pull ⚠ schema 5→7 列
catch_all   catch_all_rules.csv               raw 领先 16 行   push
liability   counterparty_keyword_rules.csv    finv 领先 1 行   pull
initial     merchant_kb.csv                   三方分叉         人工裁决
fee         fee_classification_rules.csv      一致             —
```

### 6.3 判定逻辑

基线清单 `.sync_state.json` 位于仓库根目录，纳入 git（记录的是文件内容 sha256，与机器无关；
因而克隆到新机器后同步状态依然有效）。据此判定：

| 条件 | 状态 | 动作 |
|---|---|---|
| 双方 hash 均未变 | 一致 | — |
| 仅 finv 变 | finv 领先 | `pull` |
| 仅 raw 变 | raw 领先 | 提示去跑 `apply_rules.py --sync_to` |
| 双方均变 | 冲突 | 只报告，不自动处理 |
| 清单中无记录 | 未登记 | 人工确认一次后 `adopt` |

### 6.4 设计选择

- **只拉不推**。推送必须经 `apply_rules.py --sync_to`，该方法位于审批门之后。
  `sync_rules.py` 不提供 push，避免绕过门禁。
- **schema 漂移独立告警**。列数/列名变化显式报出，不静默复制。
  §2.1 中 rent 的 5→7 列即由此发现。
- **KB 按 sha256 比较，不做行级 diff**（74 MB）。需要时用 `--diff` 显式触发。
- **`pull` 前自动 `.bak` 备份**。
- **复用现有路径解析**：`common.py` 的 `resolve_rule_path()` / `resolve_finv_path()`
  已对 18 个文件全部验证通过，无需新写路径逻辑。

---

## 7. `modules/liability_enrich` 设计

### 7.1 四步流水线

```
Step 1  gap_source.py —— 数据侧找缺口
        从分类报告抽三类"疑似放贷商"交易：
          a. finv_category="Non SACC Loans" 且 counterparty="Generic Loans"
             ← apply_generic_loan_catchall 的兜底命中
          b. classification_status="unclassified" 且文本含 LOAN/CREDIT/CASH/FINANCE
          c. illion 标签属负债类 但 finv 未归类
        产出 reviews/<date>/liability_gaps.json

Step 2  Skill（Claude）—— 联网侧判断
        读 gaps + 现有 280 个 counterparty，逐个 WebSearch 核实，三选一：
          • 真·新放贷商             → 新 counterparty 候选
          • 已有放贷商的别名/截断写法 → keyword 补充候选
          • 根本不是放贷商           → 丢弃
        产出 reviews/<date>/liability_candidates.csv

Step 3  evidence.py —— 数据侧验证（关键闸门）
        对每条候选 keyword 在真实交易上统计 hit_count / samples / risk。
        联网提出的假设必须由数据证实；零命中的候选标记为零增益。

Step 4  复用现有链路，一行不改：
        validate_candidates.py → baseline.py diff
        → 🔴 阶段五人工审批 → test_rules.py → 🔴 阶段七确认 → apply_rules.py
```

### 7.2 候选 CSV 契约

列名与 `raw/liability_rule/counterparty_keyword_rules.csv` 严格一致，尾部追加元数据列：

```
keyword,counterparty,product_type,match_type,status,hit_count,risk_level,samples,evidence_source
```

`evidence_source` 记录联网来源 URL，供人工审批时复核。

⚠️ 该列**需要新增到 `scripts/common.py` 的 `META_COLUMNS`**（当前为
`{status, hit_count, risk_level, illion_category, samples, target_file}`，不含 `evidence_source`），
否则 `apply_rules.py` 不会剥离它，会污染写入 `raw/` 的规则文件。

### 7.3 硬约束

来自 CLAUDE.md 的 liability 章节，违反即产生死规则：

1. ⚠️ **只生成 keyword 规则**。`counterparty_keyword_rules.csv` 没有 `rule_type` 列，
   而 loader 只读 `rule_type`——现有 CSV 中那条 regex 行
   （`DT\.[A-Za-z0-9]+\s+Sunshine`）就是永远匹配不到的死规则。
   要加 regex 必须先给 CSV 加列，属于独立改动。
2. **匹配边界是 `(?<![A-Za-z])...(?!A-Za-z)`，不是 `\b`**——数字可穿透。
   因此**不得生成以数字开头的 keyword**："360 CASH LOANS" 会误伤 "1360 CASH LOANS"。
3. keyword 用**分号**分隔多变体，匹配大小写不敏感。

### 7.4 检索源优先级

`AFCA 会员名录`（澳洲持牌放贷商必须加入，覆盖最全）> `ASIC 信贷牌照注册` >
`ABR`（ME 已有 XML 管线可复用）> 贷款对比站。

---

## 8. 迁移步骤

> **实施计划应拆成两份**：
> - **计划 A — 合并与同步**（阶段 0–6）：把两个项目并进来、修好路径、建好 `sync_rules.py`。
>   做完后仓库即可正常使用，可独立验收。
> - **计划 B — Liability 联网更新**（阶段 7–8）：新增模块与 skill。
>   依赖计划 A 完成（需要 `modules/` 结构与 `sync_rules.py` 均就位）。
>
> 两者之间不要交叉，计划 A 验收通过后再启动计划 B。

每步可独立回滚。

| # | 阶段 | 内容 | 风险 |
|---|---|---|---|
| 0 | 备份 | 三仓库完整复制到 `D:/project/_merge_backup_20260914/` | — |
| 1 | **git 瘦身** | `.gitignore` 排除 KB；`git filter-repo` 抹除历史中的 `merchant_kb.csv` 与 `.keyword_bak`；145 MB → 预计几 MB | ⚠️ **重写历史，需 force push** |
| 2 | **历史迁入** | `git subtree add --prefix=modules/merchant_kb` / `--prefix=modules/assessment`，保留各自提交历史 | 低 |
| 3 | **KB 归并** | ARE ∪ ME（32 行独有 + 460 条内容冲突裁决）→ 再与 finv 版本比对 | 中，需人工裁决 |
| 4 | 目录改造 | ME/BPA 路径改指顶层 `config.json`；修 `update_category.py:74` 绝对路径 bug | 低 |
| 5 | `sync_rules.py` | 三方对比；首次 `adopt` 前先裁决 §2.1 的 4 个漂移文件 | 中 |
| 6 | 文档修正 | CLAUDE.md rent 章节、`config.json` rent 配置、`validate_candidates.py` rent 校验 | 低 |
| 7 | `liability_enrich` | 新模块 + `liability-enrichment.md` skill | 低 |
| 8 | Skill 接入 | `merchant-kb-maintenance.md`、`performance-report.md` | 低 |

**第 1 步与第 2 步的顺序是刻意的**：先瘦身再合并，避免把 ME/BPA 的历史一并卷入重写。
`filter-repo` 需单独安装（`pip install git-filter-repo`）；不可用时回退 `git filter-branch`。

### 8.1 实施前的准备动作

- ME 有 2 处未提交改动：`.gitignore` 的 `cbcbcb已经清洗/` 一行、未入库的 `merge_category.py`。
  需先提交，否则 `subtree add` 会漏掉。
- BPA 工作区干净。

---

## 9. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `filter-repo` 重写历史后 force push | 任何已存在的克隆失效 | 实施前确认除本机外无其他克隆；第 0 步全量备份 |
| **BPA 是协作仓库**（有 `EliamZhang/staging` 的 PR #1、#2） | 合并后协作者工作流变为"向 ARE 的 `modules/assessment/` 提 PR" | **实施前需与协作者确认**；若为活跃协作关系，考虑保留 BPA 独立仓库（改用 D1 的替代方案） |
| KB 归并的 460 条内容冲突 | 若裁决错误会引入分类回归 | 逐条 diff 后人工确认；归并后跑 `baseline.py` 验证 |
| rent 引擎真实规则库（21,809 条）远超 ARE 认知 | 现有 rent 候选规则的重叠检查全部无效 | 第 5 步先 pull，第 6 步同步修正校验逻辑 |

---

## 10. 非目标（YAGNI）

本次**不做**：

- BPA 三份复制的业务常量**不重构**为共享模块。10K 行脚本、三个副本各自演进中，
  合并期动它是纯风险。等 `run_report.py` 入口稳定后再单独立项。
- BPA 硬编码字体路径**不改**（Windows 专用环境，无跨平台需求）。
- 不引入 `requirements.txt` 版本锁定。
- 不做 `sync_rules.py push`（见 §6.4）。
- 不为 liability 增加 regex 规则能力（需先改 CSV schema，属独立改动）。
- 不处理 `merge_initial.py`（一次性硬编码脚本，历史遗留）。
