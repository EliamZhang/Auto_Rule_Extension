---
name: auto-rule-extension
description: 为 finv_category_V2 交易分类流水线的 8 个引擎自动发现并补充分类规则。用户说"补充规则"、"发现盲区"、"分析未覆盖交易"、"扩展规则库"、"/auto-rule-extension" 时触发。
---

# Auto Rule Extension Skill

为 finv_category_V2 交易分类流水线的 8 个引擎自动发现并补充规则。

## 🌐 语言要求（强制）

- **所有对话输出必须使用中文**（简体中文）
- **所有 HTML 弹窗页面必须使用中文**（标签、按钮、提示、分析说明等）
- **代码、脚本名、字段名、命令行参数**保持英文原文
- **引擎名、category 名**保持英文原文

## 前置条件

运行本 Skill 前，必须确认：
1. `input/` 目录下有 `.xlsx` 分类报告（取最新日期的文件）
2. 报告必须包含 `transactions` sheet，且至少有 `text`、`classification_status`、`category`（illion 标签）、`finv_category` 列
3. `raw/` 目录下有各引擎的规则 CSV 文件（已从 finv_category_V2 同步）

## 输入格式

输入文件是 **已跑完流水线的分类报告 .xlsx**，通常包含：
- `transactions` sheet：每笔交易的分类结果
  - `text`：交易原始描述
  - `classification_status`：`classified` / `unclassified`
  - `classification_engine`：哪个引擎认领的
  - `category`：**illion 机构的标签**（半自动标注的 ground truth）
  - `third_party`：illion 的商户识别
  - `counterparty`：我们流水线的商户识别
  - `finv_category`：我们流水线的分类结果
- `income_summary` / `liability_summary` / `category_summary` sheets

## 执行流程

本 Skill 共 8 个阶段，**全程自动推进**，仅在两个节点弹出确认窗口等待人工操作：

```
阶段一 → 阶段二 → 阶段三 → 阶段四  （AI 自动完成，无需人工）
                              ↓
                    🔴 阶段五：弹出确认窗口（人工初筛）
                              ↓ 确认完成 → 自动推进
                    🔴 阶段六：自动跑 test_rules.py
                              ↓
                    🔴 阶段七：AI 分析 + 打印报告 + 弹出最终确认窗口
                              ↓ 确认完成 → 自动推进
                    阶段八：自动写入规则
```

| 阶段 | 名称 | 触发方式 | 产出 |
|------|------|----------|------|
| 一 | 数据准备与缺口发现 | 自动 | `gap_summary.json` + `label_compare_report.xlsx` |
| 二 | 解读分析结果 | 自动 | 理解 gaps + disagreements + label 差异 |
| 三 | 逐引擎智能分析 | 自动 | `*_candidates.csv` |
| 四 | 验证（语法 + 影响面） | 自动 | `validation_report.json` + `impact_report.json` |
| 五 | 🔴 规则确认弹窗 | **弹窗等待人工** | `confirmed_rules.json` |
| 六 | test_rules.py 测试 | 阶段五确认后自动 | `test_report.json` |
| 七 | 🔴 AI 分析报告 + 最终确认 | **弹窗等待人工** | 最终确认规则 JSON |
| 八 | 执行写入 | 阶段七确认后自动 | 规则写入 `raw/` + 可选 sync |

### 自动弹窗机制

阶段五和阶段七需要人工介入。实现方式是：

1. **生成交互式 HTML 页面** → Write 到 `reviews/<date>/` 目录
2. **自动打开浏览器** → `start reviews/<date>/xxx.html`（Windows 上自动弹出）
3. **AskUserQuestion 等待结果** → 用户在 HTML 中完成操作后，点击"复制结果 JSON"按钮，回到 Claude Code 将 JSON 粘贴到 AskUserQuestion 的输入框
4. **收到结果后自动推进** → AI 立即进入下一阶段，无需用户说"继续"

```
AI: Write HTML → Bash start xxx.html → AskUserQuestion("请粘贴确认结果")
         ↓ 浏览器弹出                    ↓ 弹窗等着
    用户在 HTML 里逐条确认/拒绝         用户粘贴 JSON
         ↓                              ↓
    点击"复制结果 JSON" ──────────→ AI 收到，立即推进
```

### 阶段一：数据准备与缺口发现

1. 检查 `input/` 目录，取最新的 `.xlsx` 文件，**记录文件名**（后续阶段复用）
2. 执行 `scripts/analyze_gaps.py`：
   ```
   python scripts/analyze_gaps.py --input input/<latest>.xlsx --output reviews/<date>/
   ```
3. 执行 `scripts/label_compare.py`：
   ```
   python scripts/label_compare.py --input input/<latest>.xlsx --output reviews/<date>/label_compare_report.xlsx
   ```
4. 读取 `reviews/<date>/gap_summary.json`

**产出**：`gap_summary.json` + `label_compare_report.xlsx`

### 阶段二：解读分析结果

**同时阅读两份报告**，它们互相补充：

#### A. `gap_summary.json` — 未覆盖模式（盲区发现）

聚焦于 `classification_status == unclassified` 的交易，帮助发现哪些**新出现的交易模式**没有被现有规则覆盖。

每种模式附带：
- `pattern_norm`：归一化后的文本模板
- `count`：出现频次
- `illion_category`：illion 给的分类标签（可能为空）
- `samples`：原始交易描述样例（最多 5 条）
- `third_parties`：illion 识别的商户名列表

#### B. `label_compare_report.xlsx` — illion vs finv 分类差异（精准定位）⭐

**这是比 gap_summary 更精准的规则挖掘指南**。它直接告诉你两个系统的分类差异在哪里。

报告包含 3 个 Sheet：

**00_核心对比**：
- **核心指标**：一致率、差异率、覆盖率差异、单边缺失统计
- **逐 Category 差异与优化优先级**（P1/P2/P3）：
  - P1 = 高差异量 + 高贡献率 或关键Category — 最优先处理
  - P2 = 有差异且达到中位数或关键Category
  - P3 = 其余低影响差异
  - 包含差异金额、用户数、申请数等业务影响指标
- **主要差异流向**：具体从哪个 Category 流向哪个 Category

**01_差异诊断地图**：
- Top N 差异流向详细数据
- 完整数量矩阵（行=illion, 列=finv，对角线=一致）
- 差异流向占比矩阵
- 差异影响申请数矩阵

**03_排查明细**：
- 按排查优先级排序的逐笔差异明细
- 包含交易文本、金额、dr_cr、原分类、新分类、排查建议

**label_compare 报告对规则挖掘的指导意义**：

```
差异类型                     → 规则挖掘方向
═══════════════════════════════════════════════════════
分类不一致 (mismatch)        → finv 分类错误，需要修正规则或添加排除
仅 illion 有 (reference_only) → finv 漏识别，需要为新类别添加规则
仅 finv 有 (candidate_only)   → finv 多识别，检查是否过度分类
```

**分析策略**：
1. 先看 `00_核心对比` 中 P1/P2 的 Category，这些是最值得处理的
2. 再看 `01_差异诊断地图` 了解具体的差异流向（如 illion=Automotive → finv=Department Stores）
3. 用 `03_排查明细` 看具体交易文本，理解 finv 为什么分错
4. 结合 `gap_summary.json` 中的未覆盖模式，形成完整规则生成策略

#### C. illion 标签的利用策略（核心）

illion 标签是半自动标注，不是 100% 准确的 ground truth。使用策略：

```
illion 标签存在 + 未分类 → 高置信候选规则
  - illion 标签直接作为 category 参考
  - 检查 pattern 是否特异到不会误伤
  - 生成规则时 confidence 可以偏高（0.80-0.90）

illion 标签存在 + 已分类但两者不一致 → 需要判断
  - 如果 illion 更合理 → 建规则来修正
  - 如果 finv 更合理 → 忽略 illion
  - 如果粒度不同（如 illion 说 "Groceries"，finv 说 "Retail"）→ 取决于引擎职责

illion 标签为空 + 未分类 → 需要 Claude 自己判断
  - 依靠 pattern 本身的语义
  - 保守估计 category
  - 降低 confidence
```

### 阶段三：逐引擎智能分析（核心）

对 `gap_summary.json` 中 `engines` 字段的每个引擎，按以下流程操作。

---

## 🚨🚨🚨 商户名/品牌名 = initial 引擎，绝不进 catch_all 🚨🚨🚨

**这是本 Skill 最高优先级的铁律，违反即为规则生成错误。每次生成候选规则前必须逐条自检。**

### ❌ 禁止事项（违反任何一条即为错误）

| 禁止行为 | 正确做法 |
|----------|----------|
| 把品牌名/平台名放入 catch_all 做 keyword | 走 3.0 流程：查 merchant_kb → 新增或更新商户 |
| 把赌博网站名放入 catch_all | 新增为 initial merchant，category=Gambling |
| 把在线服务平台名放入 catch_all | 新增为 initial merchant，category 按 illion 标签确定 |
| 跳过 merchant_kb 查询直接生成 catch_all 规则 | **必须**先查 merchant_kb，确认不存在才能考虑其他引擎 |
| 看到 `pattern_type=merchant` 还分配到非 initial 引擎 | pattern_type 已经是明确信号，直接走 initial 流程 |

### 判断流程（强制，每条 gap pattern 必须走完）

```
Step 1: 这是什么？
  ├─ 具体商户名/品牌名/平台名 → 继续 Step 2
  │   例: PLAYTKA, GARETON BV, BETR, PUNTIQ, AVIAGAMES, COURTNEY TIESTO
  │   例: KAMBALDA VILLAGE, DORSETT GOLD COAST HOTEL, CECIL HOTEL
  │   信号: pattern_type=merchant, 或者 pattern 中包含唯一标识名称
  │
  └─ 通用描述文本/费用文本/转账描述 → 跳过本节，进入 3.2 决策树
      例: INTEREST PAID, ATM OPERATOR FEE, MONTHLY FEE
      例: Fast Transfer From, Osko Payment Received
      信号: pattern_type=generic 或 ambiguous

Step 2: 查 merchant_kb（必须执行！）
  python scripts/search_merchant.py --search "<关键名称>" --fuzzy

Step 3: 根据查询结果采取行动：
  ├─ 找到 + 有 category → 检查 keyword 是否完善，补充遗漏的 keyword 变体
  ├─ 找到 + category 为空 → 写入 reviews/<date>/initial_category_updates.csv
  └─ 未找到 → 写入 reviews/<date>/initial_candidates.csv（新增商户）
```

### 实际案例（反面教材）

```
❌ 错误做法：
   PLAYTKA 是赌博平台 → catch_all keyword: PLAYTKA → Gambling
   错因：PLAYTKA 是品牌名，不是通用关键词。catch_all 只收通用描述词。

✅ 正确做法：
   Step 1: PLAYTKA 是品牌名 → Step 2
   Step 2: python search_merchant.py --search "PLAYTKA" → 未找到
   Step 3: 新增 initial merchant: PLAYTKA, keywords=PLAYTKA, category=Gambling

❌ 错误做法：
   GARETON BV 是赌博支付处理器 → catch_all keyword: GARETON → Gambling

✅ 正确做法：
   Step 1: GARETON BV 是公司名 → Step 2
   Step 2: python search_merchant.py --search "GARETON" → 未找到
   Step 3: 新增 initial merchant: GARETON BV, keywords=GARETON, category=Gambling

✅ 正确做法（通用文本）：
   INTEREST PAID 是通用利息描述 → 不进入 initial 流程
   → 决策树: 入账类 → all_other_credit 或 catch_all → All Other Credits
```

---

#### 3.0 第一步：判断 pattern 性质 + 先查 merchant_kb（强制执行）

**这是每个 gap pattern 的必经步骤，不可跳过。先看上面的 🚨 铁律，再执行下面的流程。**

```
对每个 gap pattern，先回答一个问题：
  这个 pattern 是「商户名/品牌名」还是「通用描述文本」？

判断标准：
  - 包含具体商户名称（如 "DORSETT GOLD COAST HOTEL"、"LAVERTON SUPERMARKE"）→ 商户名
  - 包含品牌/平台名（如 "PLAYTKA"、"BETR"、"AVIAGAMES"、"COURTNEY TIESTO"）→ 商户名/平台名
  - 包含在线服务名（如 "INCEUNION"、"SKMG"、"IMGVIBES"、"DEGABEAT"）→ 商户名/平台名
  - 包含赌博平台名（如 "GARETON"、"PUNTIQ"、"CASINY"、"SURGE AU"）→ 商户名/平台名
  - gap_summary 中 pattern_type=merchant → 直接判定为商户名
  - 是通用类别词（如 "RESTAURANT"、"INTEREST PAID"、"ATM WITHDRAWAL"）→ 通用文本
  - 是转账描述（如 "Fast Transfer From"、"Osko Payment"）→ 通用文本
  - 是费用描述（如 "MONTHLY FEE"、"ATM OPERATOR FEE"）→ 通用文本
```

**如果判断为「商户名/品牌名」→ 必须先查 merchant_kb**：

```bash
# 搜索商户是否存在（精确匹配）
python scripts/search_merchant.py --search "DORSETT GOLD COAST HOTEL"

# 模糊搜索（找相似商户）
python scripts/search_merchant.py --search "LAVERTON SUPERMARKE" --fuzzy

# 搜索 keyword 中是否包含某词
python scripts/search_merchant.py --search "BETR" --field keywords
```

查询结果的处理逻辑：

```
1. 商户存在 + 有 category → 
   - 为什么交易没被匹配到？
   - 检查交易 text 中的具体写法 vs merchant_kb 中的 keywords
   - 如果 keyword 遗漏 → 建议补充 keywords（变体/缩写等）
   - 如果 keyword 已包含 → 排查为什么 Aho-Corasick 没匹配（可能是 text 截断/编码问题）

2. 商户存在 + category 为空 → 
   - 根据 illion 标签 + pattern 语义，补充 category
   - 注意检查是否有长关键字冲突（如 "BETR" 4字母 vs "BETR ENTERTAINMENT LIMITED" 更长，最长匹配自动处理优先级）
   - 写入 reviews/<date>/initial_category_updates.csv
   - 列: merchant_name, new_category, reason, status

3. 商户不存在 → 
   - 根据 illion 标签 + pattern 语义，新增商户
   - 注意: 如果 keyword 较短（≤4字符），检查是否与已有商户的长关键字冲突（最长匹配会自然解决冲突）
   - 写入 reviews/<date>/initial_candidates.csv
   - schema: merchant_name, keywords, link, category, category_source, keyword_updated_at, category_updated_at
   - 额外列: status, hit_count, risk_level, illion_category, samples, notes

4. 搜索不到任何结果 → 
   - 可能不是商户名，返回通用文本处理流程
```

**如果判断为「通用描述文本」→ 按 3.2 决策树分配到对应引擎。**

#### 3.1 引擎选择规则

各引擎的适用范围重新明确：

| 引擎 | 处理什么 | 不处理什么 |
|------|----------|------------|
| **initial** | 具体商户名/品牌名的识别 | 通用关键词 |
| **transfer** | 仅 Internal Transfer / External Transfers | Gambling、Entertainment 等非转账分类 |
| **dishonour** | 银行拒付/退票消息 | — |
| **income** | 工资/Centrelink 等收入（需金额阈值） | 不满足金额阈值的文本 |
| **liability** | 贷款还款、信用卡还款、债务催收等 | 已被 income 分类的交易 |
| **all_other_credit** | 退款/返现/报销/利息等杂项入账 | 非入账类交易 |
| **fee** | 各类银行/账户费用 | — |
| **catch_all** | **仅**通用描述性关键词（非商户名） | 任何具体商户名/品牌名 |

#### 3.1.1 ⚡ 代码级规则约束速查（生成规则前必读）

以下是来自 finv_category_V2 源码的关键约束，违反将导致规则无法正确匹配：

| 引擎 | 文本预处理 | 大小写要求 | 匹配方式 | 关键约束 |
|------|----------|----------|---------|---------|
| **initial** | `clean_text()`: 仅 `[A-Z0-9 ]` | **大写** | Aho-Corasick 全词 + 最长胜出 | keyword 不能是 STOPWORDS 独立 token（70个），每商户最多50变体，`Financial Institutions` 整行被过滤 |
| **transfer** | `lower().strip()` | **小写** | regex 按 priority 第一匹配 | **regex 必须用小写**，dr_cr 列可选（debit/credit/空），对手方是子串匹配非全词 |
| **dishonour** | 无预处理 | 不敏感(flags) | keyword(re.escape) OR regex+required_terms(AND) | keyword 自动 `re.escape` 转义特殊字符 |
| **income** | `clean_text()` 大写 | 大写 | 仅 regex + 金额阈值 + 行为特征 | **不是纯文本匹配**，低于 $100 除非有工资历史否则不触发 |
| **liability** | `upper().strip()` | 大写 | 多格式(全词\b/regex/条件) | counterparty 是全词，credit_card 的 keyword 列实际是 regex，home_loan 是 Format A 多字段 |
| **all_other_credit** | 无预处理 | 不敏感(flags) | **仅 keyword** (re.escape) | **regex 模式未实现**，required_terms 被忽略，只处理入账(credit) |
| **fee** | 仅压缩空格 | **大小写敏感** | regex 第一匹配，CSV动态加载 | category 仅两个值: `"fee"`(→"Fees") 和 `"Overdrawn"`，zero_amount_reject 机制 |
| **catch_all** | `clean_text()` 大写 | 大写 | keyword(全词) OR regex，**最高 confidence 胜出** | keyword 必须大写仅含 `[A-Z0-9 ]`，confidence 建议 0.70-0.85 |

**跨引擎关键交互**（来自 `orchestrator.py`）：
- initial 的 "Financial Institutions"/"Debt Collection"/"Debt Consolidation" 在 pipeline 中被清除，由 liability 兜底
- liability 排除已被 income 分类为 Wages/Centrelink 的行
- all_other_credit 可以覆盖 "External Transfers"
- income_engine 复用 initial_engine 的 cached automaton 做 KB counterparty 查找
- 后执行的引擎总是覆盖前面的，**不管 confidence 高低**

**fee_engine 说明**: 规则从 `fee_classification_rules.csv` 动态加载，可直接追加 CSV 添加新规则。`priority` 升序（数字越小越先匹配），category CSV 中用 `"fee"`（自动转 `"Fees"`）或 `"Overdrawn"`。pattern 是 `^` 锚定的 regex，大小写敏感。`zero_amount_reject=true` 的规则在金额为 $0.00 时被撤销。

#### 3.2 读取已有规则

读取该引擎在 finv_category_V2 中的所有规则文件，理解：
- 规则 schema（每列的含义和取值范围）
- 现有规则的风格（大小写、confidence 区间、命名规则）
- 现有规则覆盖了哪些模式
- 与该引擎相关的 disagreements（从 `gap_summary.json` 中取出）

#### 3.3 对每个 gap 模式做决策

**注意：以下决策树在完成 3.0 的 merchant_kb 排查后执行。对于已确认归 initial_engine 处理的商户 pattern，跳过此决策树。**

```
决策树：

0. （前置）这个 pattern 是商户名吗？→ 已完成 3.0 排查，确认归属：
   - 归 initial → 已写入 initial_new_merchants.csv 或 initial_missing_category.csv ✓
   - 不是商户名 → 继续下面的判断

1. 这个模式有 illion 标签吗？
   - 有 → illion 标签作为 category 参考 → 跳到 3
   - 没有 → 自己判断是什么分类 → 跳到 2

2. （无 illion 标签）我能可靠地判断这个模式的分类吗？
   - 能（特征词非常明确，如 "PHARMACY"、"UBER"）→ 给出保守的 category
   - 不能（太泛，如纯数字串、银行内部消息）→ 跳过
   - 不确定 → 标注为 "需要人工判断"

3. 这个模式是噪声吗？
   - 银行系统消息 → 跳过
   - 临时性事件 → 跳过
   - 已有规则可以覆盖 → 跳过，不重复生成

4. 匹配到哪个引擎？（按语义判断，不是按 gap_summary 中的引擎分组）
   - 转账描述（Fast Transfer、Osko Payment 等）→ transfer_engine
   - 拒付/退票 → dishonour_engine
   - 工资/Centrelink + 金额合理 → income_engine
   - 贷款/信用卡还款/催收 → liability_engine
   - 退款/返现/利息 → all_other_credit_engine
   - 费用 → fee_engine
   - 通用消费关键词（非商户名）→ catch_all_engine

5. 我有多确定不会误伤？（宁可漏判不误判）
   - 模式高度特异（≥3个词，或包含独特商户名）→ confidence 0.85-0.90
   - 模式中等特异（2个词，在特定领域常见）→ confidence 0.80-0.85
   - 模式偏泛（1个常见词 + 有 illion 标签佐证）→ confidence 0.70-0.75
   - 模式太泛 + 无 illion 标签 → 直接跳过

6. 选择 match_type：
   - 固定短语/商户名 → keyword（更安全）
   - 有结构变化但模板固定 → regex
   - 优先 keyword，其次 regex
```

#### 3.3 生成候选规则

对每个确认需要的规则，**严格按照该引擎 CSV schema** 生成一条。

⚠️ **生成规则前必读**：CLAUDE.md 详细记录了每个引擎的文本归一化方式、匹配逻辑和多匹配策略。关键注意：

- **文本归一化对齐**：keyword 规则需确认匹配环境是 `clean_text()`（大写 [A-Z0-9]）还是原始文本
- **fee_engine 保留大小写**：`^MONTHLY\s+FEE$` ≠ `^monthly fee$`
- **all_other_credit 只用 keyword**：regex 规则被加载但不会匹配
- **收入不是简单关键词匹配**：需金额阈值 + payer_key + 频率模式
- **transfer 只输出 Internal Transfer / External Transfers**

新增约束：
- 如果 illion 标签存在且与 pattern 语义一致 → 直接采纳 illion category
- 如果 illion 标签存在但不太确定 → 降低 confidence 但仍采纳
- 如果无 illion 标签 → confidence 偏低 0.05-0.10

#### 3.4 写入候选规则 CSV

写入 `reviews/<date>/<engine_id>_candidates.csv`。

格式要求：
- 列名与原始 CSV 完全一致（schema 见 CLAUDE.md）
- 额外增加列：`status`（默认 `☐ confirm`）、`hit_count`、`risk_level`、`illion_category`、`samples`、`target_file`（仅多文件引擎需要，见下方说明）
- 按 hit_count 降序排列

#### 3.5 AI 预筛规则（写在阶段三末尾）

**生成候选规则后，AI 立即逐条评估，预判每个规则的风险，为人工审核提供参考标签。**

**重要原则：AI 不能代替人工做最终决定。所有规则（包括低风险的）都必须进入阶段五弹窗，由人工逐条点击确认或拒绝。AI 的角色是提供「建议标签」降低人工判断负担。**

评估维度：
- 是否有 illion 标签佐证？
- pattern 特异性如何（几个词？是否包含独特标识？）
- match_type 是 keyword（更安全）还是 regex（需仔细检查）？
- 引擎约束是否可能触发（大小写、特殊字符等）？

AI 建议标签（显示在弹窗中，辅助人工判断）：

```
🟢 AI 建议确认（低风险）：
   - keyword 规则 + illion 标签一致 + 无冲突 + gain > 10
   - 弹窗中标记为「AI 建议确认」绿色标签，但仍需人工逐条点击

🔵 需要人工仔细判断：
   - regex 规则、有冲突的规则、零增益规则
   - 无 illion 标签佐证的规则、单关键词 ≤ 2 个词的规则
   - 弹窗中不标记特殊标签，由人工独立判断

🔴 AI 建议拒绝：
   - 引擎约束 ERROR（规则根本不可用）、零增益 + 有冲突
   - pattern 太泛无法确定分类
   - 弹窗中标记为「AI 建议拒绝」红色标签，默认拒绝按钮高亮，但人工仍可改为确认
```

AI 在阶段三结束后、阶段四开始前，在对话中打印预筛摘要：
```
📋 规则预筛结果
═══════════════════════════════════════
  🟢 AI建议确认: 8 条（弹窗中绿色标签，仍需您逐条点击）
  🔵 需要您仔细判断: 5 条
  🔴 AI建议拒绝: 2 条（弹窗中红色标签，您可改为确认）

───────────────────────────────────────
🔵 需仔细判断的规则：
  1. COFFEE_DiningOut [catch_all | keyword] — gain=89, conflict=3
  2. UBER_Transport [catch_all | keyword] — gain=0, conflict=12
  ...
```

### 阶段四：验证

验证分三步：**语法验证** + **影响面分析** + **引擎级约束校验**。

#### 4.1 语法与 Schema 验证

```
python scripts/validate_candidates.py --review_dir reviews/<date>/
```

验证内容：
- 正则语法是否正确编译
- CSV 列是否匹配原规则 schema
- 是否与已有规则重叠
- **⭐ 引擎级约束校验**（新增）：
  - transfer regex 是否用了小写（引擎已转小写）
  - catch_all keyword 是否大写且仅含 [A-Z0-9 ]
  - all_other_credit 是否误用了 regex（引擎不支持）
  - fee category 是否是 "fee" 或 "Overdrawn"
  - income pattern_group 是否是合法值
  - initial category 是否误用了 "Financial Institutions"
  - 以及其他从 finv_category_V2 源码派生的约束

每条约束标注严重程度：**ERROR**（规则不可用）/ **WARNING**（可能不工作）/ **INFO**（最佳实践建议）

#### 4.2 基线影响分析（关键）

**在候选规则生成前**先保存基线：
```
python scripts/baseline.py save --input input/<latest>.xlsx --output baseline/<date>/
```

**候选规则生成后**，模拟匹配影响面：
```
python scripts/baseline.py diff --candidates reviews/<date>/ --baseline baseline/<date>/ --input input/<latest>.xlsx
```

每条规则会显示：
- **gain**：能新分类多少条未分类交易
- **conflicts**：会覆盖多少条已分类交易（分引擎/分类统计）
- `priority_conflict: true` 表示候选引擎优先级更高，会真正覆盖已有分类

#### 4.3 读取验证报告

综合 `validation_report.json` 和 `impact_report.json`，标记风险级别：
- `低`：语法 OK，无冲突，gain > 0
- `中 ⚠`：有少量冲突但优先级不覆盖，或有边界模糊
- `高 ✗`：有严重冲突（会覆盖已有正确分类）或语法错误

### 阶段五：🔴 规则确认弹窗（人工逐条审核）

**阶段四验证通过后自动执行。所有规则（包括 AI 建议确认的）都必须进入弹窗，由人工逐条点击确认或拒绝。AI 仅提供建议标签辅助判断。**

#### 5.1 弹窗设计原则

- **所有规则都必须人工点击**：AI 不能自动确认任何规则，只能标记建议标签
- **三条分组**：🟢 AI建议确认 / 🔵 需仔细判断 / 🔴 AI建议拒绝
- **一眼能看懂**：每条规则展示描述 + 样本，regex 默认折叠
- **操作简单**：逐条 [确认] [拒绝]，AI建议拒绝的默认高亮拒绝按钮
- **全部中文界面**：标签、按钮、分析说明全部使用中文

#### 5.2 弹窗结构

弹窗按 AI 建议分三组，但所有规则都需人工点击：

```
┌─ 顶部统计条 ────────────────────────────────────────┐
│  待审核 15 条 | 已确认 0 | 已拒绝 0 | 待决定 15       │
├─ 🟢 AI 建议确认（13条 · 低风险）─────────────────────┤
│  ┌─ 规则卡片 ────────────────────────────────────┐  │
│  │ 🏪 PLAYTKA → initial 新增商户  [AI建议确认]   │  │
│  │    📈 +155 笔 · 0 冲突 · illion 标签佐证      │  │
│  │    [✅ 确认]  [❌ 拒绝]                        │  │
│  └──────────────────────────────────────────────┘  │
├─ 🔵 需要您仔细判断（2条 · 有冲突或不确定）───────────┤
│  ┌─ 规则卡片 ────────────────────────────────────┐  │
│  │ 🏪 BETR → initial 新增  [Gambling]            │  │
│  │    📈 +174 笔 · ⚠️ 冲突 31 笔                 │  │
│  │    📋 样本 + AI 分析说明                       │  │
│  │    [✅ 确认]  [❌ 拒绝]                        │  │
│  └──────────────────────────────────────────────┘  │
├─ 🔴 AI 建议拒绝（4条 · 高冲突/零增益）───────────────┤
│  ┌─ 规则卡片（默认拒绝按钮高亮）────────────────┐  │
│  │ 🏪 AVIAGAMES → initial  [AI建议拒绝]         │  │
│  │    📈 +33 笔 · 🔴 冲突 199 笔                │  │
│  │    [✅ 确认]  [❌ 拒绝 ← 默认高亮]            │  │
│  └──────────────────────────────────────────────┘  │
├─ 🏪 initial 商户批量（50条）────────────────────────┤
│  [✅ 全部确认]  [❌ 全部拒绝]                       │
├─ 底部固定栏 ───────────────────────────────────────┤
│  [📋 复制确认结果 JSON]                             │
│  已确认 3 · 已拒绝 1 · 待决定 11 · 商户待确认       │
└─────────────────────────────────────────────────────┘
```

#### 5.3 交互规则

- 规则卡片默认展示**人类可读描述**（如 "PLAYTKA → initial 新增商户"），不是正则表达式
- 逐条 [确认] [拒绝] → 即点即生效，卡片变色（绿/红）
- AI 建议拒绝的规则默认拒绝按钮高亮，但人工可改为确认
- [全部确认] / [全部拒绝] → 仅对商户批量组生效
- [📋 复制结果 JSON] → 弹窗底部生成 JSON，一键复制到剪贴板
- **所有分组都展开显示，无需折叠，让人一眼看完**

#### 5.4 确认结果处理

用户在弹窗中逐条确认/拒绝后，点击「复制结果 JSON」粘贴给 AI。AI 根据结果将规则分为 confirmed 和 rejected 两组。所有 decision 都来自人工点击，AI 不做自动确认。

```json
{
  "rules": {
    "playtka": "confirmed",
    "betr": "confirmed",
    "aviagames": "rejected",
    ...
  },
  "initial_merchants": "confirmed"
}

---

### 阶段六：test_rules.py 测试（自动执行）

**阶段五确认完成后自动触发。**

#### 6.1 执行测试

```bash
python scripts/test_rules.py \
    --rules reviews/<date>/confirmed_rules.json \
    --input input/<latest>.xlsx \
    --output reviews/<date>/test_report.json \
    --max-samples 10
```

#### 6.2 test_rules.py 做了什么

与 `baseline.py diff` 互补：
- `baseline.py diff` — 需要先存基线，侧重"宏观影响面估算"
- `test_rules.py` — 轻量，直接从 .xlsx 读取，侧重"实际规则表现测试"

**测试逻辑**：
1. 读取 .xlsx 输入数据，按 `classification_status` 拆分已分类/未分类
2. 对每条规则：
   - **增益测试**：在未分类交易上做 keyword/regex 匹配 → gain count + 样本
   - **冲突测试**：在已分类交易上做匹配 → 检查原引擎优先级，判断是否真冲突
3. 高风险判定：冲突 > 5 笔 或 冲突率 > 10% 或（零增益 + 有冲突）

**输出 test_report.json**：
```json
{
  "summary": {
    "total_rules": 5, "tested_rules": 5,
    "total_gain": 1234, "total_conflicts": 23,
    "conflict_rate": 0.019, "high_conflict_rules": ["rule_name"]
  },
  "per_rule": [{
    "rule_name": "...", "engine": "catch_all",
    "pattern": "...", "category": "Dining Out",
    "gain": {"count": 156, "samples": ["..."]},
    "conflict": {"count": 2, "real_conflict_count": 2, "details": [...]},
    "is_high_conflict": false
  }]
}
```

#### 6.3 自动推进

测试完成后，**立即进入阶段七**，不等用户指令。

---

### 阶段七：🔴 AI 分析报告 + 最终确认弹窗

**阶段六测试完成后自动触发。AI 先分析测试结果并打印报告，再弹出最终确认窗口。**

#### 7.1 AI 分析测试结果并打印

读取 `test_report.json`，**在对话中打印详细分析报告**：

```
📊 测试报告摘要
═══════════════════════════════════════
  测试规则: X 条
  新增覆盖: +X,XXX 笔
  潜在冲突: XX 笔 (冲突率 X.X%)
  平均增益: XXX 笔/规则
  
  ✅ 低风险（可直接写入）: X 条
  ⚠️ 中风险（需人工判断）: X 条
  🔴 高风险（建议剔除）: X 条

───────────────────────────────────────
逐规则详情：
  
  ✅ BAKERY_DiningOut  [catch_all | keyword | conf=0.80]
    新增覆盖 +156 笔  |  冲突 0 笔
    样本: "BAKERY DELIGHT SYDNEY", "ARTISAN BAKERY CAFE", ...
  
  ⚠️ COFFEE_DiningOut  [catch_all | keyword | conf=0.75]
    新增覆盖 +89 笔  |  冲突 3 笔（真冲突 1 笔）
    ⚠ 冲突详情: "COFFEE CLUB" 原分类为 transfer→External Transfers
    → 建议: 降低 confidence 或改为更特异 pattern
  
  🔴 UBER_Transport  [catch_all | keyword | conf=0.85]
    新增覆盖 +0 笔  |  冲突 12 笔（真冲突 8 笔）
    🔴 真冲突样本: 原 initial 已分类的 UBER 交易被覆盖
    → 建议: 剔除该规则，UBER 已由 initial_engine 覆盖
```

#### 7.2 生成最终确认页面

基于 `test_report.json` 生成交互式 HTML（见下方 [测试报告规范](#test-report-html)）。

#### 7.3 自动弹出浏览器

```bash
start reviews/<date>/test_report.html
```

#### 7.4 AskUserQuestion 等待最终确认

```
question: "测试报告已在浏览器中打开。上面已打印详细分析。请逐条最终确认（确认/剔除），完成后点击 [📋 导出最终结果 JSON]，将内容粘贴到下方："
header: "最终确认"
options:
  - label: "已粘贴"
    description: "我已将最终确认 JSON 粘贴到输入框"
multiSelect: false
```

#### 7.5 处理最终结果

用户粘贴后，AI ：
1. 保存最终确认 JSON
2. 打印最终确认摘要
3. **立即进入阶段八**，不等用户指令

---

### 阶段八：执行写入（自动执行）

**阶段七最终确认后自动触发。**

写入本地 `raw/` 目录：
```bash
python scripts/apply_rules.py --review_dir reviews/<date>/
```

同时同步到 finv_category_V2：
```bash
python scripts/apply_rules.py --review_dir reviews/<date>/ --sync_to <finv_path>
```

**注意**：如果最终确认规则来自阶段七的 JSON（而非 CSV status 列），需要先将规则写回对应的 `*_candidates.csv` 并标记 `status="confirmed"`，以兼容 `apply_rules.py` 的读取逻辑。或者直接修改 `apply_rules.py` 使其也支持 JSON 输入。

#### 8.1 完成报告

规则写入完成后，打印最终完成报告：

```
✅ 规则入库完成
═══════════════════════════════════════
  写入规则: X 条
  涉及引擎: catch_all(N), liability(M), ...
  同步 finv: ✅/⏭️ 跳过
  
  下一步: 在 finv_category_V2 中重新跑流水线验证效果
```

## 保守策略（硬性约束）

1. **单关键词 ≤ 2 个词的，必须额外检查**：常见词绝对不能单独做规则
2. **新 pattern 必须 ≥ 3 个字符**
3. **regex 必须包含 `\b` 或 `^`/`$`**
4. **hit_samples 中超过 10% 看起来像其他分类 → 不生成**
5. **宁可漏判也不要误判**
6. **不自动修改已有规则**：只新增，不修改不删除
7. **每个引擎的 CSV schema 必须严格匹配**
8. **代码级匹配约束**（来自 finv_category_V2 源码分析）：
   - **transfer regex 必须用小写** — 引擎用 `text.lower()` 预处理
   - **catch_all keyword 必须是大写且仅含 `[A-Z0-9 ]`** — 引擎用 `clean_text()` 后做全词匹配
   - **all_other_credit 只支持 keyword 模式** — regex 在代码中未实现
   - **fee 规则从 CSV 加载** — 可直接追加 CSV，pattern 大小写敏感
   - **liability counterparty 匹配是全词 `\b...\b`** — 不会子串匹配
   - **transfer counterparty 匹配是子串** — 会子串匹配

## HTML 弹窗规范

阶段五和阶段七需要 Claude 动态生成交互式 HTML 页面。核心要求：
- **所有数据内联在 HTML 中，页面完全自包含，无需服务器**
- **所有界面文字必须使用中文**（标签、按钮、提示、分析说明等；引擎名/category/代码保留英文）
- **category 中文翻译参考**: Dining Out=餐饮, Groceries=超市/杂货, Gambling=赌博, Entertainment=娱乐, Transport=交通, Health=医疗, Automotive=汽车, Retail=零售, Personal Care=个人护理, Gyms and other memberships=健身/会员, Education=教育, Travel=旅游, Fees=费用, Insurance=保险, Utilities=水电, Donations=捐款, Home Improvement=家装, Pet Care=宠物, Subscription TV=订阅电视, Rent=房租, Telecommunications=电信, All Other Credits=其他入账, Internal Transfer=内部转账, External Transfers=外部转账, Wages=工资, Centrelink=福利金, Non SACC Loans=非SACC贷款, SACC Loans=SACC贷款, Credit Card Repayments=信用卡还款, Debt Collection=债务催收, Debt Consolidation=债务整合, Dishonours=拒付, Overdrawn=透支, Unknown Loans=未知贷款, Information=信息, Department Stores=百货

文件输出位置：
- `reviews/<date>/confirmation.html` — 阶段五规则确认弹窗（中文界面）
- `reviews/<date>/test_report.html` — 阶段七最终确认弹窗（中文界面）

### 共享设计规范

**配色方案**：
- 确认 → 绿色 (#059669)
- 拒绝/剔除 → 红色 (#DC2626)
- 编辑 → 橙色 (#D97706)
- 待处理 → 蓝色 (#1D4ED8)
- 高风险规则 → 卡片红色左边框 + ⚠️ 标记
- 支持深色/浅色双主题（`prefers-color-scheme` media query）

**可读性要求（强制）**：
- 正文颜色必须足够深（浅色主题 ≥ `#374151`，深色主题 ≥ `#cbd5e1`）
- 分析说明区域：深色文字配浅色背景，禁止用浅灰文字（如 `#94a3b8`）
- 样本文字：等宽字体 + 深色 + 左边框分隔，冲突样本用琥珀色高亮
- 暗色模式下所有文字对比度必须保持 4.5:1 以上
- 按钮文字：确认白字绿底、拒绝白字红底，确保清晰

**布局规范**：
- 顶部统计卡片行（待审核/已确认/已拒绝/待决定）
- 三组展开：🟢 AI建议确认 → 🔵 需判断 → 🔴 AI建议拒绝
- 所有规则卡片默认展开（不折叠），让人一眼看完
- 底部固定栏：实时统计 + `[📋 复制结果 JSON]` 按钮
- **所有界面文字使用中文**

<a id="confirmation-html"></a>

### 确认弹窗 (confirmation.html)

**用途**：阶段五，**所有规则（包括 AI 建议确认的）都进入弹窗，由人工逐条点击。AI 仅提供 🟢建议确认 / 🔵需判断 / 🔴建议拒绝 三色标签辅助判断。**

**设计原则**：
- 所有规则都必须人工点击 — AI 不能自动确认
- 三组展开不分页 — 一眼看完所有规则
- 一眼能看懂 — 每条规则用人类语言描述，不是正则表达式
- AI 建议拒绝的规则默认拒绝按钮高亮，但人工可改为确认
- 操作简单 — 点击即确认/拒绝，商户批量支持全部确认/拒绝

**数据嵌入**：将规则列表以内联 `<script>` 嵌入。

```javascript
const SUMMARY = {
  ai_confirmed: 8,  // AI已自动确认（不在弹窗中展示）
  need_review: 5,   // 需要人工判断（弹窗展示的）
  ai_rejected: 2,   // AI已自动拒绝
};

const RULES = [
  {
    id: "COFFEE_DiningOut",
    engine: "catch_all",
    description: "关键词 COFFEE SHOP → Dining Out",   // 人类可读，不是regex
    match_type: "keyword",
    confidence: 0.75,
    gain: 89,
    conflict: 1,
    conflict_note: "1笔原分类为 transfer→External Transfers",
    samples: ["COFFEE CLUB QUEENSLAND AUS...", "THE COFFEE SHOP SYDNEY..."],
    raw_pattern: "COFFEE SHOP",  // 折叠，点击展开
    category: "Dining Out",
    target_file: "catch_all_rules.csv",
  }
];
```

**卡片布局**：
- 📌 描述（人类语言） + engine badge + match_type badge
- 📈 增益数字 + ⚠️ 冲突数字（有冲突时红色）
- 📋 匹配样本（2-3 条真实交易文本）
- [查看原始pattern ▸] — 默认折叠
- [✅ 确认] [❌ 拒绝] 按钮 — 即点即生效

**底部栏**：
- [✅ 全部确认] 按钮
- [📋 导出结果 JSON] 按钮
- 实时统计：已确认 X/Y · 已拒绝 X/Y · 待处理 X/Y

<a id="test-report-html"></a>

### 测试报告 (test_report.html)

**用途**：阶段七，基于实际测试结果让用户做最终确认。

**数据嵌入方式**：将 `test_report.json` 的完整内容以内联 `<script>` 标签嵌入。

**交互状态**：每条规则维护 `finalState[idx]`：`'pending'` | `'confirmed'` | `'removed'`

**冲突展示**：
- 🔴 真冲突（is_real_conflict=true）→ 红色高亮表格
- 🔵 同引擎匹配（is_real_conflict=false）→ 蓝色标识，非真冲突
- 高风险规则（is_high_conflict=true）→ 卡片红色左边框 + ⚠️ 标记

**每条规则卡片展示**：
- 规则信息（pattern, category, confidence）
- 📈 新增覆盖 — 笔数 + 交易样本表格
- ⚠️ 冲突详情 — 真冲突/同引擎匹配表格，含交易文本、原引擎、原分类、→新分类
- [✅最终确认] [❌剔除] 按钮

**底部栏**：
- 实时统计（已确认 X / 已剔除 Y / 待决定 Z）
- [📋 导出最终结果 JSON] 按钮 — 将最终确认列表复制到剪贴板

### 生成与弹窗流程

```
阶段四完成
  → AI 读取 candidates + impact_report
  → 内嵌数据到 HTML
  → Write reviews/<date>/confirmation.html
  → Bash start reviews/<date>/confirmation.html     ← 浏览器自动弹出
  → AskUserQuestion("请粘贴确认结果")               ← 等待用户
  → 用户粘贴 → AI 保存 confirmed_rules.json
  → Bash python scripts/test_rules.py ...           ← 自动执行
  → AI 打印分析报告
  → Write reviews/<date>/test_report.html
  → Bash start reviews/<date>/test_report.html      ← 浏览器自动弹出
  → AskUserQuestion("请粘贴最终确认结果")            ← 等待用户
  → 用户粘贴 → AI 执行写入
```

如果浏览器打开失败（非 Windows 环境等），HTML 文件仍可手动在浏览器中打开使用。

**与 baseline.py 的关系**：互补而非替代。
- `baseline.py save + diff` → 宏观影响面分析（需要先存基线）
- `test_rules.py` → 轻量实际测试（直接从 .xlsx 读，适合确认后快速验证）

## illion 标签特别说明

- illion 是第三方数据富化服务，提供 `category` 和 `third_party`（商户识别）
- 其标签可作为**高置信度参考**，但不是 100% 准确的 ground truth
- 当 illion 标签与 finv_category 不一致时：
  - `illion 更具体、finv 更泛` → 考虑加规则细化
  - `illion 与 finv 粒度不同`（如 illion=Dining Out, finv=Entertainment）→ 取决于上下文，挑更合理的
  - `illion 明显错误` → 忽略
- 当 illion 有标签而交易未被分类 → **优先生成规则**（这是最高价值的 gap）

## 各引擎规则格式参考

- **dishonour / all_other_credit**：`rule_type, pattern, required_terms`
- **fee**：`priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description`
- **catch_all**：`rule_name, category, pattern, match_type, confidence`
- **income**：`pattern_group, pattern, match_type, description`
- **liability**（多文件引擎，必须指定 `target_file`）：
  - `counterparty_keyword_rules.csv` → 列：`keyword, counterparty, product_type, match_type`
  - `credit_card_rules.csv` → 列：`priority, account_type, dr_cr, bank, match_type, keyword, min_prefix_len, counterparty, product_type, exclude_pattern`
  - `home_loan_car_loan_rules.csv` → 列：`rule_id, target_field, rule_name, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled`
  - `debt_collection_rules.csv` → 列：`counterparty, product_type, rule_id, match_type, keyword`
  - `debt_consolidation_rules.csv` → 列：`keyword, counterparty, product_type, match_type`
  - `dishonours_rules.csv` → 列：`rule_type, pattern, required_terms`
  - `overdrawn_rules.csv` → 列：`counterparty, rule_id, match_type, pattern, note`
  - `bnpl_maximum_limits.csv` 是配置文件，不生成规则
- **transfer**（多文件引擎，必须指定 `target_file`）：
  - **只处理 Internal Transfer 和 External Transfers 两个 category**
  - `transfer_counterparty_rules.csv` → 列：`keyword, counterparty, match_type`
  - `transfer_external_high_confidence_rules.csv` → 列：`priority, rule_name, category, pattern, dr_cr, description`
  - `transfer_external_medium_confidence_rules.csv` → 同上 schema
  - `transfer_internal_regex_rules.csv` → 列：`priority, rule_name, pattern, dr_cr, description`
  - 其余 exclusion/indicator/pairing 文件通常不自动生成
- **initial**：`merchant_name, keywords, link, category, category_source, keyword_updated_at, category_updated_at`
