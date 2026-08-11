# 分类引擎规则使用机制详解

> **目的**：本文档详细说明 finv_category_V2（ServiFlow-AI）中 8 个分类引擎各自如何**加载、存储、匹配、覆盖规则**。理解这些机制是 Auto_Rule_Extension 生成高质量规则的前提——生成的规则必须与引擎的实际匹配逻辑兼容。

---

## 一、流水线总体架构

### 1.1 调度器（Orchestrator）

`classification_core/orchestrator.py` — `ClassificationOrchestrator.run()`

```
对每个 engine（按 priority 升序执行）：
  1. candidates = 全部交易（每个引擎都看到全部交易）
  2. 特例：liability 引擎排除已被 income 分类为 Wages/Centrelink 的行
  3. 构建 EngineContext（含 prior_claims 记录已分类交易）
  4. 调用 engine.classify(context) → 返回 EngineResult
  5. 验证 predictions：matched 列、counterparty 非空、category 归属权
  6. _commit() 写入 output：finv_category + counterparty 成对覆盖
     → **后面的引擎永远覆盖前面的**
```

### 1.2 覆盖规则（核心理解）

| 规则 | 说明 |
|------|------|
| **全覆盖** | 所有引擎看到全部交易，后面的引擎无条件覆盖前面的 |
| **liability 保护** | income 分类为 Wages/Centrelink 的交易对 liability 不可见 |
| **catch_all 自限** | 只匹配 `classification_status == unclassified` 的行 |
| **all_other_credit 自限** | 只处理 credit 交易，排除已分类行（但 `External Transfers` 可重新匹配） |

### 1.3 文本归一化公共函数

位于 `classification_core/text.py`：

```python
def clean_text(value) -> str:
    # 大写 + 保留字母数字 + 空格归一化
    # "Hello, World! 123" → "HELLO WORLD 123"
    text = str(value).upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return " ".join(text.split())
```

⚠️ **关键**：`clean_text` 只保留 `[A-Z0-9 ]`。这意味着：
- **标点符号被移除**（逗号、连字符、括号等全部消失）
- **keyword 匹配发生在归一化后的文本上**
- **regex 匹配可能发生在原始文本或归一化文本上，取决于引擎**

---

## 二、各引擎规则机制详解

### 2.1 initial_engine（优先级 1）— 商户名关键字匹配

#### 规则来源
- **CSV 文件**：`initial_engine/merchant_kb.csv`（~256 万商户，~3 万 keyword 变体）
- **CSV Schema**：`merchant_name, keywords, link, category, category_source, keyword_updated_at, category_updated_at`

#### 规则加载（`load_merchant_kb()`）

```python
# 分块读取（chunksize=100_000），避免内存溢出
chunks = pd.read_csv(kb_path, usecols=["merchant_name","keywords","category"],
                     chunksize=100_000, dtype="string")

对每个 chunk：
  1. 排除 category == "Financial Institutions" 的行（交给 liability 处理）
  2. keywords 用 | 分割，展开为多行（最多取前 50 个变体）
  3. keywords 通过 clean_text() 归一化
  4. 过滤 STOPWORDS（见下方）
  5. 去重后插入 Aho-Corasick 自动机：add_word(keyword, (keyword, merchant, category))
  6. make_automaton() 构建
```

#### 停用词过滤（STOPWORDS）
约 100 个常见银行术语被过滤（`CARD`, `VISA`, `PAYMENT`, `TRANSFER`, `FEE`, `INTEREST` 等）。这些词太泛，单独作为 keyword 会误匹配。

⚠️ **对 Auto_Rule_Extension 的影响**：
- 如果生成的 keyword 是停用词，会被 initial_engine 过滤掉，规则无效
- 多词短语中包含停用词仍然保留（如 "AUSSIE CARD SERVICES"）
- **必须用 `scripts/search_merchant.py` 查询 merchant_kb 中是否已存在该商户**

#### 文本预处理
```python
def _clean_transaction_text(value):
    text = clean_text(value)  # 大写 + 字母数字
    text = _CHANNEL_PREFIX_RE.sub("", text)  # 去掉支付通道前缀
    return text
```

**去除的通道前缀**（在匹配前从 text 开头移除）：
```
BILL PAY/BILL PAYMENT ...
VISA WDL/VISA PURCHASE/VISA DEBIT PURCHASE ...
EFTPOS DEBIT/EFTPOS WDL ...
MISCELLANEOUS DEBIT V...
DEBIT CARD PURCHASE ...
EFT Dep ...
```

⚠️ **关键发现**：这意味着如果交易文本是 `"DEBIT CARD PURCHASE WOOLWORTHS SUPERMARKET"`，引擎实际匹配的是 `"WOOLWORTHS SUPERMARKET"`。生成规则时不需要在 pattern 中包含这些前缀。

#### 匹配逻辑（`match_transactions()`）

```
对每笔交易的 cleaned text：
  1. 用 Aho-Corasick 自动机扫描 text → 所有命中
  2. 对每个命中做全词边界检查：
     - pos > 0 且 text[pos-1] != " " → 跳过（不是词边界）
     - end < text_len 且 text[end] != " " → 跳过
  3. 最长 keyword 优先（多个 keyword 命中时选最长的）
  4. 返回 (matched, counterparty=merchant_name, finv_category=category, ...)
```

#### 缓存机制
- 自动机在进程内全局缓存（`_cached_automaton`）
- 只构建一次，income_engine 等下游引擎可复用

#### 自我抑制（Self-Suppression）
在 `pipeline.py` 中，initial_engine 会**主动清除**自己对以下类别的分类：
- `Debt Collection`
- `Debt Consolidation`
- `Financial Institutions`

这意味着即使 `merchant_kb.csv` 中包含这些类别的商户，initial_engine 也不会输出它们——这些类别已移交给 liability/dishonour 引擎处理。Auto_Rule_Extension 生成 initial 规则时无需考虑这些类别。

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **keyword 必须是全词匹配**：`"WOOLWORTHS"` 不会匹配 `"WOOLWORTHSX"`，但会匹配 `"WOOLWORTHS SUPERMARKET"`
2. **keyword 归一化后只含 [A-Z0-9 ]**：标点符号全部丢失
3. **最长 keyword 胜出**：不要生成过短的 keyword（会被更长的覆盖）
4. **STOPWORDS 会被过滤**：不要生成纯停用词 keyword
5. **通道前缀会被去掉**：pattern 不需要包含 `DEBIT CARD PURCHASE` 等前缀
6. **这是唯一处理商户名的引擎**：所有商户名/品牌名规则都应优先给 initial_engine

---

### 2.2 transfer_engine（优先级 100）— 转账识别

#### 规则来源（8 个 CSV 文件）

| CSV 文件 | 用途 | Schema |
|----------|------|--------|
| `transfer_counterparty_rules.csv` | keyword → counterparty 映射 | `keyword, counterparty, match_type` |
| `transfer_external_high_confidence_rules.csv` | 高置信外部转账 regex | `priority, rule_name, category, pattern, dr_cr, description` |
| `transfer_external_medium_confidence_rules.csv` | 中置信外部转账 regex | 同上 |
| `transfer_internal_regex_rules.csv` | 内部转账 regex | `priority, rule_name, pattern, dr_cr, description` |
| `transfer_group_exclusion_patterns.csv` | 排除组（整个配对组排除） | `pattern` |
| `transfer_row_exclusion_patterns.csv` | 行排除 | `pattern` |
| `transfer_indicator_patterns.csv` | 转账指标检测 | `pattern` |
| `transfer_pairing_exclusions.csv` | 配对排除规则 | 多列 |

⚠️ **重要**：transfer_engine **只输出两个 category**：`Internal Transfer` 和 `External Transfers`。不能输出 Gambling、Entertainment 等其他分类。

#### 规则加载

```python
# 加载为模块级缓存（lazy load）
def _load_rules_csv(file_path) -> list[(rule_name, category, pattern, dr_cr_or_None)]:
    # 读取 priority, rule_name, category, pattern, dr_cr, description
    # 按 priority 排序
    rules.sort(key=lambda r: r[0])
    return [(name, cat, pat, dr) for _, name, cat, pat, dr in rules]

def _load_pattern_list(file_path) -> list[re.Pattern]:
    # 读取 pattern 列，编译为 re.compile(pattern, re.IGNORECASE)
```

#### 文本预处理
```python
def normalize_text(value):
    # 小写 + 空格归一化（只去多余空格，保留所有字符）
    return re.sub(r"\s+", " ", str(value).lower()).strip()
```

⚠️ **与 initial_engine 不同**：transfer_engine 使用**小写**，**保留特殊字符**。不会删除标点符号！

#### 匹配流程（`classify_transfers()`）

```
Step 1: 内部转账 — 配对检测
  - 按 (application_id, transaction_date, amount) 分组
  - 组内同时有 debit 和 credit → 候选
  - 排除组：gambling/lender 关键词（GROUP_EXCLUSION_PATTERNS regex）
  - 排除行：P2P 模式（ROW_EXCLUSION_PATTERNS regex）
  - 需要 transfer_indicator 匹配（TRANSFER_INDICATOR_PATTERNS regex）
  - → category = "Internal Transfer", confidence = "high"

Step 1.5: 内部转账 — regex 规则
  - 用 _match_rules() 对未分类行匹配 INTERNAL_TRANSFER_RULES
  - per_rule_exclusions：某些规则排除含 INTL-FEE 的行

Step 2: 外部转账 — regex 规则（先高置信 → 中置信）
  - HIGH_CONFIDENCE_RULES 先匹配 → confidence = "high"
  - MEDIUM_CONFIDENCE_RULES 后匹配 → confidence = "medium"
  - 支持 dr_cr 约束（只匹配 debit 或 credit 行）
  - → category = "External Transfers"

Step 2.5: Personal Osko 过滤
  - 如果 External Transfers + credit + "osko" + 含 6+ 位数字
  - 且后面跟人名（Title Case）→ 取消分类（不是真正的转账）

Step 3: 已知账户入金匹配
  - 从已分类 withdrawal 中提取已知账户号
  - 匹配 deposit 到这些已知账户 → External Transfers

Counterparty 推导：
  - ALL transfer 行都经过 counterparty keyword 匹配
  - transfer_counterparty_rules.csv：keywords（分号分隔）→ counterparty
  - 先匹配的规则胜出（CSV 行顺序）
  - 无匹配 → "Miscellaneous Funds Transfer"
```

#### `_match_rules()` 核心逻辑
```python
def _match_rules(df, rules, category_label):
    # 只处理 is_transfer_pred == 0 的剩余行
    # 对每条规则（name, pattern, dr_cr_constraint, confidence）：
    #   - text_norm.str.contains(pattern, regex=True) 向量化匹配
    #   - 检查 dr_cr 约束（如需要）
    #   - 匹配后立即从剩余池中移除 → 先匹配先得
```

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **正则匹配发生在小写+空格归一化的文本上**：字母数字外的字符仍然存在
2. **dr_cr 约束**：regex 规则可附带 dr_cr 方向约束，极大减少误匹配
3. **先匹配先得**：高 priority 的规则先匹配，匹配后该行不再被后续规则处理
4. **只能生成 Internal Transfer / External Transfers**：不能生成其他分类
5. **counterparty 用独立 keyword 规则**：不是 regex
6. **排除规则（exclusion）通常不自动生成**：这是安全防线
7. **配对排除规则**：`transfer_pairing_exclusions.csv` 支持 keyword 和 regex 两种 match_type

---

### 2.3 dishonour_engine（优先级 150）— 拒付检测

#### 规则来源
- **CSV 文件**：`dishonour_engine/resources/dishonour_rules.csv`
- **CSV Schema**：`rule_type, pattern, required_terms`
- **共享加载器**：`classification_core/rules.py` — `load_dishonour_style_rules()`

#### 规则加载
```python
def load_dishonour_style_rules(rules_file) -> list[(rule_type, pattern, required_terms)]:
    rules = []
    for row in csv.DictReader(f):
        rule_type = row["rule_type"].strip().lower()  # "keyword" 或 "regex"
        pattern = row["pattern"].strip()
        required_terms = row["required_terms"].split(";")  # 分号分隔的必须词列表
        rules.append((rule_type, pattern, required_terms))
    return rules
```

#### 匹配逻辑
```python
for rule_type, pattern, required_terms in rules:
    if rule_type == "keyword":
        # 用 re.escape(pattern) 转义后在原始 text 上做子串匹配（case=False）
        mask |= text_col.str.contains(re.escape(pattern), case=False, na=False)
    else:  # "regex"
        # required_terms：必须全部在 text（小写）中出现
        term_mask = all(text_col.str.lower().str.contains(term, na=False) for term in required_terms)
        # 且 regex pattern 匹配
        mask |= term_mask & text_col.str.contains(pattern, case=False, na=False, regex=True)
```

⚠️ **关键发现**：
- **keyword 匹配在原始 text 上做 `re.escape` 子串匹配**（不是清洁后的文本）
- **regex 匹配也在原始 text 上做**
- **required_terms 在 text.lower() 上做子串匹配**

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **所有匹配都在原始 text 上发生**（未经 clean_text 归一化）
2. **keyword 用 `re.escape`**：特殊字符（如 `.`）会被自动转义
3. **required_terms** 是 AND 逻辑：所有词都必须出现
4. **category 固定为 `Dishonours`**：无需在规则中指定
5. **dishonour 和 all_other_credit 共用同一个加载器**

---

### 2.4 income_engine（优先级 200）— 收入识别

#### 规则来源
- **规则文件**：`income_engine/resources/income_pattern_rules.csv`
- **配置文件**：`income_engine/resources/income_config.csv`
- **CSV Schema（规则）**：`pattern_group, pattern, match_type, description`

#### 规则加载
```python
def _load_pattern_rules(rules_file) -> dict[str, list[str]]:
    # 按 pattern_group 分组
    grouped = {}
    for row in csv.DictReader(f):
        group = row["pattern_group"].strip()      # strong_wage, centrelink, ...
        pattern = row["pattern"].strip()
        grouped.setdefault(group, []).append(pattern)
    return grouped
```

不同 pattern_group 的用途：
| group | 用途 |
|-------|------|
| `strong_wage` | 强工资信号（如 SALARY、PAYROLL）→ 触发 STRONG_WAGE_REGEX |
| `medium_income` | 中等收入信号（如 DIRECT CREDIT）→ 触发 MEDIUM_INCOME_REGEX |
| `repeat_employer_like` | 雇主名模式 → 触发 REPEAT_EMPLOYER_LIKE_REGEX |
| `repeat_employer_like_exclusion` | 排除伪雇主名 |
| `salary_packaging` | 工资打包 → 触发 SALARY_PACKAGING_REGEX |
| `centrelink` | Centrelink 福利 → 触发 CENTRELINK_REGEX |
| `self_employed_gig` | 自雇/零工 → 触发 SELF_EMPLOYED_GIG_REGEX |
| `wage_advance` | 工资预支（非收入）→ 触发 WAGE_ADVANCE_REGEX |
| `return_like` | 退货/退款 → 硬排除 |
| `hard_negative` | 强负向信号 |
| `soft_negative` | 弱负向信号（如 TRANSFER） |

#### 文本预处理
```python
# 使用 clean_text() — 大写 + 字母数字
out["text_clean"] = out["text"].apply(clean_text)
```

⚠️ **关键**：income_engine 使用 `clean_text()`（与 initial_engine 相同），引擎内所有的 regex 和 keyword 匹配都在归一化文本上进行。

#### 收入分类逻辑

不同于其他引擎的简单"匹配→分类"，income_engine 是**多阶段决策树**：

```
阶段 1：硬门槛（Hard Gates）
  - is_credit == 1
  - amount >= MIN_NORMAL_WAGE_AMOUNT (100)
  - has_hard_negative_keyword == 0
  - is_possible_wage_amount == 1 (100~20000)

阶段 2：基础工资规则（Base Wage Rules）
  按优先级依次判断 ~10 条复合规则，每条规则组合多个条件：
  - rule_strong_wage_keyword：强工资关键词 + 无软负向 + 通过硬门槛
  - rule_transfer_strong_wage_keyword：有 TRANSFER 类软负向 + 有强关键词 + 有效 payer_key
  - rule_medium_income_high_repeat：中等关键词 + 高频重复 + 有效 payer_key
  - rule_recurring_payer_behavior：无关键词 + 但 payer 高频且金额规律
  - ...更多规则

阶段 3：小金额历史覆盖（Small Amount History Override）
  - 金额 < 100 但该 payer 历史上被检测到 2+ 次工资
  - 且有强工资关键词 + 无硬排除

阶段 4：软负向关联到已知工资 payer（Soft Negative Alias）
  - 有软负向词 + 与已知工资 payer 的 token 重叠 >= 2
  - 用于提升 TRANSFER 类描述的工资识别

最终：is_wages_pred = base_wages_pred | override | alias | ...
```

#### 金额阈值（来自 income_config.csv）
```python
MIN_NORMAL_WAGE_AMOUNT = 100      # 低于此金额的不可能是正常工资
COMMON_WAGE_AMOUNT_MIN = 300      # 常见工资范围下限
COMMON_WAGE_AMOUNT_MAX = 10000    # 常见工资范围上限
POSSIBLE_WAGE_AMOUNT_MAX = 20000  # 可能工资范围上限
```

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **不是纯文本匹配**：收入分类严重依赖金额阈值和行为模式（频率、规律性）
2. **单条规则极少直接生效**：需要复合条件（关键词 + payer_key + 金额 + 频率）
3. **新增 regex pattern 只是"信号"**：不是决定性规则
4. **pattern_group 决定了 pattern 在决策树中的位置和权重**
5. **应优先考虑 `strong_wage` 和 `centrelink` group**，这两个 group 的 pattern 触发最确定
6. **金额范围极重要**：COMMON_WAGE_AMOUNT_MIN/MAX，超出范围的不会分类为工资
7. **payer_key 提取**：从文本中提取 3+ 字符的大写 token，排除 PAYER_STOP_WORDS

---

### 2.5 liability_engine（优先级 300）— 负债/贷款识别

#### 规则来源（7 个规则 CSV + 1 个配置 CSV）

| CSV 文件 | Schema |
|----------|--------|
| `counterparty_keyword_rules.csv` | `keyword, counterparty, product_type, match_type` |
| `credit_card_rules.csv` | `priority, account_type, dr_cr, bank, match_type, keyword, min_prefix_len, counterparty, product_type, exclude_pattern` |
| `home_loan_car_loan_rules.csv` | `rule_id, target_field, rule_name, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled` |
| `debt_collection_rules.csv` | `counterparty, product_type, rule_id, match_type, keyword` |
| `debt_consolidation_rules.csv` | `keyword, counterparty, product_type, match_type` |
| `dishonours_rules.csv` | `rule_type, pattern, required_terms` |
| `overdrawn_rules.csv` | `counterparty, rule_id, match_type, pattern, note` |
| `bnpl_maximum_limits.csv` | BNPL 限额（配置，不用于匹配） |

#### 执行流水线（`run_pipeline()`）
```python
1. apply_counterparty_rules()        → 对手方→产品类型映射
2. apply_home_loan_car_loan_flags()  → 房贷/车贷标识
3. apply_credit_card_rules()         → 信用卡还款
4. apply_dishonour_rules()           → 拒付（liability 视角）
5. apply_special_rules()             → 硬编码特殊规则
6. apply_overdrawn_flag()            → 透支
7. apply_debt_collection_flag()      → 债务催收
8. apply_debt_consolidation_flag()   → 债务整合
9. identify_streams()                → 流标识
10. add_finv_category()              → 最终分类
11. apply_generic_loan_catchall()    → 兜底通用贷款
```

#### 关键匹配特性

**`counterparty_keyword_rules.csv`**：
```python
# keyword 匹配 → counterparty + product_type
# match_type: "keyword" 或 "prefix"
# prefix 模式下 keyword 作为前缀匹配（keyword "CBA" 匹配 "CBA HOME LOAN"）
```

**`credit_card_rules.csv` 等**：
- 支持 `priority` 排序
- 支持 `dr_cr` 方向约束
- 支持 `account_type` / `bank` 限定
- 支持 `min_prefix_len`（金额前缀最小长度匹配）
- 支持 `exclude_pattern`（排除模式，regex）

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **多文件引擎**：规则分散在 7 个 CSV 中，必须通过 `target_file` 指定写入哪个文件
2. **Schema 差异大**：每个子文件的列结构不同，不能混淆
3. **counterparty_keyword 是第一步**：新增贷款机构优先写入此文件
4. **match_type = "prefix"**：特殊匹配模式，用前缀而非精确/正则
5. **exclude_pattern**：一个规则的命中可以被另一个 regex 排除
6. **min_prefix_len**：用于金额字段的前缀匹配（提取金额开头的 N 位数字）
7. **特殊规则（hardcoded）**：`special_rules.py` 包含硬编码逻辑
   - Credit Corp 子产品细化（检测文本中的 "wizit"/"pup"/"ccc" 关键词）
   - Cash Converters 零售 vs 贷款区分（检测 POS 终端标记/商店位置 → 零售，非贷款）

---

### 2.6 all_other_credit_engine（优先级 400）— 杂项入账

#### 规则来源
- **CSV 文件**：`all_other_credit_engine/resources/all_other_credit_rules.csv`
- **CSV Schema**：`rule_type, pattern, required_terms`
- **加载器**：同 dishonour — `load_dishonour_style_rules()`

#### 匹配逻辑
```python
# 1. 只处理 dr_cr == "credit" 的行
# 2. 排除已分类行（但 External Transfers 可重新匹配）
# 3. 只使用 keyword 规则（忽略 regex 规则）：
for rule_type, pattern, _required_terms in rules:
    if rule_type == "keyword":
        mask |= text_col.str.contains(re.escape(pattern), case=False, na=False)
```

⚠️ **关键发现**：all_other_credit 虽然和 dishonour 共用加载器，但**实际只执行 keyword 类型的规则**。regex 规则被加载但不会被匹配！

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **只用 keyword 规则**：不要为 all_other_credit 生成 regex 规则
2. **匹配在原始 text 上发生**（不做 clean_text）
3. **用 `re.escape` 转义**：特殊字符安全
4. **category 固定为 `All Other Credits`**

---

### 2.7 fee_engine（优先级 500）— 费用识别

#### 规则来源
- **CSV 文件**：`fee_engine/resources/fee_classification_rules.csv`
- **CSV Schema**：`priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description`

#### 规则加载
⚠️ **目前 fee_engine 使用硬编码规则**（`FEE_RULES` Python 列表），不从 CSV 动态加载！

但这可能是历史遗留——CSV 文件 `fee_classification_rules.csv` 存在于 `resources/` 下。如果未来改为 CSV 加载，schema 应与硬编码规则结构兼容。

#### 文本预处理
```python
def normalize_text(value):
    # 保留原始大小写，只做空格归一化（与 transfer_engine 相同）
    return re.sub(r"\s+", " ", str(value)).strip()
```

⚠️ **与其他引擎不同**：fee_engine **保留原始大小写**！

#### 匹配逻辑
```python
class FeeClassifier:
    def __init__(self):
        self.rules = [(name, category, re.compile(pattern), counterparty)
                      for name, category, pattern, counterparty in FEE_RULES]
    
    def predict(self, text):
        for rule_name, category, pattern, counterparty in self.rules:
            if pattern.search(text):  # 在归一化文本上做 regex search
                return FeePrediction(is_fee=True, category=category, ...)
        return FeePrediction(is_fee=False, ...)
# 按规则列表顺序，先匹配先得
```

#### 零金额排除
```python
_AMOUNT_ZERO_REJECT_RULES = {规则名列表}
# 如果 amount ≈ 0 且匹配到这些规则 → 取消预测
# 这些是信息性行（fee waived / included / $0 informational）
```

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **正则匹配，保留原始大小写**：意味着 `^MONTHLY\s+FEE$` 能匹配 "MONTHLY FEE"，但不能匹配 "monthly fee"
2. **先匹配先得**：规则的顺序决定优先级
3. **默认 category 是 `Fees`**，但 overdrawn 类的 category 是 `Overdrawn`
4. **zero_amount_reject** 机制：某些规则在金额为 0 时被忽略
5. **支持 $0 费用行**：如果规则名不在 `_AMOUNT_ZERO_REJECT_RULES` 中，即使金额为 0 也会被匹配

---

### 2.8 catch_all_engine（优先级 999）— 兜底关键词

#### 规则来源
- **CSV 文件**：`catch_all_engine/resources/catch_all_rules.csv`
- **CSV Schema**：`rule_name, category, pattern, match_type, confidence`

#### 规则加载
```python
def _load_rules(rules_file) -> list[(rule_name, category, pattern, match_type, confidence)]:
    rules = []
    for row in csv.DictReader(f):
        rules.append((rule_name, category, pattern, match_type, float(confidence)))
    # 按 confidence 降序排序
    rules.sort(key=lambda r: r[4], reverse=True)
    return rules
```

#### 匹配逻辑
```python
# 1. 只匹配 classification_status == unclassified 的行
# 2. 文本用 clean_text() 归一化（大写 + 字母数字）
# 3. 对每行，遍历所有规则（按 confidence 降序）：
for rule_name, category, keyword, match_type, confidence in rules:
    if confidence <= best_conf:  # 已找到更高 confidence 的匹配
        continue  # 跳过（优化：规则已按 confidence 排序）
    
    if match_type == "keyword":
        # str.find(keyword) + 全词边界检查
        if pos > 0 and text[pos-1] != " ": continue
        if end < text_len and text[end] != " ": continue
    else:  # regex
        re.search(keyword, text)
    
    # 最高 confidence 胜出
```

#### ⚠️ 对 Auto_Rule_Extension 的关键启示

1. **keyword 匹配在 clean_text() 后的文本上**（全大写 + 字母数字）
2. **全词边界检查**：`"BAKERY"` 不会匹配 `"BAKERYX"`
3. **最高 confidence 胜出**（不是先匹配先得）
4. **只匹配未分类交易**：不会与前面引擎冲突
5. **可以输出任意 category**：不像其他引擎受限
6. **confidence 范围**：通常在 0.70-0.85，极高特异性的可到 0.90
7. **适合通用关键词**：`"RESTAURANT"→"Dining Out"`, `"PHARMACY"→"Health"`
8. **不适合商户名**：商户名应给 initial_engine

---

## 三、文本归一化对比总结

| 引擎 | 归一化方式 | 大小写 | 特殊字符 |
|------|-----------|--------|---------|
| **initial** | `clean_text()` → 通道前缀去除 | 大写 | 移除（只剩 [A-Z0-9 ]） |
| **transfer** | 空格归一化 `.lower()` | 小写 | 保留 |
| **dishonour** | 原始 text | 原始（匹配时 case=False） | 保留（keyword 用 `re.escape`） |
| **income** | `clean_text()` | 大写 | 移除（只剩 [A-Z0-9 ]） |
| **liability** | 取决于子模块 | 混合 | 混合 |
| **all_other_credit** | 原始 text | 原始（匹配时 case=False） | 保留（keyword 用 `re.escape`） |
| **fee** | 空格归一化 | **保留原始** | 保留 |
| **catch_all** | `clean_text()` | 大写 | 移除（只剩 [A-Z0-9 ]） |

⚠️ **核心规律**：
- **商户/关键词引擎**（initial, income, catch_all）→ `clean_text()` 大写归一化
- **描述匹配引擎**（transfer, dishonour, all_other_credit, fee）→ 保留原始文本/小写

---

## 四、匹配方式对比总结

| 引擎 | 匹配方式 | 多匹配策略 |
|------|---------|-----------|
| **initial** | Aho-Corasick 多模式 + 全词边界 | 最长 keyword 优先 |
| **transfer** | pandas `str.contains` 向量化 regex | 先匹配先得（按 priority 排序） |
| **dishonour** | `str.contains` (keyword: re.escape / regex: 原始) | 任意匹配（OR） |
| **income** | 多阶段复合决策树 | 多信号综合判定 |
| **liability** | 多子模块 pipeline | 先匹配先得（多模块各自排序） |
| **all_other_credit** | `str.contains` (keyword: re.escape) | 任意匹配（OR） |
| **fee** | `re.search` 逐行 | 先匹配先得（按规则列表顺序） |
| **catch_all** | `str.find` 或 `re.search` + 全词边界 | 最高 confidence 优先 |

---

## 五、Auto_Rule_Extension 生成规则的关键指南

### 5.1 文本归一化对齐

| 如果规则目标是 | 匹配文本环境 | pattern 应写成 |
|-------------|------------|---------------|
| initial_engine keyword | `clean_text()` 大写 [A-Z0-9 ] | 大写字母数字，如 `WOOLWORTHS SUPERMARKET` |
| catch_all keyword | `clean_text()` 大写 [A-Z0-9 ] | 大写字母数字，如 `RESTAURANT` |
| catch_all regex | `clean_text()` 大写 [A-Z0-9 ] | 大写字母数字正则 |
| transfer regex | 小写原始文本 | 小写正则，如 `fast transfer from` |
| dishonour keyword | 原始文本 | 原始大小写，如 `Dishonour` |
| fee regex | 保留大小写原始文本 | 保留大小写正则，如 `^MONTHLY\s+FEE$` |

### 5.2 规则类型选择

- **具体商户名 → initial_engine**（keyword，通过 merchant_kb）
- **通用消费词 → catch_all**（keyword 优先，regex 次之）
- **转账描述 → transfer_engine**（regex）
- **费用描述 → fee_engine**（regex）
- **拒付 → dishonour_engine**（keyword 或 regex）
- **收入信号词 → income_engine**（regex pattern，归类到合适的 pattern_group）
- **贷款机构 → liability_engine counterparty_keyword_rules.csv**
- **退款/返现 → all_other_credit_engine**（仅 keyword）

### 5.3 最容易被忽略的机制

1. **initial_engine 的通道前缀去除**：text 中的 `DEBIT CARD PURCHASE ` 等前缀在匹配前被 strip
2. **income_engine 不是简单关键词匹配**：需要金额阈值 + payer_key + 频率模式
3. **all_other_credit 只认 keyword**：regex 规则被加载但不会匹配
4. **fee_engine 保留原始大小写**：`^MONTHLY\s+FEE$` ≠ `^monthly fee$`
5. **transfer regex 是小写的**：与其他引擎归一化方式不同
6. **catch_all 多个匹配时 confidence 高的胜出**：不是先匹配先得
7. **keyword 匹配在有些引擎是子串**（dishonour/all_other_credit），有些是全词（initial/catch_all）

### 5.4 检查清单（生成每条规则时）

- [ ] 目标引擎的匹配发生在什么归一化后的文本上？
- [ ] keyword 规则：是子串匹配还是全词匹配？
- [ ] regex 规则：大小写敏感吗？需要 `^`/`$` 锚定吗？
- [ ] 规则是否可能被引擎内部的排除逻辑（STOPWORDS、exclusion patterns）过滤掉？
- [ ] 如果目标引擎有 priority 机制，新规则的 priority 应设为多少？
- [ ] income 引擎的规则：金额阈值是否满足？pattern_group 是否合适？
