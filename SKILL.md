# Auto Rule Extension Skill

为 finv_category_V2 交易分类流水线的 8 个引擎自动发现并补充规则。

## 触发条件

用户说"补充规则"、"发现盲区"、"分析未覆盖交易"、"扩展规则库"、"/auto-rule-extension" 时触发。

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

### 阶段一：数据准备与缺口发现

1. 检查 `input/` 目录，取最新的 `.xlsx` 文件
2. 执行 `scripts/analyze_gaps.py`：
   ```
   python scripts/analyze_gaps.py --input input/report2.xlsx --output reviews/<date>/
   ```
3. 读取 `reviews/<date>/gap_summary.json`

### 阶段二：解读分析结果

`gap_summary.json` 包含三部分关键信息：

#### A. 各引擎的 gap 模式（`engines` 字段）

每种模式都附带：
- `pattern_norm`：归一化后的文本模板
- `count`：出现频次
- `illion_category`：illion 给的分类标签（可能为空）
- `samples`：原始交易描述样例（最多 5 条）
- `third_parties`：illion 识别的商户名列表

#### B. illion 与 finv 分类不一致（`disagreements` 字段）

已分类但 illion 标签与 finv_category 不一致的交易组。
这不一定是错误——可能只是两套体系的粒度不同。
重点关注**数量大且方向一致的**不一致。

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

**⚠ 核心原则：merchant_kb 优先。凡是可能为商户名/品牌名的 pattern，必须先排查 initial_engine。**

#### 3.0 第一步：判断 pattern 性质 + 先查 merchant_kb（强制执行）

**这是每个 gap pattern 的必经步骤，不可跳过。**

```
对每个 gap pattern，先回答一个问题：
  这个 pattern 是「商户名/品牌名」还是「通用描述文本」？

判断标准：
  - 包含具体商户名称（如 "DORSETT GOLD COAST HOTEL"、"LAVERTON SUPERMARKE"）→ 商户名
  - 包含品牌/平台名（如 "PLAYTKA"、"BETR"、"AVIAGAMES"）→ 商户名/平台名
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
   - 如果 keyword 遗漏 → 建议补充 keywords
   - 如果 keyword 已包含 → 排查为什么 Aho-Corasick 没匹配（可能是 text 截断/编码问题）

2. 商户存在 + category 为空 → 
   - 根据 illion 标签 + pattern 语义，建议补充 category
   - 写入 reviews/<date>/initial_missing_category.csv

3. 商户不存在 → 
   - 根据 illion 标签 + pattern 语义，建议新增商户
   - 写入 reviews/<date>/initial_new_merchants.csv
   - schema: merchant_name, keywords, category, notes

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

### 阶段四：验证

验证分两步：**语法验证** + **影响面分析**。

#### 4.1 语法与 Schema 验证

```
python scripts/validate_candidates.py --review_dir reviews/<date>/
```

检查：
- 正则语法是否正确编译
- CSV 列是否匹配原规则 schema
- 是否与已有规则重叠

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

### 阶段五：展示审核报告

逐引擎展示审核摘要。格式：

```
### engine_id — 候选规则 N 条
现有规则数: XXX → 建议新增 N 条

| status | rule_name | pattern | match_type | confidence | hit_count | illion标签 | 风险 | 说明 |
|--------|-----------|---------|------------|------------|-----------|------------|------|------|
| ☐ confirm | xxx | ... | keyword | 0.85 | 1247 | Dining Out | 低 | ... |

操作提示：
- 将 status 改为 "confirmed" 或 "rejected"
- 也可以直接删除行来拒绝
- 确认后回复"确认"来执行写入
```

同时展示 disagreements 摘要（如果用户要求）。

### 阶段六：执行写入（需人工确认）

**此阶段必须在用户明确确认后才执行！**

写入本地 `raw/` 目录：
```
python scripts/apply_rules.py --review_dir reviews/<date>/
```

同时同步到 finv_category_V2：
```
python scripts/apply_rules.py --review_dir reviews/<date>/ --sync_to D:/project/finv_category_V2
```

## 保守策略（硬性约束）

1. **单关键词 ≤ 2 个词的，必须额外检查**：常见词绝对不能单独做规则
2. **新 pattern 必须 ≥ 3 个字符**
3. **regex 必须包含 `\b` 或 `^`/`$`**
4. **hit_samples 中超过 10% 看起来像其他分类 → 不生成**
5. **宁可漏判也不要误判**
6. **不自动修改已有规则**：只新增，不修改不删除
7. **每个引擎的 CSV schema 必须严格匹配**

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
