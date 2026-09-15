---
name: liability-enrichment
description: 从分类报告的缺口里联网核实疑似放贷商，产出 liability 候选规则。用户说"放贷商"、"lender"、"liability 规则"、"补充放贷商"、"联网核实商户"、"/liability-enrichment" 时触发。
---

# Liability Enrichment Skill

Step 2 of the `modules/liability_enrich` pipeline. Reads the gap list produced by
`gap_source.py`, web-verifies each suspected lender, and writes
`liability_candidates.csv` for `evidence.py` to score.

## 语言要求（强制）

- **所有对话输出必须使用中文**（简体中文）
- **代码、脚本名、字段名、命令行参数**保持英文原文
- **商户名、category 名、product_type** 保持英文原文

## 这个 skill 不做什么

**不写规则。** 本 skill 只产出候选 CSV。规则落库必须走
`validate_candidates.py → baseline diff → 🔴 人工审批 → test_rules.py → 🔴 确认 → apply_rules.py`。
`evidence.py` 回填的 `status` 一律不是 `confirmed`，`apply_rules.py` 不会碰它们。

## 前置条件

```bash
python modules/liability_enrich/gap_source.py \
    --input input/<最新报告>.xlsx \
    --output reviews/<date>/
```

产出 `reviews/<date>/liability_gaps.json`。确认它存在再继续。

## 输入：`liability_gaps.json`

```jsonc
{
  "class_sizes": { "generic_loan_catchall": 236, "unclassified_loan_signal": 330,
                   "illion_liability_missed": 90, "assigned_total": 656 },
  "liability_categories": ["Credit Card Repayments", "Debt Collection", ...],
  "existing_rules": {
    "row_count": 349,
    "counterparties": ["360 Cash Loans", "Liberty Financial", ...],   // 281 个
    "keywords": ["...", ...],                                          // 574 条，已大写
    "product_types": { "personal_loan": 224, "car_loan": 54, ... }
  },
  "gaps": {
    "generic_loan_catchall":   [ {pattern_norm, count, samples[], third_parties[],
                                  illion_categories[], finv_categories[], engines[],
                                  dr_cr[], amount_min/median/max}, ... ],
    "unclassified_loan_signal": [ {..., signal_strength: "strong"|"weak"}, ... ],
    "illion_liability_missed":  [ ..., ... ]
  }
}
```

**`existing_rules` 是关键** —— 判断「这是新放贷商」还是「已有放贷商的别名」全靠它。

## 处理优先级（重要）

实测产出率差异极大，**按这个顺序做，别平铺**：

| 顺序 | 类 | 做法 |
|------|----|------|
| 1 | `illion_liability_missed` | 全部处理。illion 说是负债类而 finv 没归到，是最强信号 |
| 2 | `generic_loan_catchall` | 先看 `samples`，**只处理能看出商户名的**。大量是「向某个说不出名字的放贷商还款」，直接跳过 |
| 3 | `unclassified_loan_signal` 且 `signal_strength=strong` | 处理 |
| 4 | `unclassified_loan_signal` 且 `signal_strength=weak` | **默认跳过**。实测 134 组里 133 组是 ATM 取现/现金存入/利息 |

## 执行流程

### 0. 解析参数

| 参数 | 默认 | 说明 |
|------|------|------|
| `batch_size` | 15 | 本次核实多少个缺口 |
| `max_batches` | 1 | 最多几批（0 = 不限） |
| `gap_class` | 全部 | 只处理某一类 |
| `review_dir` | 最新 | 指定 `reviews/<date>/` |

### 1. 读取缺口

读 `liability_gaps.json`，按上面的优先级排序，取前 `batch_size` 条。
打印将要核实的清单（编号 + pattern_norm + count + 类别）让用户看到。

### 2. 逐个联网核实

对每条缺口：

**a. 先从文本里切出候选商户名。** `pattern_norm` 已剥离日期/金额/长数字，但仍是整条交易描述。
用 `samples` 交叉比对，取其中像商号的那一段（通常是开头的大写词），例如：

```
pattern_norm: "SECURE FUNDING COMMBANK APP BPAY"      → 候选商户名 "SECURE FUNDING"
pattern_norm: "CHARTER MERCANTILE BPAY BILL PAYMENT"  → 候选商户名 "CHARTER MERCANTILE"
pattern_norm: "MISCELLANEOUS CREDIT REDRAW PROCEEDS"  → 不是商户名，跳过
```

**b. WebSearch 核实。** 查询形如 `<商户名> Australia loan` / `<商户名> lender` /
`<商户名> ASIC credit licence`。按以下优先级采信来源：

`AFCA 会员名录`（澳洲持牌放贷商必须加入，覆盖最全）> `ASIC 信贷牌照注册` >
`ABR` > 贷款对比站。

**c. 三选一：**

| 判定 | 动作 |
|------|------|
| 真·新放贷商 | 生成新 `counterparty` 候选。`counterparty` 用官方商号（不是交易文本里的截断写法） |
| 已有放贷商的别名/截断写法 | **不新增 counterparty**，只在 `existing_rules.counterparties` 里找到对应那个，给它补 keyword |
| 根本不是放贷商 | 丢弃，不写入候选 |

判定「已有放贷商的别名」时，同时比对 `existing_rules.keywords`（574 条），
避免补一个已经被别的 keyword 覆盖的变体。

**d. 挑 `product_type`。** 只能是这 7 个值（现有规则的实际分布）：

| product_type | 现有条数 | 用于 |
|--------------|---------|------|
| `personal_loan` | 224 | 个人贷款（默认） |
| `car_loan` | 54 | 车贷 |
| `home_loan` | 15 | 房贷 |
| `bnpl` | 38 | 先买后付 |
| `wage_advance` | 16 | 工资预支 |
| `generic_loan` | 1 | 说不清品类、但确定是贷款（如 `Generic Loans` 这种描述性对照方） |
| `bank` | 1 | 银行自身产品 |

**e. 组装 keyword。**

- 用**分号**分隔多变体（引擎按分号切分，**不是 `|`**）
- 写**大写**（引擎在转大写的文本上匹配）
- 变体来自 `samples` 里实际出现的写法，不要凭空造
- **绝不能以数字开头** —— 见下方硬约束

### 3. 写出候选 CSV

写到 `reviews/<date>/liability_candidates.csv`，UTF-8 with BOM，列顺序固定：

```
target_file,keyword,counterparty,product_type,match_type,status,hit_count,risk_level,illion_category,samples,evidence_source
```

- `target_file` 恒为 `counterparty_keyword_rules.csv`
- `match_type` 恒为 `keyword`
- `status` 恒为 `☐ confirm`（`evidence.py` 会按验证结果改写）
- `hit_count` / `risk_level` / `samples` 留空，由 `evidence.py` 回填
- `illion_category` 取该缺口 `illion_categories` 的第一个值（可能为空）
- `evidence_source` 记联网来源 URL，供人工审批复核

### 4. 交棒给 `evidence.py`

```bash
python modules/liability_enrich/evidence.py \
    --candidates reviews/<date>/liability_candidates.csv \
    --input input/<同一份报告>.xlsx
```

然后**读回 `liability_evidence.json`**，向用户汇报：

- 有多少条 `零增益`（联网假设未获数据支持，建议丢弃）
- 有多少条 `死规则`（keyword 本身非法）
- 有多少条会 `hit_other`（从别的引擎抢行），抢的是哪些引擎、多少行
- 真正有 `hit_unclassified` 增益的是哪几条

### 5. 🔴 等待人工决策

**不要**自动改写 `status`。把上一步的汇总报给用户，
由用户决定哪些候选改成 `confirmed`，再走原有的
`validate_candidates.py → baseline diff → test_rules.py → apply_rules.py`。

## 硬约束（违反即产生死规则或误伤）

1. **只生成 keyword 规则。** `counterparty_keyword_rules.csv` 没有 `rule_type` 列，
   loader 只读 `rule_type`，所以 `match_type=regex` 会被当 keyword 处理
   （转大写 + `re.escape`），永远匹配不到。现有 CSV 里
   `DT\.[A-Za-z0-9]+\s+Sunshine` 就是这样一条死规则。

2. **绝不能生成以数字开头的 keyword。** 匹配边界是
   `(?<![A-Za-z])keyword(?![A-Za-z])`，**不是 `\b`** —— 数字可以穿透。
   `360 CASH LOANS` 会误伤 `1360 CASH LOANS`。

3. **`counterparty` 不能凭空造。** 要么是联网核实出的官方商号，
   要么是 `existing_rules.counterparties` 里已有的名字（补别名时）。

4. **`samples` 为空且 `count < 3` 的缺口要降低优先级。** 单次出现的交易文本
   聚类噪声很大，容易把一次性转账当放贷商。

## 完整链路

```
gap_source.py            → liability_gaps.json      数据侧找缺口
  ↓
liability-enrichment     → liability_candidates.csv  联网核实（本 skill）
  ↓
evidence.py              → 回填 + liability_evidence.json   数据侧验证
  ↓
validate_candidates.py   → 语法 / Schema / 重叠 / 引擎约束
  ↓
baseline.py diff         → 影响面 gain / conflict
  ↓
🔴 人工审批（逐条）
  ↓
test_rules.py            → 真实数据实测
  ↓
🔴 最终确认
  ↓
apply_rules.py           → 写入 raw/liability_rule/counterparty_keyword_rules.csv
```
