# Auto Rule Extension

基于 Claude Code 的智能规则维护系统，为 finv_category_V2 交易分类流水线的 10 个分类引擎自动发现并补充规则。

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
- `raw/` 目录维护了各引擎规则 CSV 的本地副本；**事实源是 GitHub 上的 ServiFlow-AI**
- **「上游改了、raw 要跟上」由 `scripts/sync_upstream.py` 处理**（HTTPS 直取，不经过 finv，
  不要再手工 cp）—— 见下方「规则同步」章节
- 确认的规则先写入本地 `raw/`，再通过 `--sync_to` 同步到 finv_category_V2
- **「finv 改了、raw 要跟上」由 `scripts/sync_rules.py pull` 处理**（finv 侧独立演进时）
- 不需要 finv_category_V2 的 Python 环境或模块
- 输入数据来自 finv_category_V2 跑完流水线后导出的 .xlsx 分类报告
- ⚠️ **规则文件的「新」不等于「行为一致」**：引擎自身也会重构（如 rent v2.0 从单层变双层）。
  判断某条规则会怎么跑，必须读 finv 的引擎源码，不能只看规则 CSV

### 合并进来的运维模块（`modules/`）

除核心的规则发现链路外，仓库还并入了两个原本独立的运维项目，并新增了一个模块：

| 模块 | 原项目 | 职责 | 入口 |
|------|--------|------|------|
| `modules/merchant_kb/` | Merchant-Extraction-new | ABR XML → `raw/initial_rule/merchant_kb.csv` | `build_knowledge_base.py` |
| `modules/assessment/` | BS-CAT-Performance-Assessment | reviews/ 底稿 → `reports/` 分类性能报告 | `scripts/run_report.py` |
| `modules/liability_enrich/` | ★ 新增 | 分类报告 → liability 放贷商候选规则 | `gap_source.py` + `/liability-enrichment` |

三者都**直接消费/生产本仓库根目录的数据**（`raw/`、`reviews/`、`reports/`），
不再是自成一体的独立项目。各自的 CLAUDE.md / README.md 见模块目录下。

### Skills（`.claude/skills/`）

| Skill | 用途 | 环节 |
|-------|------|------|
| `/auto-rule-extension` | 10 引擎规则发现与补充（主链路） | 分析 → 候选 → 审批 → 落库 |
| `/liability-enrichment` | 联网核实疑似放贷商 → liability 候选 | `modules/liability_enrich` Step 2 |
| `/merchant-kb-maintenance` | merchant_kb 构建/清洗/合并/校验 | `modules/merchant_kb` 流程入口 |
| `/classify-merchants` | 新商户联网分类（写 `category` 列） | `merchant-kb-maintenance` 场景 C |
| `/performance-report` | 出分类性能报告（md/docx/pdf/图表） | `modules/assessment` 流程入口 |

> 早期仓库根曾有一个 `SKILL.md` 作为「Skill 入口」，该文件已在 commit `6529997` 删除 ——
> skills 现在由 `.claude/skills/` 目录直接发现，不需要中转文件。

## 目标项目架构

### finv_category_V2 的 10 个引擎

> ⚠️ **2026-08-27 起（commit 30a8da3）initial 与 transfer 的执行顺序已交换**：
> transfer 现在是第一个执行的引擎（priority=1），initial 变为第二个（priority=10）。
> **实际执行顺序: transfer → initial → dishonour → gambling → income → liability → all_other_credit → fee → rent → catch_all**。
> 由于后执行的引擎行级覆盖前面的，**initial 的商户 KB 匹配现在会覆盖 transfer 的分类**（以前相反）。
> 事实源: `finv_category_V2/configs/pipeline.json`。
>
> ⚠️ **gambling（priority=180）是 2026-09 新增的第 10 个引擎**，ARE 于 2026-09-15 纳入。
> 它接管了原先分散在两处的赌博识别：merchant_kb.csv 的 1,822 个 Gambling 商户
> （2026-09-02 从 KB 删除）和 catch_all 的 16 条通用赌博关键词。详见下方 gambling 章节。

| 优先级 | engine_id | 规则文件 | 匹配方式 | 文本预处理 | 规则数 |
|--------|-----------|----------|----------|-----------|--------|
| 1 | transfer | 8 个 CSV | regex(小写) + keyword(子串) | `lower().strip()` | 235 |
| 10 | initial | `merchant_kb.csv` | Aho-Corasick 全词 | `clean_text()` 大写 | 874,600 商户 / 1,338,895 keywords |
| 150 | dishonour | `dishonour_rules.csv` | keyword(re.escape) + regex | 无(flags) | 14 |
| 180 | gambling | `gambling_rules.csv` | 双层：institution(Aho-Corasick 全词) + keyword/regex(最高conf) | `clean_text()` 大写 | 1,838 |
| 200 | income | `income_pattern_rules.csv` + `income_config.csv` | regex + 金额阈值 + 行为特征 | `clean_text_with_seams()` 大写 | 217 (+17 配置) |
| 300 | liability | 8 个 CSV（多格式） | keyword(边界) + regex 双 tier | `upper().strip()` | 1,072 |
| 400 | all_other_credit | `all_other_credit_rules.csv` | keyword(re.escape) 仅入账 | 无(flags) | 33 |
| 500 | fee | `fee_classification_rules.csv` | regex(大小写不敏感,^锚定) | 仅压缩空格 | 113 |
| 800 | rent | `rent_rules.csv` | 双层：institution(Aho-Corasick 全词) + keyword/regex(最高conf) | `clean_text()` 大写 | 21,809 |
| 999 | catch_all | `catch_all_rules.csv` | keyword(全词) + regex, 最高conf胜出 | `clean_text()` 大写 | 439 |

> ⚠️ **规则数以 `raw/` 实测为准**（`scripts/sync_rules.py status` 会打印行数）。
> 历史上 rent 一栏长期写着「~15」，实际早已是 **21,809 条**——rent 引擎 v2.0 把
> 21,794 个租赁机构商户搬进了 `rent_rules.csv`，详见下方 rent 章节。

### 每个引擎的代码级规则使用详解

以下内容来自对 `finv_category_V2` 各引擎源码的逐行分析，描述每个引擎**实际如何加载、预处理、匹配和应用规则**。理解这些细节对于生成能正确工作的规则至关重要。

---

#### 1. initial_engine (优先级 10) — 商户名 → 分类

**源码位置**: `initial_engine/domain/classification.py` (423行) + `initial_engine/pipeline.py`

> ⚠️ 2026-08-07 (commit 29ae8b5) 起 merchant_kb.csv 压缩为 **3 列**；2026-08-10 (commit 8ba18ea) 起 **keyword 加载时不再自动 `clean_text()`**。

##### 规则加载 (`load_merchant_kb`)
```
merchant_kb.csv → chunk-read 100k行/批 → 展开 pipe-separated keywords
→ str.strip()（不再 clean_text!） → 过滤 STOPWORDS → 去重 → 构建 Aho-Corasick 自动机
```

关键代码细节：
- **Chunk 读取**: `pd.read_csv(chunksize=100_000)`, 仅读取 `merchant_name, keywords, category` 三列（文件实际也只有这三列）
- **Keyword 展开**: `keywords.str.split("|").str[:_MAX_VARIANTS_PER_MERCHANT]` — 每个商户最多 50 个 keyword 变体
- **排除**: 直接丢弃 `category == "Financial Institutions"` 的行（由 liability/dishonour 处理）
- **Stopwords 过滤** (`_STOPWORDS`): **97 个**泛化银行/支付术语（2026-08-07 起），**作为独立 keyword 时被丢弃**，但包含它们的多词短语保留。除文档原先的类别（卡片类 CARD/VISA/EFTPOS/ATM 等、支付通道 BILL/OSKO/NPP/DIRECT/DEBIT 等、交易类 PAYMENT/TRANSFER/REFUND 等、费用类 FEE/INTEREST/SURCHARGE 等、通用后缀 LIMITED/GROUP 等），**新增**：PAYPASS、CONTACTLESS、CHIP、PAYTO、AUTOPAY、RECURRING、PYMT、PYMNT、XFER、REVERSAL、REVERSED、REBATE、ADJUSTMENT、CHARGE、TRANSACTION、SETTLEMENT、PENDING、CLEARED、AUTHORISATION、OVERDRAFT、ONLINE、INTERNET、MOBILE、BANKING、BRANCH、COUNTER、TELLER、PHONE、AUS、AUD、INTERNATIONAL、OVERSEAS、FOREIGN、CONVERSION、VALUE、DATE、RECEIPT、INVOICE、ORDER、NUMBER、SUNDRY、GENERAL、OTHER、PAYEE、MERCHANT、STORE、RETAIL、ACCOUNT、FUNDS、CASH、MONEY、CORPORATION、ENTERPRISES 等（完整列表见源码 L266-375）
- **Keyword 清洗**: ⚠️ **已移除**（commit 8ba18ea "优化init的速度"）——keyword 现在只做 `str.strip()` 原样插入自动机。**以前写错的 keyword 会被自动清洗后匹配，现在永远静默失配**。生成 keyword 必须预清洗为大写、仅 `[A-Z0-9 ]`、压缩空格
- **去重**: 先 chunk 内去重（`drop_duplicates`），再跨 chunk 去重（dict key）
- **自动机构建**: `ahocorasick.Automaton()` → `add_word(kw, (kw, merchant, cat))` → `make_automaton()`
- **模块级缓存**: `_cached_automaton` — 同一进程内只加载一次，income_engine 也复用此缓存

##### 文本预处理 (`_clean_transaction_text`)
```
原始 text → _EFTPOS_TS_RE 剥离时间戳 → clean_text() → 去除支付通道前缀 → 用于匹配
```

- `_EFTPOS_TS_RE`（2026-08-18 新增）: 剥离 "EFTPOS DEBIT [EFTPOS] DD/MM HH:MM" 时间戳，在 clean 之前执行
- `clean_text()`: 大写 + 只保留 `[A-Z0-9 ]` + 压缩空格（仅用于**交易文本侧**，keyword 侧已无此清洗）
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

- 位置推算: 优先用 ahocorasick end_pos（2.x 语义 `end_pos - kw_len + 1`，失败回退 1.x 的 `end_pos - kw_len`），最后 fallback `str.find()`（commit 8ba18ea 起）
- 全词检查: `pos > 0 and text[pos-1] != " "` 和 `end < len(text) and text[end] != " "`
- 最长优先: 当多个 keyword 匹配时，选 `len(kw)` 最大的

##### Pipeline 后处理 (`initial_engine/pipeline.py`)
匹配后还会清除以下分类（因为 ownership 属于其他引擎）：
- `finv_category == "Debt Collection"` → 清空
- `finv_category == "Debt Consolidation"` → 清空
- `finv_category == "Financial Institutions"` → 清空（加载时已丢弃该 category 行，此为冗余防御）
- 前两类由 liability_engine 负责

##### 对规则生成的影响
- **keyword 必须是预清洗后的形式**（大写，仅 `[A-Z0-9 ]`，压缩空格）——⚠️ 2026-08-10 起引擎加载时**不再自动 clean_text**，写错格式即静默失配，无兜底
- **不要添加 STOPWORDS 中的词作为独立 keyword** — 它们会被自动过滤（列表已扩到 97 个）
- **keyword 不需要考虑 channel prefix** — 代码已自动剥离
- **每个商户最多 50 个变体** — 超过的被截断
- **不要给 category="Financial Institutions" 的商户添加规则** — 整行在加载时被丢弃
- **全词匹配**: keyword "WOOLWORTHS" 匹配 "WOOLWORTHS SUPERMARKET" 但不匹配 "WOOLWORTHSGROUP"
- **最长匹配**: 如果 "COLES" 和 "COLES EXPRESS" 都匹配，后者胜出
- **counterparty 被设为 merchant_name**，不是 keyword
- **merchant_kb.csv 只有 3 列**（merchant_name, keywords, category）— 候选 CSV 中的 link/category_source 等元数据列由 apply_rules.py 剥离，不会写入

---

#### 2. transfer_engine (优先级 1) — 转账识别

**源码位置**: `transfer_engine/domain/classification.py` (669行) + `transfer_engine/domain/transfer_rules.py` (104行) + `transfer_engine/domain/transfer_counterparty.py` (77行)

**核心约束**: transfer_engine **只输出两个 category**: `Internal Transfer` 和 `External Transfers`。不输出 Gambling、Entertainment 等其他分类。

> ⚠️ 2026-08-27 起 transfer 是**第一个执行**的引擎（priority=1），其分类会被 initial 等后续引擎覆盖。

##### 规则文件及加载方式

| 文件 | 加载函数 | 格式 | 用途 |
|------|---------|------|------|
| `transfer_external_high_confidence_rules.csv` | `_load_rules_csv()` | CSV → `list[(priority, rule_name, category, pattern, dr_cr)]` | 高置信外部转账 regex |
| `transfer_external_medium_confidence_rules.csv` | `_load_rules_csv()` | 同上 | 中置信外部转账 regex |
| `transfer_internal_regex_rules.csv` | `_load_rules_csv()` | 同上（但 category 栏被忽略） | 内部转账 regex |
| `transfer_counterparty_rules.csv` | `load_counterparty_rules()` | keyword(semicolon分隔), counterparty, match_type | 转账对手方识别 |
| `transfer_indicator_patterns.csv` | `_load_pattern_list()` | `pattern_id, pattern, description` | 转账指标检测 |
| `transfer_group_exclusion_patterns.csv` | `_load_pattern_list()` | `pattern_id, category, pattern, description` | 配对排除（组级别） |
| `transfer_row_exclusion_patterns.csv` | `_load_pattern_list()` | `pattern_id, pattern, description` | 行排除（单行级别） |
| `transfer_pairing_exclusions.csv` | `load_exclusion_rules()` | keyword, match_type, exclusion_reason, priority | P2P 配对排除 |

> 注: indicator/exclusion 三个文件实际有 `pattern_id`/`description`（有的还有 `category`）列，不再是"仅 pattern 列"；加载器 `row.get("pattern")` 兼容旧格式。

##### 执行流水线 (`classify_transfers`)

```
Step 1:  配对检测 (pairing) → Internal Transfer
         条件: 同 (application_id, transaction_date, amount) 组内同时有 debit 和 credit
               + 行通过 transfer_indicator 检测
               + 组不匹配 exclusion 关键词（gambling/lender）
               + 行不匹配 transfer_row_exclusion_patterns.csv 的行级规则（如 \bosko\b）
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
- **外部转账规则的 category 列实际填 `"transfer"`**（现有规则如此；代码硬编码输出 "External Transfers"，不读该列，但为一致性照抄现有值）
- **dr_cr 列可选**: 留空表示匹配所有方向，"debit" 或 "credit" 限制方向
- **priority 决定匹配顺序**: 高 priority 先匹配，已匹配的行不再被后续规则处理
- **对手方规则是子串匹配不是全词** — "OSKO" 也会匹配 "OSKOPAYMENT"
- **不要为赌博/博彩平台生成 transfer 规则** — 它们不是转账
- **exclusion/indicator 文件通常不需要自动生成**
- ⚠️ **transfer 是最先执行的引擎**（priority=1）— 生成 transfer 规则时注意：文本若命中 merchant_kb 关键词，最终分类会被 initial（priority=10）覆盖；"all_other_credit 保留 External Transfers" 的机制仍在，但 External Transfers 行要先撑过 initial/dishonour 等引擎才轮得到 all_other_credit

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

#### 4. gambling_engine (优先级 180) — 赌博识别

**源码位置**: `gambling_engine/engine.py`（258 行）+ `classification_core/merchant_institution.py`
（与 rent_engine v2.0 **共用同一套加载/匹配机制**）

> ⚠️ **新引擎（engine_version 1.0），ARE 于 2026-09-15 纳入**。它把原先分散在两处的
> 赌博识别合并到一处：`merchant_kb.csv` 的 1,822 个 Gambling 商户（2026-09-02 从 KB
> 删除）成为 institution 层，catch_all 的 16 条通用赌博关键词成为 rule 层
> （catch_all_rules.csv 里的 Gambling 行已清零）。

##### 规则加载（`load_rules`，与 rent 共用）

```python
CSV: rule_name, category, pattern, match_type, confidence, counterparty, source
→ rule 层（source != institution）: list[(rule_name, category, pattern, match_type, confidence)]
                                   按 confidence 降序
→ institution 层（source == institution）: {rule_name → (pattern, counterparty)}，按文件顺序
```

| `source` | 条数 | 语义 | counterparty |
|----------|------|------|--------------|
| `rule` | 16 | 原 catch_all 的通用赌博关键词 | 空（引擎输出 `"-"`） |
| `institution` | 1,822 | 从 merchant_kb.csv 搬出的 Gambling 商户 | 商户名 |

当前实测：全部 `match_type=keyword`、全部 `category=Gambling`；
institution 行 confidence 一律 0.95，rule 行落在 0.70–0.90；
**变体数最大 35**（远低于 `MAX_VARIANTS_PER_INSTITUTION=50`，**没有 rent 那种静默截断**）。

##### 两层匹配逻辑

```
【rule 层】(source=rule)
  candidates → exclude_prior_claimed(prior_claims)   ← ⚠️ 对所有更早引擎的认领让位
  clean_text(text)
  遍历规则（confidence 降序）:
    keyword: str.find + 全词边界（前后必须是空格或字符串边界）
    regex:   re.search(pattern, text)
  → 最高 confidence 胜出
  → counterparty = "-"

【institution 层】(source=institution)
  build_institution_automaton(institutions)   ← pattern 按 | 拆分，最多 50 个变体
  对**全部** candidates（不只未认领的）:
    text → clean_text_with_channel_prefix()   ← 与 initial 相同的通道前缀清洗
    match_institutions() → Aho-Corasick，最长 keyword 胜出，全词边界
  命中后**丢弃**该命中的两种情形：
    1. 该行已被 fee 或 dishonour 引擎认领
    2. initial 引擎已认领该行，且其命中 keyword 长度 >= 本次 gambling 命中的长度
       （复刻搬迁前 KB 自动机跨全类别按长度排名、等长归 initial 的结果）
  → counterparty = 商户名

【合并】institution 层命中**优先于** rule 层（0.95 > rule 层的 ≤0.90）
```

##### 与 orchestrator 的关系（⚠️ 与 rent 不同）

- gambling 的 `candidates` **不做** income/liability 排除 —— 它比这两个引擎**先跑**
- ⚠️ **赌博认领对 income/liability 是终局**：orchestrator 在 commit 前**丢弃**
  income(200) 和 liability(300) 在 gambling 已认领行上的预测 —— 赌客从博彩公司收到的
  payout（退款/入账）**永远不会被重新标成 Wages**。这是唯一一个"反向压制"后置引擎的机制
- 其余后置引擎（all_other_credit / fee / rent / catch_all）**保持后来者覆盖**的语义
- rule 层的 `exclude_prior_claimed` 意味着：gambling 的 rule 行**只对
  transfer/initial/dishonour 都没认领的行生效**（priority 180 时能存在的全部前置认领）
- ⚠️ 源码 docstring 指出：institution 层里"让位给 fee"的分支**现实中永不触发**
  （fee 现在晚于 gambling 运行），fee 与 gambling 的双重命中改由 fee 在 priority 500
  的后置认领解决；**只有 fee 那条要求"未认领"的 keyword 兜底规则**才会给 gambling 让路

##### 输出

- `finv_category = "Gambling"`（硬编码，CSV 的 category 列仅作一致性占位）
- `counterparty` = institution 层命中时为**商户名**，rule 层命中时为 `"-"`
- `classification_rule_id` = rule_name（institution 层为常量 `gambling_merchant_kb`）
- `classification_reason` = institution 层带 `keyword=`/`merchant=`，rule 层带 `confidence=`
- `stream_id = pd.NA`

##### 对规则生成的影响

- **新增通用赌博关键词 → 走 rule 层**（`source=rule`，counterparty 留空），
  confidence 参照现有 0.70–0.90；**这是最安全的增量方式**
- **新增具体赌博商户 → 走 institution 层**（`source=institution`，counterparty 填商户名，
  confidence 照写 0.95）—— 但注意 institution 层会**让位**给 initial 的等长/更长 keyword 命中
- ⚠️ **rule 行对 transfer/initial/dishonour 已认领的行不生效** —— 如果缺口行已被这三个引擎
  认领（例如商户名命中了 merchant_kb），加 gambling rule 规则**不会有任何效果**，
  得先确认该行的 `classification_engine`
- 两层的文本侧都是 `[A-Z0-9 ]`（institution 层额外剥离通道前缀），keyword 必须大写
- **institution 行的 pattern 是 `|` 分隔的变体表**，`match_type` 与 `confidence` 列**都不被读取**
- **只生成 Gambling 分类的规则** — 引擎只输出 "Gambling" 一个 category
- `analyze_gaps.py` 把 `pattern_type == "gambling"` 的模式路由到本引擎
  （`pattern_classification.gambling_indicators` 命中即判为 gambling，**优先级最高**）

---

#### 5. income_engine (优先级 200) — 收入识别

**源码位置**: `income_engine/domain/classification.py` (1246行) + `income_engine/pipeline.py` + `income_engine/domain/summary.py`

⚠ **这是最复杂的引擎**，不仅依赖文本匹配，还依赖大量行为特征（金额分布、时间规律、付款方历史）。

> ⚠️ 2026-08 多次大改：pattern_group 扩到 18 组、工资规则扩到 18 条、文本预处理改用 `clean_text_with_seams()`、新增零工（gig）双规则、stream_id 粗化。规则总数约 217。

##### 规则加载

**income_pattern_rules.csv** — 数据驱动的模式定义:
```
CSV各列: pattern_group, pattern, match_type, description
→ _load_pattern_rules() 返回 Dict[pattern_group → List[regex_string]]
```
当前 pattern_group 分类（**18 组**，原 11 组 + 新增 7 组）：
| group | 含义 | 影响 |
|-------|------|------|
| `strong_wage` | 强工资信号 | 直接触发 strong_wage_keyword 规则（已扩：HUMANFORCE、THRIVE PAY、WEEKLY PAY 等） |
| `medium_income` | 中等收入信号 | 需结合重复/稳定性 |
| `repeat_employer_like` | 类似雇主的重复付款方 | 需稳定金额+常见工资额 |
| `repeat_employer_like_exclusion` | 排除以上匹配的 | 减少误判 |
| `salary_packaging` | 工资打包 | 直接触发 salary_packaging |
| `centrelink` | 政府福利 | 直接触发 centrelink（已扩：CHILD SUPPORT 等） |
| `self_employed_gig` | 自雇/零工 | 需无 gig 排除（已扩：STRIPE、SQUARE、SHOPIFY、PAYMENT LINK、DIDI PARTNER PAYMENT、HNRY、LIGHTSPEED、B2B PAY 等平台模式） |
| `wage_advance` | 工资预支 | 识别为非收入（新增 WAGE ACCESS、EARNED WAGE ACCESS；删除 WAGE PAY） |
| `return_like` | 退款类 | 归入 hard_negative |
| `hard_negative` | 硬排除 | 直接阻止分类为收入（`\bATO\b` 独立词已删除，改为 `DIRECT CREDIT...ATO` 复合模式，"Salary ATO PAYROLL" 不再被误杀） |
| `soft_negative` | 软排除 | 阻止 base_wages 但允许 alias 规则 |
| `transfer_from` ⭐ | "TRANSFER FROM" 单独成组 | **已从 hard_negative 中移除**，配合豁免机制 |
| `pay_signal` ⭐ | 独立 PAY 词 + 截断的 PAYRO | 触发 transfer_from + pay_signal 规则 |
| `behavior_exclusion` ⭐ | 高重复行为规则专用排除 | 自转账/退款/房租/BET365/ATO 等 |
| `gig_exclusion_extra` ⭐ | gig 额外排除 | LOAN DEPOSIT、UBER EATS 等 |
| `gig_family_exclusion` ⭐ | 家庭成员/生活费词 | MUM/DAD/FOOD/FUEL 等 |
| `gig_personal_transfer` ⭐ | 个人转账信号 | OSKO/PAYMENT FROM/PAYID/NPP FROM 等 |
| `gig_personal_exclusion` ⭐ | 赌博 payout/自转账/网关 | NEXTBET、KENOGO 等 |

> ⭐ = 2026-08 新增组。规则总数约 218 条（原 ~106）。

**income_config.csv** — 阈值配置:
```
config_key, config_value, config_type
```
关键默认值（原有值全部不变）：
- `MIN_NORMAL_WAGE_AMOUNT = 100`
- `COMMON_WAGE_AMOUNT_MIN = 300`, `COMMON_WAGE_AMOUNT_MAX = 10000`
- `POSSIBLE_WAGE_AMOUNT_MAX = 20000`
- `STABLE_AMOUNT_CV_MAX = 0.35`
- `HIGH_REPEAT_PAYER_COUNT_MIN = 4`
- `VERY_HIGH_REPEAT_PAYER_COUNT_MIN = 8`
- `REGULAR_GAP_*`: 周(6-8天), 双周(13-16天), 月(27-33天) —— 原单一 `REGULAR_GAP_RANGES` key 已拆为 6 个独立 key（REGULAR_GAP_WEEKLY_MIN/MAX 等）
- ⭐ 新增: `SMALL_WAGE_AMOUNT_MIN = 50`（小额强关键词工资区间下限）、`STABLE_PAYER_REPEAT_COUNT_MIN = 6`、`GIG_REPEAT_PAYER_AMOUNT_MIN = 20`、`GIG_REPEAT_PAYER_COUNT_MIN = 4`

##### 执行流水线

```
1. prepare_input: 合并 Unnamed 列到 text, 删除受限列 (trx_type, third_party 等)
2. add_wages_features:
   a. add_basic_features: 日期解析, 金额数值化, clean_text_with_seams, 12 种 pattern 匹配计数
   b. add_payer_history_features: payer_key 提取, 时间间隔, 规律性, 稳定性
3. apply_wages_rules:
   a. add_hard_gate_flags: 4 个硬门控 (credit, amount>=100, effective_hard_negative==0, possible_wage_amount)
   b. add_base_wage_rules: 基础工资规则（含新增的 high_repeat 行为规则，单独 OR 进 is_wages_pred）
   c. add_small_amount_history_override: 小金额但有工资历史 → 覆盖
   d. add_soft_negative_alias_to_known_wage_payer_rule: 软排除但 payer token 重叠 ≥2 → 覆盖
4. add_income_type_rules: 5 级分类 (salary_packaging > centrelink > salary_payg > self_employed_gig 双规则)
5. _add_kb_counterparty: 复用 initial_engine 的自动机查找商户名（KB 匹配优先于 payer_key 提取；centrelink 硬编码 "CENTRELINK"）
6. add_income_streams: stream_id 粗化（见下）
```

##### 文本预处理：`clean_text_with_seams`（⚠️ 与其余 clean_text 引擎不同）

```python
# classification_core/text.py — income 引擎独有
# 在 clean_text 基础上，于数字-字母接缝处插入空格:
# "3780.7Salary"   → "3780 7 SALARY"
# "IBMAUPAY986915" → "IBMAUPAY 986915"
# 使 \bSALARY\b 等全词正则能命中拼接的银行描述
```
- income 的 regex 规则在 **seams 处理后的文本**上匹配，数字与字母拼接处会多出空格，正则边界行为与 initial/catch_all/rent 不同
- 该变体仅 income 使用（全局改动曾导致 rent 的 "TEN00083" 失配回归）

##### TRANSFER FROM 豁免机制（`effective_hard_negative`）

```
effective_hard_negative = 有 hard_negative 且 非(有 transfer_from 且 有 strong_wage)
```
- "FAST TRANSFER FROM X WAGES"（transfer_from + strong_wage）可被识别为工资
- 裸 "TRANSFER FROM X" 仍被硬阻断
- 硬门控从 `has_hard_negative_keyword==0` 改为 `effective_hard_negative==0`

##### Payer Key 提取 (`make_payer_key`)
```python
1. 移除日期: \b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b
2. 移除长数字: \b\d{5,}\b
3. 提取所有 ≥3 个大写字母的 token
4. 过滤 PAYER_STOP_WORDS (~68 个 token: DIRECT, CREDIT, PAYMENT, TRANSFER, FROM, TO, ...)
5. 取前 4 个 token 拼接
```
- PAYER_STOP_WORDS 词表与文档原 40 词一致（实际约 68 个 token，含月份缩写等）
- payer_key 输入是 `clean_text_with_seams` 结果，数字-字母接缝拆开会影响 token 提取

##### 工资检测核心规则 (18 条，原 13 + 新增 5)

原 13 条（1-13，逻辑基本不变，仅硬门控换为 effective_hard_negative 语义）：
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

新增 5 条（⭐）：
14. ⭐ `strong_wage_keyword_small_amount`: 金额 [50,100) 小额强关键词工资（HUMANFORCE 兼职等）
15. ⭐ `transfer_strong_wage_keyword_no_payer_key`: TRANSFER FROM + 强关键词但 payer_key 提取失败（如 "TRANSFER FROM PAYROLL SALARY" 全被 stopwords 占据）
16. ⭐ `transfer_from_pay_signal`: TRANSFER FROM + 独立 PAY 词
17. ⭐ `high_repeat_no_keyword`: 无任何关键词 + 重复≥6 + 规律周期/稳定金额
18. ⭐ `high_repeat_soft_negative`: 同上但带 soft_negative

> 规则 16-18 属于新增的 `add_high_repeat_behavior_rules()`（L746-827），不计入 base_wages_pred，而是单独 OR 进 is_wages_pred。新增输出列约 20 个（has_transfer_from_keyword、has_pay_signal、has_behavior_exclusion、has_gig_*、effective_hard_negative 等）。

##### 零工收入（gig）双规则

- 关键词规则 `income_self_employed_gig`: 新增门控 `has_gig_family_exclusion_keyword==0`（payer_key **全部**由家庭词组成才拦截）；有个人转账信号时**让位**给行为规则（除非含 INVOICE）
- ⭐ 行为规则 `income_self_employed_gig_repeat_payer`: 个人转账信号 + 同 payer 重复≥4 + 金额 [20,20000] + 单向（无 payer_debit）+ 无行为排除（PSP 引用可豁免）+ 无 family/个人排除 → self_employed_gig
- gig 排除组不再包含 SOFT_NEGATIVE（OSKO/TRANSFER 从 gig 门控中移除，避免 "Osko + UBER" 结构性死锁）
- 赌博相关排除：NEXTBET、KENOGO（gig_personal_exclusion）、BET 365（behavior_exclusion）

##### finv_category 映射
- `salary_packaging`, `salary_payg`, `self_employed_gig` → `"Wages"`
- `centrelink` → `"Centrelink"`
- `non_income` → `""` (空)
- `wage_advance` → `known_non_income_type_pred="wage_advance"`（非收入）

##### stream_id 生成（2026-08 起"粗化"）
- 三种工资子类（salary_payg/salary_packaging/self_employed_gig）**共享一个粗化序号** `wage_001, wage_002...`；centrelink 保留自己的 `centrelink_NNN`
- 流分组键 = `bank_account_id + income_type_pred + counterparty`（**不再是纯 payer 分组**）
- 细粒度子类型仍通过 summary 的 `income_category` 列保留

##### 对规则生成的影响
- **所有规则都是 regex**（match_type 列虽存在但代码只用 regex）
- **新增 strong_wage 模式是最安全的** — 配合金额阈值，precision 高
- **medium_income 模式需要配合行为特征** — 单独使用需要 payer_key 可提取且重复出现
- **PayPal、Afterpay 等支付平台的 "SALARY" 关键词会被 hard/soft negative 排除**
- **收入引擎关注 payer_key 提取质量** — 如果 text 中的付款方信息被大量 stopwords 占据，无法提取有效 payer_key
- **金额阈值是硬门控**: 低于 100 的除非有工资历史（或小额强关键词 [50,100)），否则不会被分类为工资
- **hard_negative 列表包含 return/refund/loan 等关键词** — 新增收入规则时要确保不会被误判覆盖
- ⭐ **新增组的语义必须先理解再使用**: `transfer_from` 是豁免信号不是排除；`behavior_exclusion`/`gig_*` 组只影响行为规则和 gig 规则，不影响 base_wages
- ⭐ **regex 在 seams 文本上匹配** — "IBMAUPAY986915" 现在等价于 "IBMAUPAY 986915"，`\bSALARY\b` 类全词正则能命中拼接文本

---

#### 6. liability_engine (优先级 300) — 负债/贷款识别

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
Step 12: renumber_stream_ids_uniform → 所有 stream_id 统一重命名为 loan_NNN (必须最后执行)
```

**注意**: liability 在 orchestrator 中排除了已被 income 分类为 Wages/Centrelink 的行。

> ⚠️ **2026-08-25 起（commit 8feef85）最终输出的 stream_id 全部为 `loan_NNN`**（全局跨 application 编号，按各流最早交易日期排序）。`bnpl_NNN`/`sacc_NNN` 等前缀只是内部中间格式，**下游不能再从 stream_id 前缀解读产品类型**——一律改读 `product_type`/`finv_category`。

##### 对手方规则 (`counterparty.py` / `load_rules`)

CSV: `keyword, counterparty, product_type, match_type` (以及可选的 rule_type)

- **keyword 模式** (rule_type ≠ "regex"):
  - keyword 用分号分隔 → 转大写
  - 匹配: `(?<![A-Za-z])` + re.escape(keyword) + `(?![A-Za-z])` + re.IGNORECASE —— ⚠️ **不是 `\b`**！只拦字母不拦数字，"1360 CASH LOANS" 能匹配 keyword "360 CASH LOANS"（`\b` 下不会）
  - 第一匹配胜出（已匹配的行被 `already` tracker 排除）
- **regex 模式** (rule_type = "regex"):
  - 直接编译 pattern
  - ⚠️ **陷阱**: loader 只读 `rule_type` 列，但 CSV 表头实际是 `match_type`（没有 rule_type 列）！现有 CSV 中唯一的 regex 行（`DT\.[A-Za-z0-9]+\s+Sunshine`, match_type=regex）被当 keyword 处理——先转大写再 re.escape，含字面反斜杠，**实际永远匹配不到，是死规则**。新增 regex 规则必须**给 CSV 加 `rule_type` 列**（loader 支持，文件里还没有）

##### 信用卡规则 (`load_credit_card_rules`)
- 两层: specific (priority ≥ 90) 和 generic (priority < 90)
- 每层内按 priority 降序
- 支持 bank/account_type/dr_cr 列约束
- keyword 列实际包含 regex pattern（列名遗留）
- 使用 `normalize_regex_pattern()` 去除 `(?i)` 前缀（引擎自己加 IGNORECASE flag）
- ⚠️ **"覆写模式"实际不存在**: 初始 `already = product_type != ""` —— **已有 product_type 的行（被 counterparty 规则认领的）整个被排除**，credit card 规则只填充未认领行；层内 `already` tracker 使后规则永远不覆盖先规则
- product_type 值域: `bank`(109) / `contract_loan`(90, idp_v1 merge 引入) / `generic_loan`(180, liability extention 引入)，共 379 条规则

##### 标志规则 (`_load_flag_rules`)

自动检测两种格式：

**Format A** (home_loan_car_loan): **12 列** `rule_id, target_field, rule_name, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled`
- match_scope: `text` / `text_or_counterparty` / `all`
- match_type: `keyword` / `regex` / **`always`**（不带 pattern 的纯条件规则，如 HL009）
- bucket: `{match_scope}_{match_type}` → keyword 被合并为单个 `\b(?:kw1|kw2|...)\b` regex
- `all_rules`: 不带 pattern 的纯条件规则（仅 account_type/dr_cr/bank/amount_gt 条件）
- `rule_id`/`rule_name` 列 loader 忽略，仅为可读性

**Format B** (overdrawn, debt_collection, debt_consolidation): 三个文件列各不相同——
- `debt_collection_rules.csv`: `counterparty, product_type, rule_id, match_type, keyword`（有 regex 行且**生效**）
- `debt_consolidation_rules.csv`: `keyword, match_type, counterparty, product_type`（与文档一致）
- `overdrawn_rules.csv`: `counterparty, rule_id, match_type, pattern, note` —— **没有 keyword 列**，5 条全部是 `match_type=regex`，pattern 列放正则（如 `\boverdra(?:wn|ft|w)\b`）；loader 兼容 `keyword` 或 `pattern` 列

##### 元数据映射 (`_TARGET_METADATA_MAP`)
```
is_home_loan        → counterparty="Home Loan", finv_category="Non SACC Loans", product_type="home_loan"
is_car_loan          → counterparty="Car Loan", finv_category="Non SACC Loans", product_type="car_loan"
is_overdrawn         → counterparty="Overdrawn", finv_category="Overdrawn"
is_debt_collection   → counterparty="Debt Collection", finv_category="Debt Collection"
is_debt_consolidation→ counterparty="Debt Consolidation", finv_category="Debt Consolidation"
```

##### Stream 分配 (`streams.py` / `PRODUCT_RULES`)

按优先级为不同 product_type 生成内部 stream_base（最终统一重命名为 `loan_NNN`）:

| 优先级 | product_type | 内部格式 | 说明 |
|--------|-------------|------------|------|
| 10 | bnpl | `bnpl_NNN` | 按 (app+account+counterparty) 分组 |
| 20 | wage_advance | `wage_advance_NNN` | 同上 |
| 25 | home_loan | `home_loan_NNN` | 同上 |
| 27 | car_loan | `car_loan_NNN` | 同上 |
| 30 | bank | `bank_NNN` | 同上 |
| 35 | contract_loan | `contract_loan_NNN` | 同上 |
| 37 | generic_loan | `generic_loan_NNN` | ⭐ 新增（credit_card CSV 有 180 行 generic_loan） |
| 40 | personal_loan | `sacc_NNN` / `non_sacc_NNN` / `unknown_NNN` | 复杂聚类算法 |
| 50 | loc | `loc_NNN` | 直接 + SACC 合并 |

- `renumber_stream_ids_by_application`: identify_streams 末尾按 (app, prefix) 内部连续编号（自 2026-08 前已有）
- `SPECIAL_COUNTERPARTY_STREAM_RULES` + `apply_special_counterparty_stream_overrides`: zip money 的 personal_loan 流 merge 成 non_sacc；credit corp 的 sacc 流转为 non_sacc（文档原未记载）
- `apply_generic_loan_catchall` 在 identify_streams **之后**设置 product_type="generic_loan"（pipeline.py L59-61）——兜底命中行**拿不到 stream_id**
- ⚠️ **最终输出永远是 `loan_NNN`**：`renumber_stream_ids_uniform`（streams.py:2321-2370）在 pipeline 最后把所有 stream_id 重命名，函数 docstring 明确 "nothing downstream may read product/type semantics from the stream_id prefix anymore"

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
generic_loan      → Non SACC Loans   ⭐ 新增
```
- `add_finv_category` 还处理 dishounor 行 → **"Dishonours"**（文档原未记载的输出路径）

##### 特殊规则 (`special_rules.py`)
- **Cash Converters**: 区分零售（有地点/SQ终端/EFTPOS/卡尾号）和贷款（有合同号 B31470T1949）；零售命中行输出 `counterparty="Cash Converters Retail"`, `finv_category="Retail"`
- **Credit Corp**: 根据子产品关键词 (wizit→bnpl, pup→loc, ccc→personal_loan)

##### 通用贷款兜底 (`apply_generic_loan_catchall`)
- finv_category 仍为空 + text 含 `\bLOAN\b` → `counterparty="Generic Loans"`, `finv_category="Non SACC Loans"`, `product_type="generic_loan"`

##### 对规则生成的影响
- **counterparty_keyword_rules.csv 的 keyword 支持分号分隔** — 多个变体用分号分开
- **counterparty 匹配边界是 `(?<![A-Za-z])...(?!A-Za-z)`，不是 `\b`** — 数字可穿透，避免用纯数字相邻的关键词
- **counterparty CSV 没有 `rule_type` 列** — 加 regex 规则时必须新增该列，否则 regex 会被当 keyword 处理成死规则
- **home_loan_car_loan_rules.csv 是 Format A（12 列）** — 有 rule_id/rule_name/match_scope/amount_gt 等列
- **debt_collection/overdrawn/debt_consolidation 三个 Format B 文件列各不相同** — overdrawn 用 pattern 列 + regex，debt_collection 有 rule_id 列
- **credit_card_rules.csv 的 keyword 列实际是 regex** — 列名有误导性；product_type 可选 bank/contract_loan/generic_loan
- **先执行的规则优先级更高**: counterparty → home/car loan → credit card → ...
- **credit_card 规则只填充未认领行** — 不覆盖 counterparty 已认领的行（文档"覆写模式"不成立）
- **product_type 决定 stream 行为** — 错误的 product_type 会导致错误的 stream 分组
- **stream_id 一律是 loan_NNN** — 分析与基线工具不得从 stream_id 前缀读产品类型

---

#### 7. all_other_credit_engine (优先级 400) — 杂项入账

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

#### 8. fee_engine (优先级 500) — 费用识别

**源码位置**: `fee_engine/domain/classification.py` (258行)

##### 规则加载 (`load_fee_rules`)

规则完全从 CSV 动态加载，不再硬编码：

```python
# load_fee_rules() 从 CSV 读取规则
CSV schema: priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, unclassified_only, dr_cr, description
→ 按 priority 升序排列（数字越小越先匹配）
→ re.compile(pattern, re.IGNORECASE)  ← ⚠️ 自 2026-08-20 起大小写不敏感（曾是无 flags 的大小写敏感）
```

关键代码细节：
- **CSV 动态加载**: 规则从 `fee_classification_rules.csv` 读取，可直接追加 CSV 添加新规则
- **category 映射**: CSV 中用 `"fee"`（小写），引擎通过 `_CATEGORY_MAP` 自动转换为 `"Fees"`。`"Overdrawn"` 直接透传
- **大小写不敏感**: `re.compile(pattern, re.IGNORECASE)` — pattern 无需考虑大小写变体
- **zero_amount_reject**: CSV 列 `zero_amount_reject=true` 的规则在金额为 $0.00 时被撤销
- **unclassified_only**: CSV 列 `unclassified_only=true` 的规则只处理尚未被其他引擎分类的行
- **dr_cr 约束**: 列值为 `"credit"`/`"debit"` 时只在对应方向生效（CSV 中有 2 条 credit 规则）
- 空规则名或空 pattern 的行自动跳过，正则编译失败的行也自动跳过
- 当前 113 条规则（原文档约 87 条）

##### 匹配逻辑 (`FeeClassifier.predict`)
```python
for rule_name, category, pattern, counterparty in self.rules:
    if pattern.search(text):  # 第一个匹配胜出
        return FeePrediction(...)
```
- 第一匹配胜出，按 priority 升序
- **所有规则都用 regex** — 编译为 `re.compile(pattern, re.IGNORECASE)`

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
- **大小写不敏感** — `^MONTHLY FEE$` 和 `^monthly fee$` 都能匹配 "Monthly Fee"
- **pattern 通常 `^` 锚定** — 匹配文本开头
- **category 只有两个值**: `"Overdrawn"` 或 `"Fees"`（CSV 中用 `"fee"`，引擎自动转换为 `"Fees"`）
- **Overdrawn 规则必须 priority 更小** — 确保透支费用覆盖通用费用
- **新规则需要设置合适的 priority** — 插入到正确的优先级位置
- **counterparty 是描述性标签**（如 "International Transaction Fee"），不是具体商户名
- **可选列** `unclassified_only`/`dr_cr` 可按需使用（原文档未记载）

---

#### 9. rent_engine (优先级 800) — 房租识别

**源码位置**: `rent_engine/engine.py`（**v2.0**，双层结构）+ `classification_core/merchant_institution.py`

> ⚠️ **rent 引擎在 commit `daec0be`「gambling和rent引擎」中重构为 v2.0**：
> `rent_rules.csv` 从 5 列扩到 **7 列**（新增 `counterparty`、`source`），
> 规则数从 15 条变成 **21,809 条**。旧文档「~15 条规则」已严重失真。

##### 规则加载 (`_load_rules`)
```python
CSV: rule_name, category, pattern, match_type, confidence, counterparty, source
→ keyword/regex 层: list[(rule_name, category, keyword, match_type, confidence)]，按 confidence 降序
→ institution 层:   {rule_name → [keyword...]}（pattern 用 | 分隔多个变体）
```

| `source` | 条数 | 语义 |
|----------|------|------|
| `rule` | 15 | 原始手写通用规则（RENT / TENANCY / LANDLORD / REAL ESTATE …），confidence 0.75–0.90，counterparty 为空 |
| `institution` | 21,794 | 特定租赁机构/中介商户，confidence **全部 0.95**，counterparty = 商户名 |

##### 两层匹配逻辑（⚠️ 与旧版单层完全不同）

```
【institution 层】仅当存在 institution 行时启用：
  build_institution_automaton(institutions)
  text → clean_text_with_channel_prefix()   ← 与 initial_engine 相同的通道前缀清洗，不是干净的 clean_text
  match_institutions() → Aho-Corasick，最长 keyword 胜出，全词边界
  命中后**丢弃**该命中的两种情形：
    1. 该行已被 fee 或 dishonour 引擎认领（这两个引擎在重构前也压得过 initial 的认领）
    2. initial 引擎已认领该行，且其命中 keyword 长度 >= rent 命中的 keyword 长度
       （重构前 initial 的自动机跨**全部** category 按长度排名，等长归 initial）
  → counterparty = institution 的商户名

【keyword/regex 层】原有语义，逐行迭代：
  clean_text(text) → 遍历规则（按 confidence 降序）:
    keyword: str.find + 全词边界（前后必须是空格或字符串边界）
    regex:   re.search(pattern, text)
  → 最高 confidence 胜出（不是第一个匹配！）
  → counterparty = "-"

【合并】institution 层命中**优先于** keyword 层（`inst_win` 覆盖 `kw_win`）
```

##### 与 orchestrator 的关系（重要）
- rent 的 `candidates` 已排除被 `income` 或 `liability` 认领的行
- rent **不使用** `exclude_prior_claimed`；但 institution 层通过 `prior_claim_keys()` 主动
  排除 fee / dishonour 的认领行（见上）
- **dishonour(150) 和 fee(500) 都在 rent(800) 之前执行**，它们的认领压得过
  initial(priority 10) 的商户 KB 认领 —— 所以 institution 层也必须对它们让位，
  否则重构会让这些行从 fee/dishonour 变成 Rent

##### 输出
- `finv_category = "Rent"`（硬编码，不是 CSV 的 category 列）
- `counterparty` = institution 层命中时为**商户名**，keyword 层命中时为 `"-"`
- `classification_rule_id` = rule_name（institution 层为常量 `rent_merchant_kb`）
- `classification_reason = "category=Rent; rule=<rule>; evidence=confidence=<0.XX>"`
- `stream_id = pd.NA`

##### 对规则生成的影响
- **两层规则要用不同的 confidence 策略**：institution 层的 0.95 会压过手写规则的 0.90；
  往 keyword 层加规则时，confidence 低于 0.95 的规则会被任何命中的 institution 覆盖
- **institution 层用 `clean_text_with_channel_prefix`**（含通道前缀剥离），keyword 层用
  `clean_text` —— 新增 institution 行时 keyword 仍需是大写、仅 `[A-Z0-9 ]`
- **keyword 是全词匹配** — "RENT" 匹配 "RENT JANUARY 2024" 但不匹配 "PARENT"
- **最高 confidence 胜出，不是第一匹配** — confidence 值非常重要
- **只生成 Rent 分类的规则** — 引擎只输出 "Rent" 一个 category
- **orchestrator 已排除 income/liability 的行** — 房租规则不会被这两个引擎的分类行触发
- **confidence 建议范围**: keyword 层 0.75–0.90（现有手写规则的实际范围）；
  institution 层固定 0.95

⚠️ **REAL ESTATE 全词陷阱**: 现有规则 `REAL ESTATE`（keyword）匹配 "REAL ESTATE AGENT" 但**不匹配** "REAL ESTATEAGENT"（现实中常见无空格拼写）。生成规则时注意这一点——如需覆盖无空格变体，用 regex 或额外 keyword。

⚠️ **已知数据状态矛盾**: 引擎 docstring 称这批商户是「从 `merchant_kb.csv` 搬出来的
（category=Rent）」，但实测**两边都在**——KB 里 21,794 条 Rent 行与 rent_rules.csv 的
21,794 条 institution 行 **100% 重合**。因此这些行通常先被 initial（priority 10）认领，
institution 层的「等长让位」规则再让它落回 initial。**改这批数据前先确认这是有意为之**。

⚠️ **institution 行同样有 50 变体上限**（`MAX_VARIANTS_PER_INSTITUTION`，与 merchant_kb 同）。
实测 2 行超限，超出部分静默失效：

| 规则 | 变体数 | 实际生效 | 被丢弃的示例 |
|------|--------|---------|-------------|
| `ABACUS STORAGE OPERATIONS LIMITED` | 134 | 前 50 | `STORAGE KING CRESTMEAD` 等 84 个 |
| `ELDERS RURAL SERVICES AUSTRALIA LIMITED` | 51 | 前 50 | `ELDERS VP MERCHANDISE` |

`scripts/validate_candidates.py` 的 `_check_rent` 已适配 v2.0 双层：institution 行会检查
match_type 必须为 keyword（否则是死规则）、变体数上限、逐变体的字符集，
且**不会**再把该层的 conf 0.95 误报为「偏高」（引擎对该层忽略 confidence 列，
恒用代码常量 `INSTITUTION_CONFIDENCE = 0.95`）。

---

#### 10. catch_all_engine (优先级 999) — 兜底关键词

**源码位置**: `catch_all_engine/engine.py` (228行)

##### 规则加载 (`_load_rules`)
```python
CSV: rule_name, category, pattern, match_type, confidence
→ list[(rule_name, category, pattern, match_type, confidence)]
→ 按 confidence 降序排列
```
当前 439 条规则（原文档约 280 条）。

> 2026-09-14 曾把本地多出的 16 条 Gambling 规则按 finv 版丢弃，raw 与 finv 已对齐。
> **这 16 条没有消失** —— 它们已迁入 `gambling_rules.csv` 的 rule 层（`source=rule`），
> 由 priority 180 的 gambling 引擎接管。当前 catch_all 的 Gambling 类别行数为 0。

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
- **confidence 建议范围**: 0.55–0.90（当前规则的实际范围，原文档写 0.70–0.85 过窄）
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
priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, unclassified_only, dr_cr, description
```
- **规则从 CSV 动态加载**，不再硬编码在 Python 源码中
- 所有 pattern 是 `^` 锚定的 regex，**大小写不敏感**（`re.compile(pattern, re.IGNORECASE)`，自 2026-08-20 起）
- category 仅两个值: `"fee"`（CSV中，引擎自动转为 `"Fees"`）或 `"Overdrawn"`
- `zero_amount_reject=true` 的规则在金额为 $0.00 时被撤销
- `unclassified_only=true` 的规则只处理未被其他引擎分类的行
- `dr_cr` 列值为 `"credit"`/`"debit"` 时只在对应方向生效
- priority 升序排列，数字越小优先级越高

#### rent_engine / gambling_engine（共用 `load_rules`，同构 7 列）
```
rule_name, category, pattern, match_type, confidence, counterparty, source
```
- **7 列**（v2.0 起；旧文档的 5 列 schema 已过时）—— gambling 与 rent 列结构完全相同
- `source` 决定走哪一层：`rule`（通用关键词，rule 层）或 `institution`（特定商户，institution 层）
- **institution 层**：`clean_text_with_channel_prefix()` 后用 Aho-Corasick 做全词匹配，**最长 keyword 胜出**，
  命中后可能被 fee/dishonour 的认领或 initial 的等长/更长 keyword 让位（见上方各引擎章节），counterparty = 商户名
- **rule 层**：`clean_text()` 后 keyword 做全词匹配（`str.find` + 空格边界）、regex 做 `re.search`，
  **最高 confidence 胜出**（不是第一匹配），confidence 降序加载，counterparty = `-`
  - ⚠️ **两层对"已被更早引擎认领的行"的态度不同**：rent 的 rule 层可以重新认领，
    gambling 的 rule 层走 `exclude_prior_claimed`，**对所有前置认领让位**
- `pattern` 列在 institution 行里是 `|` 分隔的多个 keyword 变体（与 merchant_kb 的 keywords 列同构）
- category 列恒为 `"Rent"` / `"Gambling"`（引擎输出硬编码，CSV 中的 category 仅作一致性占位）
- confidence：rent rule 层 0.75–0.90；**gambling rule 层 0.70–0.90**；两者 institution 层固定 0.95

#### catch_all_engine
```
rule_name, category, pattern, match_type, confidence
```
- keyword 在 `clean_text()` 后的文本上做**全词匹配**（`str.find` + 空格边界）
- regex 在 `clean_text()` 后的文本上做 `re.search`
- **最高 confidence 胜出**（不是第一匹配），confidence 降序加载
- confidence 建议 0.55–0.90（当前规则实际范围）

#### income_engine
```
pattern_group, pattern, match_type, description
```
- `pattern_group` 决定规则的语义角色（当前 18 组，完整表格见上方 income 章节）：`strong_wage`, `medium_income`, `centrelink`, `salary_packaging`, `self_employed_gig`, `wage_advance`, `hard_negative`, `soft_negative`, `return_like`, `repeat_employer_like`, `repeat_employer_like_exclusion`, `transfer_from`, `pay_signal`, `behavior_exclusion`, `gig_exclusion_extra`, `gig_family_exclusion`, `gig_personal_transfer`, `gig_personal_exclusion`
- 所有 pattern 都是 regex，在 `clean_text_with_seams()` 后的文本上匹配
- 需配合 `income_config.csv` 中的金额/周期性阈值
- **新增 strong_wage 规则最安全、影响最小**

#### liability_engine (多文件，多格式)

**counterparty_keyword_rules.csv** (Format: keyword+counterparty):
```
keyword, counterparty, product_type, match_type [, rule_type]
```
- keyword: 分号分隔多个变体
- 匹配: `(?<![A-Za-z])` + re.escape(keyword) + `(?![A-Za-z])`，大小写不敏感 —— ⚠️ **不是 `\b`**，数字可穿透
- rule_type: loader 支持，但**当前 CSV 没有该列** —— 加 regex 规则必须先新增列，否则 regex 被当 keyword 转大写 + re.escape 成死规则

**credit_card_rules.csv** (Format: V2 regex):
```
priority, account_type, dr_cr, bank, match_type, keyword, min_prefix_len, counterparty, product_type, exclude_pattern
```
- ⚠ `keyword` 列实际是 regex pattern（列名有误导性）
- 两层匹配: specific (priority≥90) → generic (priority<90)
- ⚠️ **不存在覆写模式**: 已有 product_type 的行（被 counterparty 规则认领）整个被排除，credit card 规则只填充未认领行
- product_type 值域: `bank` / `contract_loan` / `generic_loan`

**home_loan_car_loan_rules.csv** (Format A — 多字段条件, **12 列**):
```
rule_id, target_field, rule_name, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled
```
- target_field: `is_home_loan` 或 `is_car_loan`
- match_scope: `text`, `text_or_counterparty`, `all`
- match_type: `keyword` / `regex` / `always`（不带 pattern 的纯条件规则）
- rule_id/rule_name 列 loader 忽略，仅为可读性

**Format B — 三个文件列各不相同**:
```
debt_collection_rules.csv:    counterparty, product_type, rule_id, match_type, keyword
debt_consolidation_rules.csv: keyword, match_type, counterparty, product_type
overdrawn_rules.csv:          counterparty, rule_id, match_type, pattern, note   (无 keyword 列，5 条全是 regex)
```
- 匹配: 全词 `(?<![A-Za-z])...(?!A-Za-z)`（同 counterparty，非 `\b`）

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
pattern_id, pattern, description
```
- loader 只读 pattern 列，编译为 `re.Pattern` 列表（pattern_id/description 列可留空）
- 通常不自动生成

**transfer_pairing_exclusions.csv**:
```
keyword, match_type, exclusion_reason, priority, description
```
- keyword: 分号分隔；match_type: keyword 或 regex
- 通常不自动生成

#### initial_engine
```
merchant_name, keywords, category
```
（**自 2026-08-27 起仅 3 列** — 原文档 7 列 schema 过时；link/category_source/updated_at 等列已删除。
**当前实测：874,600 行 / 1,338,895 keywords**，空 category 行为 0）

> ⚠️ **2026-09-02 finv 侧做过一次 Gambling 清理**：从 KB 中删除了全部 1,819 条 Gambling 商户
> （如 `888 LOTTO PTY LTD`、`21bit Casino`），并把 `LOTTOPIA PTY LTD` 从 Gambling 改为 Retail。
> finv 的 KB 因此在很长一段时间里是 ARE 的**严格子集**。2026-09-14 已按「finv 为准」对齐，
> 覆盖前的版本在 `raw/initial_rule/merchant_kb.csv.bak`。
> **当前 KB 中 Gambling 类别行数为 0** —— 这是有意为之，不是数据丢失。
> **这批商户没有消失**：2026-09-15 起它们由 priority 180 的 `gambling_engine` 接管，
> 落在 `raw/gambling_rule/gambling_rules.csv` 的 institution 层（见上方 gambling 章节）。
>
> 另有一个已知数据质量问题：`Australia Post` 有 **5,486 个 keyword**，但 initial_engine 每个
> 商户只取前 50 个，其余 5,436 个从未生效。
- `keywords`: pipe `|` 分隔的多个变体（每个商户最多 50 个）
- ⚠️ **keyword 加载不再 clean_text**（引擎端只去 `_STOPWORDS` 中的独立 token + 去重）；但匹配在 `clean_text()` 后的文本上做 —— **写入 CSV 的 keyword 仍必须是大写且仅 `[A-Z0-9 ]`**，否则无法匹配（如 `KFC AUSTRALIA` 正常，`KFC (AUS)` 会因括号失配）
- `category`: 不能为 `"Financial Institutions"`（整行被过滤）
- keyword 不能是 STOPWORDS 中的独立 token（见上方 97 个词的列表）

### 流水线覆盖规则（代码级实现）

来自 `classification_core/orchestrator.py` 的 `ClassificationOrchestrator.run()`:

```
1. 所有引擎按 config.enabled_engines 的 priority 升序执行
2. 每个引擎看到所有原始交易（candidates = original.copy()）
3. 后面引擎的预测**行级覆盖**前面的 (finv_category + counterparty 成对替换)
4. 特殊处理: liability_engine 的 candidates 排除已被 income 分类为 Wages/Centrelink 的行
5. 特殊处理: rent_engine 的 candidates 排除已被 income 或 liability 认领的行
6. 特殊处理: income_engine / liability_engine 的预测在**提交前**丢弃 gambling(180)
   已认领的行 —— gambling 的认领对这两个引擎是**终局**（唯一一处「反向抑制」：
   正常是后执行覆盖先执行，这里先执行的 gambling 反过来压住后执行的 income/liability，
   否则博彩平台的派彩入账会被 income 重新打成 Wages）
7. 所有引擎预测被归档到 claim_archive（用于 baseline diff 检测回归）
8. 最终未被任何引擎认领的标记为 "unclassified"
9. 输出行带 4 个分类元数据列（xlsx 报告新增，自 2026-08 起）:
   classification_status / classification_engine / classification_engine_version / classification_priority
   —— baseline 与 gap 分析可按 classification_engine 定位规则归属引擎
```

**各引擎间的重要交互**（来自源码）：
- **initial → liability/dishonour**: initial 匹配的 "Financial Institutions" 在 pipeline 中被清除，由 liability/dishonour 兜底
- **initial → liability**: initial 匹配的 "Debt Collection"/"Debt Consolidation" 被清除，由 liability 处理
- **income → liability**: orchestrator 在 liability 前排除 Wages/Centrelink 行
- **income/liability → rent**: orchestrator 在 rent 前排除 income/liability 认领的行
- **gambling → income/liability**: ⚠️ **反向**的排除 —— orchestrator 丢弃 income/liability 在
  gambling 已认领行上的预测（gambling 的认领是终局）。同时 gambling 的 rule 层走
  `exclude_prior_claimed`，对**所有**更早引擎（transfer/initial/dishonour）的认领让位
- **initial → income**: income_engine 复用 initial_engine 的 cached automaton 做 KB counterparty 查找
- **transfer → all_other_credit**: all_other_credit 可覆盖 "External Transfers"（保留在 candidates 中）
- **initial → catch_all**: catch_all 通过 `exclude_prior_claimed` 排除所有前面引擎的分类
- **覆盖规则**: 后执行的引擎总是覆盖前面的，不管 confidence 高低 ——
  **唯一例外是 gambling 对 income/liability 的终局认领**（见上）

### 各引擎文本预处理差异（重要！）

| 引擎 | 预处理方式 | 大小写 |
|------|----------|--------|
| initial | `clean_text()`: 仅 `[A-Z0-9 ]`，大写 | 大写 |
| transfer | `normalize_text()`: `re.sub(r"\s+", " ", str(value).lower()).strip()` | **小写** |
| dishonour | 无特殊预处理，直接用 `text_col.str.contains()` | 不敏感(flags) |
| income | `clean_text_with_seams()`: 大写 + 仅 `[A-Z0-9 ]` + 保留 seam 标点字符 | 大写 |
| liability | `normalize_match_text()`: `re.sub(r"\s+", " ", str(value).strip().upper())` | 大写 |
| all_other_credit | 无特殊预处理 | 不敏感(flags) |
| fee | `normalize_text()`: 仅压缩空格 `re.sub(r"\s+", " ", str(value)).strip()` | **不敏感(IGNORECASE)** |
| rent | **双层**：institution 层用 `clean_text_with_channel_prefix()`（同 initial，含通道前缀剥离）；keyword 层用 `clean_text()` | 大写 |
| gambling | **双层**（同 rent）：institution 层用 `clean_text_with_channel_prefix()`；rule 层用 `clean_text()` | 大写 |
| catch_all | `clean_text()` (同 initial) | 大写 |

**这意味着**:
- 为 transfer 生成 regex 规则时，**必须用小写**
- 为 catch_all/initial/rent/gambling 生成 keyword 规则时，**必须用大写且仅 `[A-Z0-9 ]`**
- 为 income 生成 regex 规则时，在 `clean_text_with_seams()` 后的文本上匹配（大写，标点按 seam 规则保留）
- fee 大小写不敏感（自 2026-08-20 起）—— pattern 无需考虑大小写变体

## 项目目录结构

```
D:\project\Auto_Rule_Extension\
├── CLAUDE.md              ← 本文件（项目上下文 + 引擎机制详解）
├── README.md              ← 项目概览 + 快速开始
├── config.json            ← 配置（引擎定义、finv_root、分析参数）
├── .sync_state.json       ← raw/ ↔ finv 的三方同步基线（纳入 git）
├── .upstream_state.json   ← GitHub → raw/ 的同步基线：每文件的 ETag + 内容 sha256（纳入 git）
├── .upstream_cache/       ← blobless 浅抓取缓存，只有 commit/tree（gitignore）
├── docs/                  ← 设计文档（docs/superpowers/specs/）
├── raw/                   ← 各引擎规则 CSV 的本地副本
│   ├── initial_rule/merchant_kb.csv      ← 同时是 modules/merchant_kb 的产物
│   ├── transfer_rule/*.csv
│   ├── rent_rule/rent_rules.csv          ← 21,809 条（7 列）
│   ├── gambling_rule/gambling_rules.csv  ← 1,838 条（7 列，双层：16 rule + 1,822 institution）
│   ├── catch_all_rule/catch_all_rules.csv
│   └── ...
├── modules/               ← 从独立项目合并进来的运维模块
│   ├── merchant_kb/       ← ABR XML → merchant_kb.csv 的构建流水线
│   ├── assessment/        ← BS-CAT 分类性能报告生成
│   └── liability_enrich/  ← ★ 新增：放贷商缺口发现 + 候选验证（Step 2 见 skill）
├── input/                 ← 数据入口（.xlsx 分类报告，用户手工放入）
├── scripts/
│   ├── common.py              ← 共享工具（配置加载、路径解析、引擎元数据）
│   ├── sync_upstream.py       ← 同步层 1：GitHub → raw/（HTTPS 直取，不经过 finv）
│   ├── sync_rules.py          ← 同步层 2：finv 工作副本 ↔ raw 三方对比，只拉不推
│   ├── analyze_gaps.py        ← 统计层：发现高频未覆盖模式
│   ├── label_compare.py       ← 质检层：illion vs finv 分类差异质检报告
│   ├── search_merchant.py     ← 工具：搜规则 CSV 的商户/keyword（列名按角色识别，merchant_kb 与 gambling/rent 都支持）
│   ├── validate_candidates.py ← 验证层：语法+Schema+重叠检查
│   ├── baseline.py            ← 基线层：save 保存基线 / diff 模拟影响面
│   ├── test_rules.py          ← 测试层：确认规则在真实数据上的实际表现
│   └── apply_rules.py         ← 执行层：写入确认规则到本地 raw/
├── .claude/skills/        ← Claude Code Skill 定义（5 个，见上方「Skills」表）
├── reviews/               ← 每次运行的审核产物（label_compare.py 的输出）
│   └── <date>/
│       ├── gap_summary.json
│       ├── label_compare_report.xlsx     ← modules/assessment 的默认输入
│       ├── <engine>_candidates.csv
│       ├── liability_gaps.json           ← modules/liability_enrich Step 1
│       ├── liability_candidates.csv      ← Step 2（skill）+ Step 3 回填
│       ├── liability_evidence.json       ← Step 3 诊断明细
│       ├── validation_report.json
│       └── impact_report.json
├── reports/               ← modules/assessment 的输出（每次运行一个时间戳目录）
│   └── <YYYY-MM-DD_HHMM>/
├── baseline/              ← 基线快照
│   └── <date>/
│       └── baseline.json.gz
└── .gitignore
```

> **git 追踪范围**：`raw/` 的规则 CSV **纳入 git**（25 个 CSV + `category_catalog.json`，
> 是本地工作副本的事实记录）；
> 但 `raw/**/*.bak`（sync_rules.py 的备份）、`input/`、`reviews/`、`reports/`、`baseline/`
> 均 gitignore，只保留 `.gitkeep`。`modules/merchant_kb/cbcbcb已经清洗/` 是历史清洗数据，同样 gitignore。
>
> ⚠️ `raw/initial_rule/merchant_kb.csv`（74 MB）也在 git 里，`.git` 已因此膨胀到 145 MB。
> 「把 KB 移出 git 并清理历史」是已识别但**尚未执行**的决定。

## 工作流程

```
0. 🔁 规则同步（分析前必做，拿过期规则做分析会得出错误候选）：
   0a. `python scripts/sync_upstream.py` — GitHub(ServiFlow-AI) → `raw/`，HTTPS 直取，
       **不经过 finv**。上游没变的文件走 HTTP 条件请求、不下载正文；覆盖前强制 `.bak`；
       本地改过的文件**不覆盖**（那是「raw 领先」，属推送方向）。先用 `--dry-run` 预览
   0b. `python scripts/sync_rules.py status` → 确认 raw/ 与 finv 无漂移；
       有「finv 领先」则 `pull` 对齐（自动 .bak 备份，只拉不推）
1. 用户在 finv_category_V2 跑完流水线，导出 .xlsx 分类报告
2. 用户将 .xlsx 放入 input/ 目录
3. 用户启动 Claude Code Skill（/auto-rule-extension）
4. Claude 执行 baseline.py save → 保存当前分类状态快照
5. Claude 执行 analyze_gaps.py → 生成各引擎的 gap_summary.json
6. Claude 执行 label_compare.py → 生成 illion vs finv 分类差异质检报告（写入 reviews/<date>/）
7. Claude 读取 gap_summary + label_compare_report + 各引擎已有规则 → 逐引擎分析 → 生成候选规则 CSV
8. Claude 执行 validate_candidates.py → 语法/Schema 验证
9. Claude 执行 baseline.py diff → 影响面分析（gain/conflict）
10. 🔴 弹出规则确认窗口，用户逐引擎审核候选规则
11. Claude 执行 test_rules.py → 确认规则在实际数据上的表现
12. Claude 打印测试分析报告 → 🔴 弹出最终确认窗口
13. 用户最终确认后，Claude 执行 apply_rules.py → 写入本地 raw/
14. （可选）执行 apply_rules.py --sync_to <finv_path> 同步到 finv_category_V2
15. （可选）需要性能报告时：modules/assessment/scripts/run_report.py --with-charts
    → 读 reviews/ 最新底稿，产物落 reports/<时间戳>/
```

## 规则同步（GitHub → raw/，以及 raw/ ↔ finv）

两个**独立**方向，别混淆：

| 脚本 | 方向 | 用途 |
|------|------|------|
| `sync_upstream.py` | GitHub → `raw/` | 让规则跟上线上（分析前必做） |
| `sync_rules.py` | finv ↔ `raw/` | 看漂移、finv 领先时 pull；推送仍走 apply_rules 审批门 |

```
GitHub: EliamZhang/ServiFlow-AI
  └─ sync_upstream.py ─→ raw/                (HTTPS 直取，不经过 finv)
                            ↑
     ../finv_category_V2 ───┘  sync_rules.py  (只读比较；finv 领先时 pull)
```

`../finv_category_V2` 本身就是 ServiFlow-AI 的 git clone，但它**不参与拉取**：
要它中转就得先 git fetch + 切分支（本地在 `Finv_category_v2`、上游是 `main`），
会改变用户跑流水线的那个环境。规则 CSV 两边同构，直接下载即可。
finv 仍有两个不可替代的用途：`apply_rules.py --sync_to` 的**推送目标**，
以及**引擎源码**来源（判断规则行为必须读它）。

### 第 1 段：`sync_upstream.py`（GitHub → raw/）

```bash
python scripts/sync_upstream.py                    # 拉取并写入 raw/
python scripts/sync_upstream.py --dry-run          # 只报告差异，不写任何文件
python scripts/sync_upstream.py --branch staging   # 换上游分支
python scripts/sync_upstream.py --accept-upstream  # 本地有改动时仍采用上游版本
```

上游地址与分支取 `config.json` 的 `upstream.url` / `upstream.branch`（当前 `main`）。

**范围**：`raw/<引擎目录>/*.csv`，共 25 个 —— 含 config 的 `rule_files` 漏登记的
`income_config.csv`、`bnpl_maximum_limits.csv` 和 4 个 transfer pattern 文件；
外加 `raw/category_catalog.json`（合计 26 个）。`category_catalog.json` 不是规则，但它会
原样进 `gap_summary.json` 的 `category_catalog` 字段供 skill 读 —— 陈旧的 `owner_engine_id`
会误导引擎归属判断，历史上就这么静默漂移过（Gambling 的 owner 停在 `initial,catch_all`）。
不碰 finv，不创建本地没有的引擎目录。

**硬约束**：

- **只拉不推**。推送仍只能走 `apply_rules.py --sync_to` 的审批门
- **覆盖前强制 `.bak` 备份**（与 `sync_rules.py` 同名同位置）
- **本地自上次同步后被改动的文件拒绝覆盖**；`--accept-upstream` 是显式逃生门。
  这防止「刚审批进 `raw/`、还没推到 finv」的规则被上游更新静默抹掉
- ⚠️ **行尾归一后再比对**：上游按索引内容发 LF，而 `raw/` 与 finv 工作副本是 CRLF
  （finv 仓库 `core.autocrlf` 把 LF 检出成 CRLF，`raw/` 又从 finv 复制而来）。
  不归一的话 18 个文件会被误判成「有差异」。写入时再转回该文件既有的行尾风格，
  保持 `raw/` 与 finv 字节可比，免得 `sync_rules.py` 平白报出一堆漂移
- **HTTP 条件请求**（`If-None-Match`）：上游未变的文件不下载正文。
  `merchant_kb.csv`（74 MB）因此几乎零流量 —— 状态记在 `.upstream_state.json`（纳入 git）

除同步外它还会**只读地**报告两件事：finv 的 HEAD 落后上游多少（**引擎代码**可能过期，
而函数行为取决于引擎源码），以及「上游有、本地未跟踪的规则文件」—— 后者是发现
上游新增引擎的唯一途径。文件清单来自 `.upstream_cache/` 的 blobless 浅抓取
（只有 commit/tree、无文件正文，约 81 KB），刻意**不用** GitHub REST API：
匿名额度仅 60 次/小时，限流时这个检查会静默失效。

### 第 2 段：`sync_rules.py`（finv 工作副本 → raw/）

`raw/` 是本地工作副本，事实源是 GitHub 上的 ServiFlow-AI；finv 工作副本是它的检出。
`raw/` 与 finv 两边各自演进，**不要手工 cp**：

```bash
python scripts/sync_rules.py status                          # 漂移总览（默认动作）
python scripts/sync_rules.py status --engine rent --diff     # 看某个引擎的行级差异
python scripts/sync_rules.py pull                            # finv → raw（自动 .bak 备份）
python scripts/sync_rules.py pull --dry-run                  # 只预览
python scripts/sync_rules.py adopt                           # 把当前状态登记为新基线
```

判定依据仓库根的 **`.sync_state.json`**：记录上次登记时**两侧各自**的内容 sha256。
据此三方对比——只有一侧变了、还是两侧都变了。记录的是内容哈希而非时间戳，
因此与机器无关，克隆到新机器后同步状态依然有效。

| 条件 | 状态 | 动作 |
|------|------|------|
| 两侧 hash 均未变 | 一致 | — |
| 仅 finv 变 | finv 领先 | pull |
| 仅 raw 变 | raw 领先 | `apply_rules.py --sync_to` 推上去 |
| 两侧均变 | 冲突 | 只报告，人工裁决 |
| 清单中无记录 | 未登记 | 人工确认后 adopt |
| adopt 时两侧就不同 | 已登记分叉 | 人工裁决（不自动 pull） |

**硬约束**：

- **只拉不推**。推送必须走 `apply_rules.py --sync_to`（在审批门之后），本脚本不提供 push
- **pull 默认只处理安全情形**：状态是 finv 领先，**且登记基线本身是收敛的**
  （基线 `raw_sha == finv_sha`）。第二条保证 raw 当前内容就是「上次同步后的 finv 内容」，
  覆盖它不会丢掉任何本地改动。基线本身就分叉的文件默认拒绝 pull
- **`--accept-finv` 是显式逃生门**：人工看过 `--diff` 后用它表达「我决定采用 finv 侧」，
  未登记/冲突/已登记分叉都放行，覆盖前强制 `.bak` 备份
- **`merchant_kb.csv` 默认不参与 pull**（74 MB），要拉必须显式加 `--include-large`
- 文件清单 = config 的 `rule_files` ∪ 两侧目录里实际存在的 `*.csv`。
  只信 config 不够——实测 config 的 `rule_files` 是「规则创作清单」而非文件全量
  （liability 少 `bnpl_maximum_limits.csv`、transfer 少 4 个 pattern/exclusion 文件、
  income 少 `income_config.csv`），而这些都会实质影响流水线行为

> ⚠️ **同步状态反映的是 `raw/` 与 finv 的一致性，不代表行为一致。** 例：rent_engine 是
> v2.0 双层引擎，若某个应用环境仍跑旧版引擎，同一份 `rent_rules.csv` 行为会不同。
> 判断「哪边新」用 sync_rules；判断「跑起来什么样」必须看 finv 的引擎源码。
>
> **2026-09-15 状态**：25 个文件全部「一致」（含新增的 `gambling/gambling_rules.csv`）。

## 引擎规则使用机制

每个引擎的文本归一化、匹配逻辑、CSV Schema 约束等代码级细节已在上方各节中详细说明。生成候选规则前必须确认目标引擎的匹配逻辑兼容。

关键差异速查：

| 引擎 | 文本归一化 | 匹配方式 | 多匹配策略 |
|------|-----------|---------|-----------|
| initial | `clean_text()` 大写 [A-Z0-9]，去通道前缀 | Aho-Corasick + 全词边界 | 最长 keyword 优先 |
| transfer | `.lower()` 保留特殊字符 | `str.contains` 向量化 regex | 先匹配先得（priority 排序） |
| dishonour | 原始 text（case=False） | `str.contains` keyword/regex | OR（任意命中） |
| income | `clean_text_with_seams()` 大写 [A-Z0-9]+seams | 多阶段复合决策树 | 多信号综合 + 金额阈值 |
| liability | 混合（取决于子模块） | 多子模块 pipeline | 各模块独立排序 |
| all_other_credit | 原始 text（case=False） | `str.contains` **仅 keyword** | OR |
| fee | 空格归一化，**大小写不敏感**(IGNORECASE) | `re.search` | 先匹配先得（priority 升序） |
| rent | **双层**：institution 层 `clean_text_with_channel_prefix()` 大写；keyword 层 `clean_text()` 大写 | institution: Aho-Corasick + 全词；keyword: `str.find` + 全词 / `re.search` | institution 最长 keyword 优先；keyword 层最高 confidence 优先 |
| gambling | **双层**（同 rent） | 同 rent | 同 rent；但 rule 层走 `exclude_prior_claimed` 对前置认领让位 |
| catch_all | `clean_text()` 大写 [A-Z0-9] | `str.find` + 全词 / `re.search` | 最高 confidence 优先 |

⚠️ 生成规则时必须注意：
- **文本归一化对齐**：keyword 规则在 `clean_text()` 后的文本上匹配（标点全部移除！），regex 规则的环境因引擎而异
- **all_other_credit 只用 keyword**：regex 规则被加载但不会匹配
- **fee 大小写不敏感**（自 2026-08-20 起）：`^MONTHLY\s+FEE$` 也能匹配 `monthly fee`；且规则带 `unclassified_only`/`dr_cr` 可选列
- **income 不是简单关键词匹配**：必须满足金额阈值 + payer_key + 频率模式
- **transfer 只能输出 Internal Transfer / External Transfers**：不能生成其他分类
- **gambling 认领对 income/liability 是终局**：赌博行不会被这两者重新认领；反过来
  gambling 的 rule 层对**所有**更早引擎（transfer/initial/dishonour）的认领让位 ——
  商户名若已命中 merchant_kb，再往 rule 层加规则不会有任何效果
- **initial_engine 的通道前缀被自动去除**：不需要在 pattern 中包含 `DEBIT CARD PURCHASE ` 等前缀

## 重要约定

- 任何规则写入操作前必须经过人工确认，不可自动执行
- 新规则保持各引擎已有规则的 confidence 范围和命名风格
- 输入必须是 finv_category_V2 流水线处理后的 .xlsx 报告，包含 classification_status 列
- `raw/` 目录的规则文件是本地工作副本，初始从 finv_category_V2 复制，后续由 apply_rules.py 维护。
  **finv 侧改动要同步下来时用 `sync_rules.py pull`**（会自动 `.bak` 备份），不要手工 cp。
  pull 是「只拉不推」——推送仍然只能走 apply_rules.py 的审批门
- 同步到 finv_category_V2 后，需在 finv_category_V2 中手动运行 baseline.py 更新基线
- **`raw/initial_rule/merchant_kb.csv` 就是 finv 的线上规则文件**（不是普通中间产物）。
  它同时是 `modules/merchant_kb` 的产物，列结构由 initial_engine 的 `usecols` 锁定为 3 列
- **生成每个候选规则前，必须参考本文件上方对应引擎的章节确认文本归一化方式与匹配逻辑**
- **候选 CSV 里只有 `scripts/common.py` 的 `META_COLUMNS` 列会被剥离**，其余列一律当规则数据写进 `raw/`。
  需要新的诊断字段（如联网来源 URL）时：要么加进 `META_COLUMNS`（已有 `status`、`hit_count`、
  `risk_level`、`illion_category`、`samples`、`target_file`、`evidence_source`），
  要么写进旁边的 JSON 报告 —— **不要直接往候选 CSV 加列**
- **`apply_rules.py` 只写入 `status == "confirmed"` 的行**（`apply_rules.py:137`，
  逐字符比较）。`☐ confirm`、`✗ 零增益`、`✗ 死规则` 等占位值都不等于 `confirmed`，是安全的。
  新增任何 status 取值时**绝不能等于 `confirmed`**
