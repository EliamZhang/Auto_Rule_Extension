# Auto Rule Extension

基于 Claude Code 的智能规则维护系统，为 finv_category_V2 交易分类流水线的 8 个分类引擎自动发现并补充规则。

## 项目目标

finv_category_V2 的各引擎规则库仅基于小样本人工提炼。当新的百万级交易数据到来时，新出现的交易模式无法被现有规则覆盖。本系统通过 **统计层 + 智能层 + 人工审核层** 的三层架构，实现规则的半自动化发现、生成、验证和入库。

## 核心设计原则

- **宁可漏判不要误判**：新规则必须保守，优先保证 precision 而非 recall
- **人工审核是必须环节**：所有规则变更必须经过人工确认后才能写入
- **不修改 finv_category_V2 代码**：只追加 CSV 规则数据，不改变任何引擎逻辑
- **保持规则风格一致**：新规则必须与各引擎已有规则的格式、命名风格、confidence 水平保持一致
- **完全独立项目**：不 import finv_category_V2 的任何模块，自行维护 raw/ 副本

## 与 finv_category_V2 的关系

本系统是**完全独立项目**：
- `raw/` 目录维护了各引擎规则 CSV 的本地副本（初始从 finv_category_V2 复制）
- 确认的规则先写入本地 `raw/`，再通过 `--sync_to` 同步到 finv_category_V2
- 不需要 finv_category_V2 的 Python 环境或模块
- 输入数据来自 finv_category_V2 跑完流水线后导出的 .xlsx 分类报告

## 目标项目架构

### finv_category_V2 的 8 个引擎

| 优先级 | engine_id | 规则文件 | 匹配方式 | 文本预处理 | 规则数 |
|--------|-----------|----------|----------|-----------|--------|
| 1 | initial | `merchant_kb.csv` | Aho-Corasick 全词 | `clean_text()` 大写 | ~9,000商户/~30K keywords |
| 100 | transfer | 8 个 CSV | regex(小写) + keyword(子串) | `lower().strip()` | ~150 |
| 150 | dishonour | `dishonour_rules.csv` | keyword(re.escape) + regex | 无(flags) | ~15 |
| 200 | income | `income_pattern_rules.csv` + `income_config.csv` | regex + 金额阈值 + 行为特征 | `clean_text()` 大写 | ~106 |
| 300 | liability | 8 个 CSV（多格式） | keyword(全词\b) + regex 双 tier | `upper().strip()` | ~500+ |
| 400 | all_other_credit | `all_other_credit_rules.csv` | keyword(re.escape) 仅入账 | 无(flags) | ~30 |
| 500 | fee | `fee_classification_rules.csv` | regex(大小写敏感,^锚定) | 仅压缩空格 | ~87 |
| 999 | catch_all | `catch_all_rules.csv` | keyword(全词) + regex, 最高conf胜出 | `clean_text()` 大写 | ~280 |

### 每个引擎的代码级规则使用详解

以下内容来自对 `finv_category_V2` 各引擎源码的逐行分析，描述每个引擎**实际如何加载、预处理、匹配和应用规则**。理解这些细节对于生成能正确工作的规则至关重要。

---

#### 1. initial_engine (优先级 1) — 商户名 → 分类

**源码位置**: `initial_engine/domain/classification.py` (423行) + `initial_engine/pipeline.py`

##### 规则加载 (`load_merchant_kb`)
```
merchant_kb.csv → chunk-read 100k行/批 → 展开 pipe-separated keywords
→ clean_text() → 过滤 STOPWORDS → 去重 → 构建 Aho-Corasick 自动机
```

关键代码细节：
- **Chunk 读取**: `pd.read_csv(chunksize=100_000)`, 仅读取 `merchant_name, keywords, category` 三列
- **Keyword 展开**: `keywords.str.split("|").str[:_MAX_VARIANTS_PER_MERCHANT]` — 每个商户最多 50 个 keyword 变体
- **排除**: 直接丢弃 `category == "Financial Institutions"` 的行（由 liability/dishonour 处理）
- **Stopwords 过滤** (`_STOPWORDS`): 70 个泛化银行/支付术语，**作为独立 keyword 时被丢弃**，但包含它们的多词短语保留。完整列表见源码 L222-331，关键类别：
  - 卡片类: CARD, CARDS, VISA, MASTERCARD, AMEX, EFTPOS, ATM, PAYWAVE
  - 支付通道: BILL, BPAY, OSKO, PAYID, NPP, DIRECT, DEBIT, CREDIT
  - 交易类: PAYMENT, PURCHASE, TRANSFER, WITHDRAWAL, DEPOSIT, REFUND
  - 费用类: FEE, FEES, INTEREST, OVERDRAWN, SURCHARGE
  - 通用后缀: LIMITED, GROUP, HOLDINGS, SERVICES, COMPANY 等
- **Keyword 清洗**: `clean_text()` → 只保留 `[A-Z0-9 ]`，转大写，压缩空格
- **去重**: 先 chunk 内去重（`drop_duplicates`），再跨 chunk 去重（dict key）
- **自动机构建**: `ahocorasick.Automaton()` → `add_word(kw, (kw, merchant, cat))` → `make_automaton()`
- **模块级缓存**: `_cached_automaton` — 同一进程内只加载一次，income_engine 也复用此缓存

##### 文本预处理 (`_clean_transaction_text`)
```
原始 text → clean_text() → 去除支付通道前缀 → 用于匹配
```

- `clean_text()`: 大写 + 只保留 `[A-Z0-9 ]` + 压缩空格（与 keyword 清洗相同）
- `_CHANNEL_PREFIX_RE`: 去除交易文本开头的支付通道前缀，包括：
  - `BILL PAY(MENT) ...`
  - `VISA PURCHASE ...`, `VISA DEBIT ...`, `VISA WDL ...`, `VISA CREDIT ...`
  - `EFTPOS DEBIT ...`, `EFTPOS WDL ...`
  - `MISCELLANEOUS DEBIT Vxxxx xx xx ...`
  - `DEBIT CARD PURCHASE ...`
  - `EFT Dep ...`
- **重要**: channel prefix 只从 transaction text 去除，**不影响 keyword** — 一个叫 "Bill Pay Services" 的商户其 keyword 仍正常插入

##### 匹配逻辑 (`match_transactions` / `_classify_one`)
```
清洗后的 text → Aho-Corasick.iter() → 逐匹配项检查:
  1. 全词匹配（前后必须是空格或字符串边界）
  2. 最长 keyword 胜出
→ 返回 (matched, counterparty=merchant_name, finv_category=category, keyword, rule_id, reason)
```

- 使用 `str.find()` 确定位置（而非依赖 ahocorasick 的 end_pos，因版本语义不一致）
- 全词检查: `pos > 0 and text[pos-1] != " "` 和 `end < len(text) and text[end] != " "`
- 最长优先: 当多个 keyword 匹配时，选 `len(kw)` 最大的

##### Pipeline 后处理 (`initial_engine/pipeline.py`)
匹配后还会清除以下分类（因为 ownership 属于其他引擎）：
- `finv_category == "Debt Collection"` → 清空
- `finv_category == "Debt Consolidation"` → 清空
- 这两类由 liability_engine 负责

##### 对规则生成的影响
- **keyword 必须是 clean_text 后的形式**（大写，仅 `[A-Z0-9 ]`），否则永远无法匹配
- **不要添加 STOPWORDS 中的词作为独立 keyword** — 它们会被自动过滤
- **keyword 不需要考虑 channel prefix** — 代码已自动剥离
- **每个商户最多 50 个变体** — 超过的被截断
- **不要给 category="Financial Institutions" 的商户添加规则** — 整行在加载时被丢弃
- **全词匹配**: keyword "WOOLWORTHS" 匹配 "WOOLWORTHS SUPERMARKET" 但不匹配 "WOOLWORTHSGROUP"
- **最长匹配**: 如果 "COLES" 和 "COLES EXPRESS" 都匹配，后者胜出
- **counterparty 被设为 merchant_name**，不是 keyword

---

#### 2. transfer_engine (优先级 100) — 转账识别

**源码位置**: `transfer_engine/domain/classification.py` (669行) + `transfer_engine/domain/transfer_rules.py` (104行) + `transfer_engine/domain/transfer_counterparty.py` (77行)

**核心约束**: transfer_engine **只输出两个 category**: `Internal Transfer` 和 `External Transfers`。不输出 Gambling、Entertainment 等其他分类。

##### 规则文件及加载方式

| 文件 | 加载函数 | 格式 | 用途 |
|------|---------|------|------|
| `transfer_external_high_confidence_rules.csv` | `_load_rules_csv()` | CSV → `list[(priority, rule_name, category, pattern, dr_cr)]` | 高置信外部转账 regex |
| `transfer_external_medium_confidence_rules.csv` | `_load_rules_csv()` | 同上 | 中置信外部转账 regex |
| `transfer_internal_regex_rules.csv` | `_load_rules_csv()` | 同上（但 category 栏被忽略） | 内部转账 regex |
| `transfer_counterparty_rules.csv` | `load_counterparty_rules()` | keyword(semicolon分隔), counterparty, match_type | 转账对手方识别 |
| `transfer_indicator_patterns.csv` | `_load_pattern_list()` | pattern 列 → `list[re.Pattern]` | 转账指标检测 |
| `transfer_group_exclusion_patterns.csv` | `_load_pattern_list()` | 同上 | 配对排除（组级别） |
| `transfer_row_exclusion_patterns.csv` | `_load_pattern_list()` | 同上 | 行排除（单行级别） |
| `transfer_pairing_exclusions.csv` | `load_exclusion_rules()` | keyword, match_type, exclusion_reason, priority | P2P 配对排除 |

##### 执行流水线 (`classify_transfers`)

```
Step 1:  配对检测 (pairing) → Internal Transfer
         条件: 同 (application_id, transaction_date, amount) 组内同时有 debit 和 credit
               + 行通过 transfer_indicator 检测
               + 组不匹配 exclusion 关键词（gambling/lender）
Step 1.5: 内部转账 regex → Internal Transfer
         规则来自 transfer_internal_regex_rules.csv，按 priority 排序
         特殊排除: "internal_anz_funds_tfer" 和 "internal_internet_banking" 排除含 INTL-FEE 的文本
Step 2:   外部转账 regex → External Transfers
         先 high_confidence 规则（全部），再 medium_confidence 规则（全部），各按 priority 排序
Step 2.5: 过滤个人 Osko → 取消分类
         条件: credit + osko + (无6位以上数字 或 有数字+人名模式)
Step 3:   已知账户匹配 → External Transfers
         从已匹配的 withdrawal 中提取账户号，匹配 unclassified 的 internet deposit
Step 4:   对手方提取 → counterparty 列
         对 ALL transfer 行做 keyword 匹配，第一匹配胜出，无匹配 → "Miscellaneous Funds Transfer"
```

##### 匹配细节 (`_match_rules`)
- **全部向量化**: 使用 `pd.Series.str.contains()` — 无 Python 循环
- **dr_cr 约束**: `dr_cr_constraint` 不为 None 时，只在匹配的 dr_cr 方向生效
- **Per-rule 排除**: 特定规则可附带排除 regex（如 INTL-FEE）
- **第一匹配胜出**: 规则按 priority 排序，顺序执行，已匹配的行被 `remaining_mask` 排除

##### 文本预处理 (`normalize_text`)
```python
# 与 initial_engine 完全不同 — 保留原样但小写
re.sub(r"\s+", " ", str(value).lower()).strip()
```
**注意**: transfer 用小写，initial 用大写 clean_text！

##### 对手方提取 (`_derive_counterparty`)
- 文本: `text.upper()` + 压缩空格
- 所有 transfer 行都经过对手方匹配（Internal 和 External 一样）
- keyword 用简单的 `str.contains(kw, regex=False)` — **不是全词匹配**，是子串匹配
- CSV 行顺序 = 优先级，第一匹配胜出
- 默认值: `"Miscellaneous Funds Transfer"`

##### 配对排除 (`transfer_rules.py` / `ExclusionRule`)
- keyword 模式: 分号分隔，`str.contains(kw, regex=False)` 子串匹配
- regex 模式: 预编译 `re.compile(pattern, re.IGNORECASE)`
- 按 priority 降序排列
- 用于阻止特定文本被配对为 Internal Transfer

##### 对规则生成的影响
- **regex 规则用小写**（text 被 normalize 为小写）
- **transfer 只输出两个 category**: Internal Transfer / External Transfers — 不要尝试添加其他分类
- **外部转账规则需要指定 category 列**（固定为 "External Transfers"）
- **dr_cr 列可选**: 留空表示匹配所有方向，"debit" 或 "credit" 限制方向
- **priority 决定匹配顺序**: 高 priority 先匹配，已匹配的行不再被后续规则处理
- **对手方规则是子串匹配不是全词** — "OSKO" 也会匹配 "OSKOPAYMENT"
- **不要为赌博/博彩平台生成 transfer 规则** — 它们不是转账
- **exclusion/indicator 文件通常不需要自动生成**

---

#### 3. dishonour_engine (优先级 150) — 拒付检测

**源码位置**: `dishonour_engine/engine.py` (66行) + `classification_core/rules.py`

##### 规则加载 (`load_dishonour_style_rules`)
```python
# CSV schema: rule_type, pattern, required_terms
# required_terms 是分号分隔的，转为小写 list
rules = [(rule_type, pattern, [term1, term2, ...]), ...]
```

##### 匹配逻辑（全部向量化）
- **keyword 模式**: `text_col.str.contains(re.escape(pattern), case=False)` — 注意用了 `re.escape`！keyword 中的特殊字符被转义
- **regex 模式**: 先检查所有 `required_terms` 都在 `lower_text` 中，再 `text_col.str.contains(pattern, regex=True)`
- 多个规则用 `|=` 累积 mask（OR 逻辑）
- 输出: `finv_category="Dishonours"`, `counterparty="-"`, `classification_rule_id="dishonour:generic"`

##### 重要：rule_type 区分
- `rule_type=keyword`: pattern 作为**纯文本子串**（被 `re.escape` 处理），不是正则
- `rule_type=regex`: pattern 作为正则表达式

##### 对规则生成的影响
- **keyword 模式是子串匹配**（不要求全词），且大小写不敏感
- **keyword 中的特殊字符会被自动转义** — 因此 "." "$" 等不需要额外处理
- **required_terms 是 AND 条件**: 所有 term 都必须在 text 中出现（小写子串）
- **regex 模式时 required_terms 也是必须全部满足** — 可用于增加特异性

---

#### 4. income_engine (优先级 200) — 收入识别

**源码位置**: `income_engine/domain/classification.py` (926行) + `income_engine/pipeline.py`

⚠ **这是最复杂的引擎**，不仅依赖文本匹配，还依赖大量行为特征（金额分布、时间规律、付款方历史）。

##### 规则加载

**income_pattern_rules.csv** — 数据驱动的模式定义:
```
CSV各列: pattern_group, pattern, match_type, description
→ _load_pattern_rules() 返回 Dict[pattern_group → List[regex_string]]
```
当前 pattern_group 分类：
| group | 含义 | 影响 |
|-------|------|------|
| `strong_wage` | 强工资信号 | 直接触发 strong_wage_keyword 规则 |
| `medium_income` | 中等收入信号 | 需结合重复/稳定性 |
| `repeat_employer_like` | 类似雇主的重复付款方 | 需稳定金额+常见工资额 |
| `repeat_employer_like_exclusion` | 排除以上匹配的 | 减少误判 |
| `salary_packaging` | 工资打包 | 直接触发 salary_packaging |
| `centrelink` | 政府福利 | 直接触发 centrelink |
| `self_employed_gig` | 自雇/零工 | 需无 gig_exclusion |
| `wage_advance` | 工资预支 | 识别为非收入 |
| `return_like` | 退款类 | 归入 hard_negative |
| `hard_negative` | 硬排除 | 直接阻止分类为收入 |
| `soft_negative` | 软排除 | 阻止 base_wages 但允许 alias 规则 |

**income_config.csv** — 阈值配置:
```
config_key, config_value, config_type
```
关键默认值：
- `MIN_NORMAL_WAGE_AMOUNT = 100`
- `COMMON_WAGE_AMOUNT_MIN = 300`, `COMMON_WAGE_AMOUNT_MAX = 10000`
- `POSSIBLE_WAGE_AMOUNT_MAX = 20000`
- `STABLE_AMOUNT_CV_MAX = 0.35`
- `HIGH_REPEAT_PAYER_COUNT_MIN = 4`
- `VERY_HIGH_REPEAT_PAYER_COUNT_MIN = 8`
- `REGULAR_GAP_RANGES`: 周(6-8天), 双周(13-16天), 月(27-33天)

##### 执行流水线

```
1. prepare_input: 合并 Unnamed 列到 text, 删除受限列 (trx_type, third_party 等)
2. add_wages_features:
   a. add_basic_features: 日期解析, 金额数值化, clean_text, 12 种 pattern 匹配计数
   b. add_payer_history_features: payer_key 提取, 时间间隔, 规律性, 稳定性
3. apply_wages_rules:
   a. add_hard_gate_flags: 4 个硬门控 (credit, amount>=100, no hard_negative, possible_wage_amount)
   b. add_base_wage_rules: 8 条基础工资规则
   c. add_small_amount_history_override: 小金额但有工资历史 → 覆盖
   d. add_soft_negative_alias_to_known_wage_payer_rule: 软排除但 payer token 重叠 ≥2 → 覆盖
4. add_income_type_rules: 4 级分类 (salary_packaging > centrelink > salary_payg > self_employed_gig)
5. _add_kb_counterparty: 复用 initial_engine 的自动机查找商户名
6. add_income_streams: 按 payer 分组生成 stream_id
```

##### Payer Key 提取 (`make_payer_key`)
```python
1. 移除日期: \b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b
2. 移除长数字: \b\d{5,}\b
3. 提取所有 ≥3 个大写字母的 token
4. 过滤 PAYER_STOP_WORDS (40个: DIRECT, CREDIT, PAYMENT, TRANSFER, FROM, TO, ...)
5. 取前 4 个 token 拼接
```
**PAYER_STOP_WORDS** (完整 40 个): DIRECT, CREDIT, DIR, DEPOSIT, SALARY, PAYROLL, WAGE, WAGES, PAY, PAYMENT, PAYMENTS, TRANSFER, TRANS, FROM, TO, REF, REFERENCE, ONLINE, INTERNET, EFT, DEP, OSKO, VISA, CARD, PURCHASE, DEBIT, MISCELLANEOUS, BPAY, WITHDRAWAL, ATM, TRNS, ACC, ACCOUNT, LINKED, AU, AUS, THE, AND, PTY, LTD, LIMITED, PACKAGING, CENTRELINK, CENTRE, LINK, SERVICES, AUSTRALIA, GOV, GOVERNMENT, RETURN, VALUE, DATE, FAST, NPP, THANK, YOU, RECEIVED, MAIN, JAN-DEC (月份缩写)

##### 工资检测核心规则 (13 条)
1. `strong_wage_keyword`: credit + 无软排除 + strong_wage 模式 — 最直接
2. `transfer_strong_wage_keyword`: 有软排除 + 无 wage_advance + strong_wage + payer_key + possible_wage_amount
3. `transfer_with_wage_signal`: 同上但有 repeat + (regular_cycle 或 stable_amount)
4. `repeat_employer_like_payment`: repeat_employer_like + stable_amount + common_wage_amount
5. `soft_negative_alias_to_known_wage_payer`: 软排除但 token overlap ≥2 + very_high_repeat(≥8)
6. `direct_credit_with_repeat`: medium_income + payer + repeat + (regular 或 stable)
7. `medium_income_high_repeat`: medium_income + high_repeat(≥4) + possible_wage_amount
8. `stable_payer_without_keywords`: 无任何 wage keyword + stable_repeat(≥6) + stable + common_wage
9. `recurring_payer_behavior`: no keyword + common_wage + repeat + regular + stable — 纯行为
10. `small_amount_wage_history_override`: amount<100 + strong_wage + 该 payer 已有 ≥2 次工资
11. `small_amount_alias_to_known_wage_payer`: 软排除 + very_high_repeat + token overlap ≥2
12. `small_amount_medium_income_high_repeat`: amount<100 + medium_income + high_repeat + regular_cycle
13. `small_amount_same_known_wage_payer`: amount<100 + medium_income + high_repeat + token overlap ≥3

##### finv_category 映射
- `salary_packaging`, `salary_payg`, `self_employed_gig` → `"Wages"`
- `centrelink` → `"Centrelink"`
- `non_income` → `""` (空)
- `wage_advance` → `known_non_income_type_pred="wage_advance"`（非收入）

##### 对规则生成的影响
- **所有规则都是 regex**（match_type 列虽存在但代码只用 regex）
- **新增 strong_wage 模式是最安全的** — 配合金额阈值，precision 高
- **medium_income 模式需要配合行为特征** — 单独使用需要 payer_key 可提取且重复出现
- **PayPal、Afterpay 等支付平台的 "SALARY" 关键词会被 hard/soft negative 排除**
- **收入引擎关注 payer_key 提取质量** — 如果 text 中的付款方信息被大量 stopwords 占据，无法提取有效 payer_key
- **金额阈值是硬门控**: 低于 100 的除非有工资历史，否则不会被分类为工资
- **hard_negative 列表包含 return/refund/loan 等关键词** — 新增收入规则时要确保不会被误判覆盖

---

#### 5. liability_engine (优先级 300) — 负债/贷款识别

**源码位置**: `liability_engine/pipeline.py` (62行) + `liability_engine/domain/counterparty.py` (646行) + `liability_engine/domain/streams.py` (2414行) + `liability_engine/domain/special_rules.py` (93行) + `liability_engine/domain/dishonours.py` (27行)

##### 执行流水线 (`run_pipeline`)

```
Step 1: apply_counterparty_rules → counterparty + product_type
        来源: counterparty_keyword_rules.csv
Step 2: apply_home_loan_car_loan_flags → is_home_loan, is_car_loan 标志
        来源: home_loan_car_loan_rules.csv (Format A)
Step 3: apply_credit_card_rules → counterparty + product_type
        来源: credit_card_rules.csv (V2 regex)
Step 4: apply_dishonour_rules → is_dishonours 标志
        来源: dishonours_rules.csv (dishonour_style)
Step 5: apply_special_rules → Cash Converters 零售修正, Credit Corp 子产品
        硬编码在 special_rules.py
Step 6: apply_overdrawn_flag → is_overdrawn 标志
        来源: overdrawn_rules.csv (Format B)
Step 7: apply_debt_collection_flag → is_debt_collection 标志
        来源: debt_collection_rules.csv (Format B)
Step 8: apply_debt_consolidation_flag → is_debt_consolidation 标志
        来源: debt_consolidation_rules.csv (Format B)
Step 9: identify_streams → stream_id (按 product_type 优先级分组)
Step 10: add_finv_category → finv_category (根据 product_type + stream_id)
Step 11: apply_generic_loan_catchall → 剩余含 "LOAN" 的行 → "Non SACC Loans"
```

**注意**: liability 在 orchestrator 中排除了已被 income 分类为 Wages/Centrelink 的行。

##### 对手方规则 (`counterparty.py` / `load_rules`)

CSV: `keyword, counterparty, product_type, match_type` (以及可选的 rule_type)

- **keyword 模式** (rule_type ≠ "regex"):
  - keyword 用分号分隔 → 转大写
  - 匹配: `re.compile(r"\b(?:" + "|".join(escaped_keywords) + r")\b", re.IGNORECASE)` → **全词匹配**
  - 第一匹配胜出（已匹配的行被 `already` tracker 排除）
- **regex 模式** (rule_type = "regex"):
  - 直接编译 pattern
  - 同样第一匹配胜出

##### 信用卡规则 (`load_credit_card_rules`)
- 两层: specific (priority ≥ 90) 和 generic (priority < 90)
- 每层内按 priority 降序
- 支持 bank/account_type/dr_cr 列约束
- keyword 列实际包含 regex pattern（列名遗留）
- 使用 `normalize_regex_pattern()` 去除 `(?i)` 前缀（引擎自己加 IGNORECASE flag）
- 已覆写模式 (overwrite): 会覆盖已有 counterparty/product_type

##### 标志规则 (`_load_flag_rules`)

自动检测两种格式：

**Format A** (home_loan_car_loan): `target_field, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled`
- match_scope: `text` / `text_or_counterparty` / `all`
- match_type: `keyword` / `regex`
- bucket: `{match_scope}_{match_type}` → keyword 被合并为单个 `\b(?:kw1|kw2|...)\b` regex
- `all_rules`: 不带 pattern 的纯条件规则（仅 account_type/dr_cr/bank/amount_gt 条件）

**Format B** (overdrawn, debt_collection, debt_consolidation): `keyword, match_type, counterparty, product_type`
- keyword 按 (counterparty, product_type) 分组合并

##### 元数据映射 (`_TARGET_METADATA_MAP`)
```
is_home_loan        → counterparty="Home Loan", finv_category="Non SACC Loans", product_type="home_loan"
is_car_loan          → counterparty="Car Loan", finv_category="Non SACC Loans", product_type="car_loan"
is_overdrawn         → counterparty="Overdrawn", finv_category="Overdrawn"
is_debt_collection   → counterparty="Debt Collection", finv_category="Debt Collection"
is_debt_consolidation→ counterparty="Debt Consolidation", finv_category="Debt Consolidation"
```

##### Stream 分配 (`streams.py` / `PRODUCT_RULES`)

按优先级为不同 product_type 生成 stream_id:

| 优先级 | product_type | stream 格式 | 说明 |
|--------|-------------|------------|------|
| 10 | bnpl | `bnpl_NNN` | 按 (app+account+counterparty) 分组 |
| 20 | wage_advance | `wage_advance_NNN` | 同上 |
| 25 | home_loan | `home_loan_NNN` | 同上 |
| 27 | car_loan | `car_loan_NNN` | 同上 |
| 30 | bank | `bank_NNN` | 同上 |
| 35 | contract_loan | `contract_loan_NNN` | 同上 |
| 40 | personal_loan | `sacc_NNN` / `non_sacc_NNN` / `unknown_NNN` | 复杂聚类算法 |
| 50 | loc | `loc_NNN` | 直接 + SACC 合并 |

##### final finv_category 映射 (`FINV_CATEGORY_MAP`)
```
bank              → Credit Card Repayments
bnpl              → Non SACC Loans
wage_advance      → Non SACC Loans
contract_loan     → Non SACC Loans
home_loan         → Non SACC Loans
car_loan          → Non SACC Loans
personal_loan_sacc→ SACC Loans
personal_loan_unknown→ Unknown Loans
personal_loan_non_sacc→ Non SACC Loans
loc               → Non SACC Loans
```

##### 特殊规则 (`special_rules.py`)
- **Cash Converters**: 区分零售（有地点/SQ终端/EFTPOS/卡尾号）和贷款（有合同号 B31470T1949）
- **Credit Corp**: 根据子产品关键词 (wizit→bnpl, pup→loc, ccc→personal_loan)

##### 通用贷款兜底 (`apply_generic_loan_catchall`)
- finv_category 仍为空 + text 含 `\bLOAN\b` → `counterparty="Generic Loans"`, `finv_category="Non SACC Loans"`

##### 对规则生成的影响
- **counterparty_keyword_rules.csv 的 keyword 支持分号分隔** — 多个变体用分号分开
- **counterparty 匹配是全词** (`\b...\b`) — 不会误匹配子串
- **home_loan_car_loan_rules.csv 是 Format A** — 有 match_scope/target_field/amount_gt 等额外列
- **debt_collection/overdrawn 等是 Format B** — 简单的 keyword→counterparty 映射
- **credit_card_rules.csv 的 keyword 列实际是 regex** — 列名有误导性
- **先执行的规则优先级更高**: counterparty → home/car loan → credit card → ...
- **debt_collection/dishonours/debt_consolidation csv schema 和 dishonour_engine 不同** — 它们是 Format B，不是 rule_type/pattern/required_terms
- **product_type 决定 stream 行为** — 错误的 product_type 会导致错误的 stream 分组

---

#### 6. all_other_credit_engine (优先级 400) — 杂项入账

**源码位置**: `all_other_credit_engine/engine.py` (83行)

##### 规则加载
使用 `load_dishonour_style_rules()` — 与 dishonour_engine **完全相同的 loader**:
```
CSV: rule_type, pattern, required_terms
→ list[(rule_type, pattern, [required_terms])]
```

##### 执行逻辑（全部向量化）
```
1. 过滤: 只处理 dr_cr == "credit" 的行
2. 排除: 已被前面引擎分类的行（但保留 "External Transfers" — 允许覆盖）
3. 匹配: 仅 keyword 模式 → text_col.str.contains(re.escape(pattern), case=False)
4. 输出: finv_category="All Other Credits", counterparty="-"
```

##### 关键差异 vs dishonour_engine
- **只处理入账** (credit only)
- **只使用 keyword 模式** — `required_terms` 被加载但**完全忽略**
- **允许覆盖 External Transfers** — transfer 分类的入账可以被重新分类为 All Other Credits

##### 对规则生成的影响
- **只生成 keyword 规则** — regex 虽然 CSV 支持，但代码中未实现
- **rule_type 列仍然需要填写** (保持 CSV schema 一致)
- **required_terms 列可留空** — 代码中不使用
- **keyword 使用 re.escape** — 特殊字符自动转义
- **是入账分类** — 出账( debit)不会被此引擎处理

---

#### 7. fee_engine (优先级 500) — 费用识别

**源码位置**: `fee_engine/domain/classification.py` (258行)

##### 规则加载 (`load_fee_rules`)

规则完全从 CSV 动态加载，不再硬编码：

```python
# load_fee_rules() 从 CSV 读取规则
CSV schema: priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description
→ 按 priority 升序排列（数字越小越先匹配）
→ re.compile(pattern) — 无 IGNORECASE flag
```

关键代码细节：
- **CSV 动态加载**: 规则从 `fee_classification_rules.csv` 读取，可直接追加 CSV 添加新规则
- **category 映射**: CSV 中用 `"fee"`（小写），引擎通过 `_CATEGORY_MAP` 自动转换为 `"Fees"`。`"Overdrawn"` 直接透传
- **大小写敏感**: `re.compile(pattern)` 无 flags，与原始文本大小写完全一致才匹配
- **zero_amount_reject**: CSV 列 `zero_amount_reject=true` 的规则在金额为 $0.00 时被撤销
- 空规则名或空 pattern 的行自动跳过，正则编译失败的行也自动跳过

##### 匹配逻辑 (`FeeClassifier.predict`)
```python
for rule_name, category, pattern, counterparty in self.rules:
    if pattern.search(text):  # 第一个匹配胜出
        return FeePrediction(...)
```
- 第一匹配胜出，按 priority 升序
- **所有规则都用 regex** — 编译为 `re.compile(pattern)`
- 未使用 `re.IGNORECASE` — **大小写敏感**！

##### 文本预处理 (`normalize_text`)
```python
re.sub(r"\s+", " ", str(value)).strip()  # 仅压缩空格，保留原样大小写
```

##### $0 金额排除 (`zero_amount_reject`)

CSV 中 `zero_amount_reject=true` 的规则，在交易金额为 $0.00 时被撤销。
这些行通常是信息性备注（如 "Includes Foreign Currency Conversion Fee $0.81"），不是实际扣费。
具体规则由 CSV 中的 `zero_amount_reject` 列控制，不再硬编码在源码中。

##### 对规则生成的影响
- **规则从 CSV 加载，可直接追加** — 不再需要修改 Python 源码
- **大小写完全敏感** — `^MONTHLY FEE$` 不匹配 `Monthly Fee`
- **pattern 通常 `^` 锚定** — 匹配文本开头
- **category 只有两个值**: `"Overdrawn"` 或 `"Fees"`（CSV 中用 `"fee"`，引擎自动转换为 `"Fees"`）
- **Overdrawn 规则必须 priority 更小** — 确保透支费用覆盖通用费用
- **新规则需要设置合适的 priority** — 插入到正确的优先级位置
- **counterparty 是描述性标签**（如 "International Transaction Fee"），不是具体商户名

---

#### 8. catch_all_engine (优先级 999) — 兜底关键词

**源码位置**: `catch_all_engine/engine.py` (228行)

##### 规则加载 (`_load_rules`)
```python
CSV: rule_name, category, pattern, match_type, confidence
→ list[(rule_name, category, pattern, match_type, confidence)]
→ 按 confidence 降序排列
```

##### 文本预处理
```python
clean_text(value)  # 与 initial_engine 相同：大写 + 仅[A-Z0-9 ] + 压缩空格
```
**这与 transfer 的小写 normalize 不同！**

##### 匹配逻辑（逐行迭代，非向量化）
```
对每个候选行:
  clean_text(text) → 遍历规则（已按 confidence 降序）:
    keyword 模式: str.find(keyword) + 全词边界检查
      - 前后必须是空格或字符串边界（与 initial_engine 相同的全词逻辑）
    regex 模式: re.search(pattern, text)
    → 最高 confidence 匹配胜出（不是第一个匹配！）
```
- **只处理未被前面引擎分类的行** (`exclude_prior_claimed`)
- 逐行处理（性能不高但在优先级 999 时数据量已很小）
- 全词检查: `pos > 0 and text[pos-1] != " "` 和 `end < len(text) and text[end] != " "`

##### 输出
- `finv_category = matched category`
- `counterparty = "-"` (硬编码)
- `classification_rule_id = rule_name`
- `classification_reason = "category=<cat>; rule=<rule>; evidence=confidence=<0.XX>"`

##### 对规则生成的影响
- **keyword 是全词匹配**（经过 clean_text 的大写文本） — "CAFE" 匹配 "JOES CAFE AND RESTAURANT" 但不匹配 "CAFES"
- **keyword 必须是大写且仅含 [A-Z0-9 ]** — 与 initial_engine 相同
- **regex 在 clean_text 后的文本上匹配** — 不需要考虑大小写变体
- **最高 confidence 胜出，不是第一匹配** — confidence 值非常重要
- **confidence 建议范围**: 0.70–0.85（当前规则的实际范围）
- **只生成通用类别关键词** — 具体商户名归 initial_engine
- **规则按 confidence 降序加载** — CSV 顺序不影响匹配优先级

### 各引擎规则 CSV Schema（含代码级约束）

#### dishonour_engine, all_other_credit_engine (共用 `load_dishonour_style_rules`)
```
rule_type, pattern, required_terms
```
- `rule_type`: `"keyword"` 或 `"regex"`
- `pattern`: keyword 模式为纯文本（自动 `re.escape`），regex 模式为正则
- `required_terms`: 分号分隔的必须同时出现的词（小写），regex 模式时所有 term 都必须满足
- **代码差异**: all_other_credit 只使用 keyword 模式且忽略 required_terms

#### fee_engine
```
priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description
```
- **规则从 CSV 动态加载**，不再硬编码在 Python 源码中
- 所有 pattern 是 `^` 锚定的 regex，**大小写敏感**（不使用 `re.IGNORECASE`）
- category 仅两个值: `"fee"`（CSV中，引擎自动转为 `"Fees"`）或 `"Overdrawn"`
- `zero_amount_reject=true` 的规则在金额为 $0.00 时被撤销
- priority 升序排列，数字越小优先级越高

#### catch_all_engine
```
rule_name, category, pattern, match_type, confidence
```
- keyword 在 `clean_text()` 后的文本上做**全词匹配**（`str.find` + 空格边界）
- regex 在 `clean_text()` 后的文本上做 `re.search`
- **最高 confidence 胜出**（不是第一匹配），confidence 降序加载
- confidence 建议 0.70–0.85

#### income_engine
```
pattern_group, pattern, match_type, description
```
- `pattern_group` 决定规则的语义角色：`strong_wage`, `medium_income`, `centrelink`, `salary_packaging`, `self_employed_gig`, `wage_advance`, `hard_negative`, `soft_negative`, `return_like`, `repeat_employer_like`, `repeat_employer_like_exclusion`
- 所有 pattern 都是 regex
- 需配合 `income_config.csv` 中的金额/周期性阈值
- **新增 strong_wage 规则最安全、影响最小**

#### liability_engine (多文件，多格式)

**counterparty_keyword_rules.csv** (Format: keyword+counterparty):
```
keyword, counterparty, product_type, match_type [, rule_type]
```
- keyword: 分号分隔多个变体
- 匹配: **全词** `\b(?:kw1|kw2)\b`，大小写不敏感
- rule_type: 可选，设为 `"regex"` 时 keyword 列作为 regex 处理

**credit_card_rules.csv** (Format: V2 regex):
```
priority, account_type, dr_cr, bank, match_type, keyword, min_prefix_len, counterparty, product_type, exclude_pattern
```
- ⚠ `keyword` 列实际是 regex pattern（列名有误导性）
- 两层匹配: specific (priority≥90) → generic (priority<90)
- 覆写模式: 会覆盖已有 counterparty/product_type

**home_loan_car_loan_rules.csv** (Format A — 多字段条件):
```
target_field, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled
```
- target_field: `is_home_loan` 或 `is_car_loan`
- match_scope: `text`, `text_or_counterparty`, `all`
- match_type: `keyword` 或 `regex`

**debt_collection_rules.csv / debt_consolidation_rules.csv / overdrawn_rules.csv** (Format B — 简单):
```
keyword, match_type, counterparty, product_type
```
- keyword 按 (counterparty, product_type) 分组合并
- 匹配: 全词 `\b`

**dishonours_rules.csv** (liability 内部用):
```
rule_type, pattern, required_terms
```
- 与 dishonour_engine 相同的 loader

#### transfer_engine (多文件)

**transfer_counterparty_rules.csv**:
```
keyword, counterparty, match_type
```
- keyword: 分号分隔
- 匹配: **子串匹配**（不是全词），大小写不敏感，先匹配胜出
- 默认值: `"Miscellaneous Funds Transfer"`

**transfer_external_high_confidence_rules.csv / transfer_external_medium_confidence_rules.csv**:
```
priority, rule_name, category, pattern, dr_cr, description
```
- category 固定填 `"External Transfers"`
- pattern: regex，**用小写**
- dr_cr: 可选，`"debit"` 或 `"credit"` 或空（匹配所有方向）

**transfer_internal_regex_rules.csv**:
```
priority, rule_name, pattern, dr_cr, description
```
- 同上但 category 由代码自动设为 `"Internal Transfer"`（CSV 中 category 列被忽略）

**transfer_indicator_patterns.csv / transfer_group_exclusion_patterns.csv / transfer_row_exclusion_patterns.csv**:
```
pattern
```
- 仅 pattern 列，编译为 `re.Pattern` 列表
- 通常不自动生成

**transfer_pairing_exclusions.csv**:
```
keyword, match_type, exclusion_reason, priority, description
```
- keyword: 分号分隔；match_type: keyword 或 regex
- 通常不自动生成

#### initial_engine
```
merchant_name, keywords, link, category, category_source, keyword_updated_at, category_updated_at
```
- `keywords`: pipe `|` 分隔的多个变体，每个变体经 `clean_text()` 处理
- `category`: 不能为 `"Financial Institutions"`（整行被过滤）
- 每个商户最多 50 个 keyword 变体
- keyword 不能是 STOPWORDS 中的独立 token（见上方 70 个词的列表）

### 流水线覆盖规则（代码级实现）

来自 `classification_core/orchestrator.py` 的 `ClassificationOrchestrator.run()`:

```
1. 所有引擎按 config.enabled_engines 的 priority 升序执行
2. 每个引擎看到所有原始交易（candidates = original.copy()）
3. 后面引擎的预测**行级覆盖**前面的 (finv_category + counterparty 成对替换)
4. 特殊处理: liability_engine 的 candidates 排除已被 income 分类为 Wages/Centrelink 的行
5. 所有引擎预测被归档到 claim_archive（用于 baseline diff 检测回归）
6. 最终未被任何引擎认领的标记为 "unclassified"
```

**各引擎间的重要交互**（来自源码）：
- **initial → liability/dishonour**: initial 匹配的 "Financial Institutions" 在 pipeline 中被清除，由 liability/dishonour 兜底
- **initial → liability**: initial 匹配的 "Debt Collection"/"Debt Consolidation" 被清除，由 liability 处理
- **income → liability**: orchestrator 在 liability 前排除 Wages/Centrelink 行
- **initial → income**: income_engine 复用 initial_engine 的 cached automaton 做 KB counterparty 查找
- **transfer → all_other_credit**: all_other_credit 可覆盖 "External Transfers"（保留在 candidates 中）
- **initial → catch_all**: catch_all 通过 `exclude_prior_claimed` 排除所有前面引擎的分类
- **覆盖规则**: 后执行的引擎总是覆盖前面的，不管 confidence 高低

### 各引擎文本预处理差异（重要！）

| 引擎 | 预处理方式 | 大小写 |
|------|----------|--------|
| initial | `clean_text()`: 仅 `[A-Z0-9 ]`，大写 | 大写 |
| transfer | `normalize_text()`: `re.sub(r"\s+", " ", str(value).lower()).strip()` | **小写** |
| dishonour | 无特殊预处理，直接用 `text_col.str.contains()` | 不敏感(flags) |
| income | `clean_text()` (同 initial) | 大写 |
| liability | `normalize_match_text()`: `re.sub(r"\s+", " ", str(value).strip().upper())` | 大写 |
| all_other_credit | 无特殊预处理 | 不敏感(flags) |
| fee | `normalize_text()`: 仅压缩空格 `re.sub(r"\s+", " ", str(value)).strip()` | **大小写敏感** |
| catch_all | `clean_text()` (同 initial) | 大写 |

**这意味着**:
- 为 transfer 生成 regex 规则时，**必须用小写**
- 为 catch_all/initial 生成 keyword 规则时，**必须用大写且仅 `[A-Z0-9 ]`**
- 为 fee 生成规则时，pattern 必须与原始文本的**实际大小写完全匹配**

## 项目目录结构

```
D:\project\Auto_Rule_Extension\
├── CLAUDE.md              ← 本文件（项目上下文 + 引擎机制详解）
├── SKILL.md               ← Skill 入口（指向 .claude/skills/）
├── README.md              ← 项目概览 + 快速开始
├── config.json            ← 配置（引擎定义、分析参数）
├── raw/                   ← 各引擎规则 CSV 的本地副本
│   ├── initial_rule/merchant_kb.csv
│   ├── transfer_rule/*.csv
│   ├── catch_all_rule/catch_all_rules.csv
│   └── ...
├── input/                 ← 数据入口（.xlsx 分类报告）
├── scripts/
│   ├── common.py              ← 共享工具（配置加载、路径解析、引擎元数据）
│   ├── analyze_gaps.py        ← 统计层：发现高频未覆盖模式
│   ├── label_compare.py       ← 质检层：illion vs finv 分类差异质检报告
│   ├── search_merchant.py     ← 工具：搜索 merchant_kb.csv 中的商户/keyword
│   ├── validate_candidates.py ← 验证层：语法+Schema+重叠检查
│   ├── baseline.py            ← 基线层：save 保存基线 / diff 模拟影响面
│   ├── test_rules.py          ← 测试层：确认规则在真实数据上的实际表现
│   └── apply_rules.py         ← 执行层：写入确认规则到本地 raw/
├── .claude/skills/         ← Claude Code Skill 定义
├── reviews/               ← 每次运行的审核产物
│   └── <date>/
│       ├── gap_summary.json
│       ├── label_compare_report.xlsx
│       ├── <engine>_candidates.csv
│       ├── validation_report.json
│       └── impact_report.json
├── baseline/              ← 基线快照
│   └── <date>/
│       └── baseline.json.gz
└── .gitignore
```

## 工作流程

```
1. 用户在 finv_category_V2 跑完流水线，导出 .xlsx 分类报告
2. 用户将 .xlsx 放入 input/ 目录
3. 用户启动 Claude Code Skill（/auto-rule-extension）
4. Claude 执行 baseline.py save → 保存当前分类状态快照
5. Claude 执行 analyze_gaps.py → 生成各引擎的 gap_summary.json
6. Claude 执行 label_compare.py → 生成 illion vs finv 分类差异质检报告
7. Claude 读取 gap_summary + label_compare_report + 各引擎已有规则 → 逐引擎分析 → 生成候选规则 CSV
8. Claude 执行 validate_candidates.py → 语法/Schema 验证
9. Claude 执行 baseline.py diff → 影响面分析（gain/conflict）
10. 🔴 弹出规则确认窗口，用户逐引擎审核候选规则
11. Claude 执行 test_rules.py → 确认规则在实际数据上的表现
12. Claude 打印测试分析报告 → 🔴 弹出最终确认窗口
13. 用户最终确认后，Claude 执行 apply_rules.py → 写入本地 raw/
14. （可选）执行 apply_rules.py --sync_to <finv_path> 同步到 finv_category_V2
```

## 引擎规则使用机制

每个引擎的文本归一化、匹配逻辑、CSV Schema 约束等代码级细节已在上方各节中详细说明。生成候选规则前必须确认目标引擎的匹配逻辑兼容。

关键差异速查：

| 引擎 | 文本归一化 | 匹配方式 | 多匹配策略 |
|------|-----------|---------|-----------|
| initial | `clean_text()` 大写 [A-Z0-9]，去通道前缀 | Aho-Corasick + 全词边界 | 最长 keyword 优先 |
| transfer | `.lower()` 保留特殊字符 | `str.contains` 向量化 regex | 先匹配先得（priority 排序） |
| dishonour | 原始 text（case=False） | `str.contains` keyword/regex | OR（任意命中） |
| income | `clean_text()` 大写 [A-Z0-9] | 多阶段复合决策树 | 多信号综合 + 金额阈值 |
| liability | 混合（取决于子模块） | 多子模块 pipeline | 各模块独立排序 |
| all_other_credit | 原始 text（case=False） | `str.contains` **仅 keyword** | OR |
| fee | 空格归一化，**保留原始大小写** | `re.search` | 先匹配先得（规则列表顺序） |
| catch_all | `clean_text()` 大写 [A-Z0-9] | `str.find` + 全词 / `re.search` | 最高 confidence 优先 |

⚠️ 生成规则时必须注意：
- **文本归一化对齐**：keyword 规则在 `clean_text()` 后的文本上匹配（标点全部移除！），regex 规则的环境因引擎而异
- **all_other_credit 只用 keyword**：regex 规则被加载但不会匹配
- **fee 保留原始大小写**：`^MONTHLY\s+FEE$` ≠ `^monthly fee$`
- **income 不是简单关键词匹配**：必须满足金额阈值 + payer_key + 频率模式
- **transfer 只能输出 Internal Transfer / External Transfers**：不能生成其他分类
- **initial_engine 的通道前缀被自动去除**：不需要在 pattern 中包含 `DEBIT CARD PURCHASE ` 等前缀

## 重要约定

- 任何规则写入操作前必须经过人工确认，不可自动执行
- 新规则保持各引擎已有规则的 confidence 范围和命名风格
- 输入必须是 finv_category_V2 流水线处理后的 .xlsx 报告，包含 classification_status 列
- `raw/` 目录的规则文件是本地工作副本，初始从 finv_category_V2 复制，后续由 apply_rules.py 维护
- 同步到 finv_category_V2 后，需在 finv_category_V2 中手动运行 baseline.py 更新基线
- **生成每个候选规则前，必须参考本文件上方对应引擎的章节确认文本归一化方式与匹配逻辑**
