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

| 优先级 | engine_id | 规则文件 | 匹配方式 | 规则数 |
|--------|-----------|----------|----------|--------|
| 1 | initial | `merchant_kb.csv` | Aho-Corasick keyword | ~9,000 |
| 100 | transfer | 8 个 CSV | keyword + regex | ~150 |
| 150 | dishonour | `dishonour_rules.csv` | keyword + regex | ~15 |
| 200 | income | `income_pattern_rules.csv`, `income_config.csv` | regex | ~106 |
| 300 | liability | 8 个 CSV | keyword + regex | ~500+ |
| 400 | all_other_credit | `all_other_credit_rules.csv` | keyword | ~30 |
| 500 | fee | `fee_classification_rules.csv` | regex | ~87 |
| 999 | catch_all | `catch_all_rules.csv` | keyword + regex | ~280 |

### 每个引擎的职责和匹配逻辑

#### 1. initial_engine (优先级 1) — 商户名 → 分类

**职责**：通过匹配商户名称来分类交易。这是第一个运行的引擎，负责精确识别已知商户。**对于任何看起来像商户名/品牌名的未分类交易，必须优先排查此引擎。**

**匹配逻辑**：
- 从 `merchant_kb.csv` 加载 ~256 万商户的 ~3 万 keyword 变体
- 使用 Aho-Corasick 自动机做多模式匹配（线性时间，高效）
- 匹配 transaction text 中的 keyword → 返回 merchant 对应的 category

**适合添加的规则**：
- 特定的商户名 → category 映射（如 "LAVERTON SUPERMARKET" → "Groceries"）
- 你需要知道这个商户的**确切名称**和**应该分到哪个 category**
- 很多商户的 category 为空 → 需要补充分类
- **新增商户**：merchant_kb 中不存在的商户 + 其 keywords

**不适合**：
- 通用关键词（如 "SUPERMARKET"）→ 应该给 catch_all

**排查优先级**：任何未分类的 merchant-like pattern，**必须先执行以下排查**：
1. 用 `scripts/search_merchant.py` 搜索 merchant_kb 中是否存在该商户
2. 如果存在但没有 category → 补充 category 建议
3. 如果存在且有 category → 检查 keyword 是否遗漏（交易 text 中的变体未被 keywords 覆盖）
4. 如果不存在 → 建议新增商户 + keywords + category

#### 2. transfer_engine (优先级 100) — 转账识别

**职责**：识别内部转账（Internal Transfer）和外部转账（External Transfers），识别交易对手方。transfer_engine **只输出两个 category**：`Internal Transfer` 和 `External Transfers`。

**规则文件及职责**：
| 文件 | 用途 |
|------|------|
| `transfer_counterparty_rules.csv` | keyword → counterparty 映射 |
| `transfer_external_high_confidence_rules.csv` | 高置信外部转账 regex（category=External Transfers） |
| `transfer_external_medium_confidence_rules.csv` | 中置信外部转账 regex |
| `transfer_internal_regex_rules.csv` | 内部转账 regex |
| `transfer_group_exclusion_patterns.csv` | **排除组**：匹配这些的会从结果中过滤掉 |
| `transfer_row_exclusion_patterns.csv` | **行排除**：单行满足条件则排除 |
| `transfer_indicator_patterns.csv` | 转账指标检测 |
| `transfer_pairing_exclusions.csv` | 配对排除规则 |

**匹配逻辑**：
- 先做内部转账匹配（pairing + regex + indicator）
- 再做外部转账匹配（regex），用 priority 排序
- 最后用 exclusion patterns 过滤（排除不应被识别为转账的交易）
- 匹配成功后设置 category（Internal Transfer / External Transfers）+ counterparty

**适合添加的规则**：
- 新出现的转账描述模式（regex）→ `transfer_external_*_confidence_rules.csv` 或 `transfer_internal_regex_rules.csv`
- 特定转账对手方 → `transfer_counterparty_rules.csv`

**不适合**：
- 赌博/博彩/游戏平台等 → 应优先排查 initial_engine 的 merchant_kb.csv，若为通用文本则归 catch_all
- 任何非转账分类（Gambling、Entertainment 等）→ 不在 transfer 职责范围内

#### 3. dishonour_engine (优先级 150) — 拒付检测

**职责**：检测拒付/退票交易（dishonoured payments）。

**匹配逻辑**：
- 加载 `dishonour_rules.csv`（`rule_type, pattern, required_terms`）
- keyword 模式：检查 pattern 是否在 text 中
- regex 模式：正则匹配
- `required_terms`：可选的必须同时出现的词

**适合添加的规则**：
- 银行拒付/退票消息的特定模式
- 需要三个字段：rule_type（keyword/regex）、pattern、required_terms（可选）

#### 4. income_engine (优先级 200) — 收入识别

**职责**：识别工资、Centrelink 福利等收入类交易。

**匹配逻辑**：
- 加载 `income_pattern_rules.csv`（`pattern_group, pattern, match_type, description`）
- 只用 regex 匹配（match_type = "regex"）
- pattern_group 分组：`strong_wage`、`possible_wage`、`centrelink` 等
- **结合金额阈值**（来自 `income_config.csv`）：MIN_NORMAL_WAGE_AMOUNT=100、COMMON_WAGE_AMOUNT_MIN=300~MAX=10000 等
- 金额不在范围内的不会被分类为收入

**⚠ 特别说明**：income 引擎**不只是文本匹配**——它还需要金额满足阈值。单纯的关键词匹配不够。因此 Auto_Rule_Extension 为 income 生成规则时需要特别保守。

**适合添加的规则**：
- 新出现的明确工资入账描述模式（regex）
- 归类到正确的 pattern_group（strong_wage / possible_wage / centrelink）

#### 5. liability_engine (优先级 300) — 负债/贷款识别

**职责**：识别各类贷款还款、债务催收、信用卡还款等。

**规则文件及用途**：
| 文件 | Schema | 用途 |
|------|--------|------|
| `counterparty_keyword_rules.csv` | `keyword, counterparty, product_type, match_type` | 对手方→产品类型映射 |
| `credit_card_rules.csv` | `priority, account_type, dr_cr, bank, match_type, keyword, min_prefix_len, counterparty, product_type, exclude_pattern` | 信用卡还款 |
| `home_loan_car_loan_rules.csv` | 同上 schema | 房贷/车贷 |
| `debt_collection_rules.csv` | 同上 | 债务催收 |
| `debt_consolidation_rules.csv` | 同上 | 债务整合 |
| `dishonours_rules.csv` | 同上 | 拒付（liability 视角） |
| `overdrawn_rules.csv` | 同上 | 透支 |
| `bnpl_maximum_limits.csv` | BNPL 限额配置 | 先买后付 |

**匹配逻辑**：
- 多种规则类型，按 priority 排序执行
- 支持 `min_prefix_len`（前缀最小长度，用于金额前缀匹配）
- 支持 `exclude_pattern`（排除模式）
- 排除已被 income 分类的交易
- 设置 counterparty + product_type

**适合添加的规则**：
- 新贷款机构的对手方关键词 → `counterparty_keyword_rules.csv`
- 新出现的贷款产品描述模式 → 对应类型的 CSV

#### 6. all_other_credit_engine (优先级 400) — 杂项入账

**职责**：捕获不属于收入的杂项入账（退款、返现、报销等）。

**匹配逻辑**：
- 加载 `all_other_credit_rules.csv`（`rule_type, pattern, required_terms`）
- 与 dishonour 使用相同的 loader
- keyword 和 regex 两种模式

**适合添加的规则**：
- 退款/返现/报销类关键词
- 入账类但非工资非转账的文本模式

#### 7. fee_engine (优先级 500) — 费用识别

**职责**：识别各类费用：国际交易费、ATM 费、账户费、透支/拒付/滞纳金、预付现金费、第三方维护费等。

**匹配逻辑**：
- 加载 `fee_classification_rules.csv`（`priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description`）
- 只用 **regex** 匹配
- 按 priority 排序，高 priority 先匹配
- `zero_amount_reject`：金额为 0 时是否跳过（t/f）

**适合添加的规则**：
- 新出现的费用描述模式（regex）
- category 固定为 "Fees"
- 需要合理设置 priority（在已有规则的 priority 范围外）

#### 8. catch_all_engine (优先级 999) — 兜底关键词

**职责**：**最后的兜底引擎**。用**描述性通用关键词**推断分类（如 "BAKERY"→"Dining Out"、"PHARMACY"→"Health"）。

**核心定位**：catch_all 处理的是**泛化关键词**，不是具体商户。如果某个 pattern 明确是一个商户名/品牌名，应该优先去 initial_engine 的 merchant_kb.csv 排查，而不是直接加 catch_all 规则。

**匹配逻辑**：
- 加载 `catch_all_rules.csv`（`rule_name, category, pattern, match_type, confidence`）
- 支持 keyword 和 regex
- 按 confidence 降序排列，**最高 confidence 的匹配胜出**
- 只能对尚未被前面引擎分类的交易生效

**适合添加的规则**：
- **纯通用关键词** → category 映射（如 "RESTAURANT" → "Dining Out"）
- 单个常见词或词组，不指向特定商户
- confidence 通常 0.70-0.85
- 非转账、非费用、非收入、非负债的通用消费关键词

**不适合**：
- **任何具体商户名** → 必须先去 initial_engine 的 merchant_kb.csv 排查
- 转账相关关键词 → transfer_engine
- 费用相关关键词 → fee_engine
- 收入相关关键词 → income_engine

### 各引擎规则 CSV Schema

dishonour_engine, all_other_credit_engine (共用 loader):
  `rule_type, pattern, required_terms`

fee_engine:
  `priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description`

catch_all_engine:
  `rule_name, category, pattern, match_type, confidence`

income_engine:
  `pattern_group, pattern, match_type, description`

liability_engine (counterparty_keyword):
  `keyword, counterparty, product_type, match_type`

liability_engine (credit_card, home_loan_car_loan etc.):
  各子文件 schema 不同，见各文件 header

transfer_engine:
  `transfer_counterparty_rules.csv`: `keyword, counterparty, match_type`
  `transfer_external_high_confidence_rules.csv`: `priority, rule_name, category, pattern, dr_cr, description`
  `transfer_external_medium_confidence_rules.csv`: `priority, rule_name, category, pattern, dr_cr, description`
  `transfer_internal_regex_rules.csv`: `priority, rule_name, pattern, dr_cr, description`
  其余为排除/指标类规则，通常不自动生成

initial_engine:
  `merchant_name, keywords, link, category, category_source, keyword_updated_at, category_updated_at`

### 流水线覆盖规则

- 所有引擎按 priority 升序执行
- 后面的引擎覆盖前面的结果（行级覆盖 finv_category + counterparty）
- liability 引擎排除已被收入分类的交易
- 最终未被任何引擎认领的标记为 unclassified

## 项目目录结构

```
D:\project\Auto_Rule_Extension\
├── CLAUDE.md              ← 本文件
├── SKILL.md               ← Claude Code Skill 提示词
├── config.json            ← 配置（引擎定义、分析参数）
├── raw/                   ← 各引擎规则 CSV 的本地副本
│   ├── initial_rule/merchant_kb.csv
│   ├── transfer_rule/*.csv
│   ├── catch_all_rule/catch_all_rules.csv
│   └── ...
├── input/                 ← 数据入口（.xlsx 分类报告）
├── scripts/
│   ├── analyze_gaps.py       ← 统计层：发现高频未覆盖模式
│   ├── search_merchant.py    ← 工具：搜索 merchant_kb.csv 中的商户/keyword
│   ├── validate_candidates.py ← 验证层：语法+Schema+重叠检查
│   ├── baseline.py           ← 基线层：save 保存基线 / diff 模拟影响面
│   └── apply_rules.py        ← 执行层：写入确认规则到本地 raw/
├── reviews/               ← 每次运行的审核产物
│   └── <date>/
│       ├── gap_summary.json
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
6. Claude 读取 gap_summary + 各引擎已有规则 → 逐引擎分析 → 生成候选规则 CSV
7. Claude 执行 validate_candidates.py → 语法/Schema 验证
8. Claude 执行 baseline.py diff → 影响面分析（gain/conflict）
9. Claude 展示审核报告，用户逐引擎确认
10. 用户确认后，Claude 执行 apply_rules.py → 写入本地 raw/
11. （可选）执行 apply_rules.py --sync_to <finv_path> 同步到 finv_category_V2
```

## 重要约定

- 任何规则写入操作前必须经过人工确认，不可自动执行
- 新规则保持各引擎已有规则的 confidence 范围和命名风格
- 输入必须是 finv_category_V2 流水线处理后的 .xlsx 报告，包含 classification_status 列
- `raw/` 目录的规则文件是本地工作副本，初始从 finv_category_V2 复制，后续由 apply_rules.py 维护
- 同步到 finv_category_V2 后，需在 finv_category_V2 中手动运行 baseline.py 更新基线
