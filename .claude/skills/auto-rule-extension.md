---
name: auto-rule-extension
description: 为 finv_category_V2 交易分类流水线的 8 个引擎自动发现并补充分类规则。用户说"补充规则"、"发现盲区"、"分析未覆盖交易"、"扩展规则库"、"/auto-rule-extension" 时触发。
---

# Auto Rule Extension Skill

为 finv_category_V2 交易分类流水线的 8 个引擎自动发现并补充规则。

## 语言要求（强制）

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

本 Skill 共 8 个阶段，**全程自动推进**，仅在两个节点使用 AskUserQuestion 等待人工审批：

```
阶段一 → 阶段二 → 阶段三 → 阶段四  （AI 自动完成，无需人工）
                              ↓
                    🔴 阶段五：逐引擎 AskUserQuestion 审批
                              ↓ 确认完成 → 自动推进
                    🔴 阶段六：自动跑 test_rules.py
                              ↓
                    🔴 阶段七：AskUserQuestion 最终确认
                              ↓ 确认完成 → 自动推进
                    阶段八：自动写入规则
```

| 阶段 | 名称 | 触发方式 | 产出 |
|------|------|----------|------|
| 一 | 数据准备与缺口发现 | 自动 | `gap_summary.json` + `label_compare_report.xlsx` |
| 二 | 解读分析结果 | 自动 | 理解 gaps + disagreements + label 差异 |
| 三 | 逐引擎智能分析 | 自动 | `*_candidates.csv` |
| 四 | 验证（语法 + 影响面） | 自动 | `validation_report.json` + `impact_report.json` |
| 五 | 🔴 逐引擎 AskUserQuestion 审批 | **AskUserQuestion 等待人工** | status 列更新为 confirmed/review/rejected |
| 六 | test_rules.py 测试 | 阶段五确认后自动 | `test_report.json` |
| 七 | 🔴 AI 分析报告 + AskUserQuestion 最终确认 | **AskUserQuestion 等待人工** | 最终确认规则 JSON |
| 八 | 执行写入 | 阶段七确认后自动 | 规则写入 `raw/` + 可选 sync |

---

### 阶段一：数据准备与缺口发现

1. 检查 `input/` 目录，取最新的 `.xlsx` 文件，**记录文件名**（后续阶段复用）
2. **确定输出目录**：使用格式 `reviews/<YYYY-MM-DD_HHMM>/`（日期+时间，同一天多次运行不覆盖）
3. 执行 `scripts/analyze_gaps.py`：
   ```
   python scripts/analyze_gaps.py --input input/<latest>.xlsx --output reviews/<date>/
   ```
4. 执行 `scripts/label_compare.py`：
   ```
   python scripts/label_compare.py --input input/<latest>.xlsx --output reviews/<date>/label_compare_report.xlsx
   ```
5. 读取 `reviews/<date>/gap_summary.json`

**产出**：`gap_summary.json` + `label_compare_report.xlsx`

### 阶段二：解读分析结果

**同时阅读两份报告**，它们互相补充：

#### A. `gap_summary.json` — 未覆盖模式（盲区发现）

聚焦于 `classification_status == unclassified` 的交易。每种模式附带：
- `pattern_norm`：归一化后的文本模板
- `count`：出现频次
- `illion_category`：illion 给的分类标签（可能为空）
- `samples`：原始交易描述样例（最多 5 条）
- `third_parties`：illion 识别的商户名列表

#### B. `label_compare_report.xlsx` — illion vs finv 分类差异（精准定位）

报告包含 3 个 Sheet：

**00_核心对比**：核心指标、逐 Category 差异与优化优先级（P1/P2/P3）、主要差异流向。P1 = 高差异量 + 高贡献率，最优先处理。

**01_差异诊断地图**：Top N 差异流向、完整数量矩阵（行=illion, 列=finv）、差异流向占比矩阵。

**03_排查明细**：按排查优先级排序的逐笔差异明细，包含交易文本、金额、dr_cr、原分类、新分类、排查建议。

**差异类型 → 规则挖掘方向**：

| 差异类型 | 规则挖掘方向 |
|---------|------------|
| 分类不一致 (mismatch) | finv 分类错误，需要修正规则或添加排除 |
| 仅 illion 有 (reference_only) | finv 漏识别，需要为新类别添加规则 |
| 仅 finv 有 (candidate_only) | finv 多识别，检查是否过度分类 |

**分析策略**：先看 `00_核心对比` P1/P2 Category → 再看 `01_差异诊断地图` 看流向 → 用 `03_排查明细` 看具体文本 → 结合 `gap_summary.json` 形成完整策略。

#### C. illion 标签的利用策略

illion 标签是半自动标注，不是 100% 准确的 ground truth。使用策略：

| 场景 | 策略 |
|------|------|
| illion 标签存在 + 未分类 | **高置信候选规则**，confidence 可偏高（0.80-0.90） |
| illion 标签存在 + 已分类但两者不一致 | 需判断：illion 更合理→修正规则；finv 更合理→忽略 illion；粒度不同→取决于引擎职责 |
| illion 标签为空 + 未分类 | 保守估计 category，降低 confidence |

---

### 阶段三：逐引擎智能分析（核心）

#### 3.0 重审 gap_summary 的引擎归属（强制，不可跳过）

**analyze_gaps.py 的引擎分配基于表层文本特征，不保证语义正确。AI 必须在逐引擎分析前，跨引擎重审所有 pattern 的归属。**

常见分配错误：

| gap_summary 分配的引擎 | 实际应归属 | 原因 |
|------------------------|-----------|------|
| transfer（赌博平台） | **initial** | 文本含 "VISA DEBIT PURCHASE CARD" 被误判为转账，实际是赌博商户 |
| catch_all（商户名/品牌名） | **initial** | 任何品牌名/平台名/店铺名都不属于 catch_all |
| catch_all（费用描述） | **fee** | 含 FEE 关键词的应优先考虑 fee_engine |
| catch_all（入账描述） | **all_other_credit** | 利息、退款等入账类应优先考虑 all_other_credit |
| catch_all（转账描述） | **transfer** | Fast Transfer、Osko Payment 等 |
| initial（内部转账） | **transfer** 或 skip | "EVERYDAY ROUND UP" 是内部转账，不是商户 |

**执行方式**：逐条审视每个 pattern，回答：
1. 这是商户名/品牌名吗？→ 是 → 归 initial（不管 gap_summary 怎么说）
2. 这是费用描述吗？→ 是 → 归 fee
3. 这是入账交易吗？→ 是 → 优先归 all_other_credit
4. 这是转账描述吗？→ 是 → 归 transfer
5. 归入正确引擎后，再继续后续分析。

**纠偏后**，transfer 引擎中的赌博平台全部移到 initial，catch_all 中的商户名全部移到 initial。

---

#### 3.1 商户名 vs 通用文本：判断 + 验证（铁律）

## 🚨 商户名/品牌名 = initial 引擎，绝不进 catch_all 🚨

**这是本 Skill 最高优先级的铁律。每次生成候选规则前必须逐条自检。**

**禁止事项**：

| 禁止行为 | 正确做法 |
|----------|----------|
| 把品牌名/平台名放入 catch_all 做 keyword | 走 3.1 流程：查 merchant_kb → 新增或更新商户 |
| 把赌博网站名放入 catch_all | 新增为 initial merchant，category=Gambling |
| 把在线服务平台名放入 catch_all | 新增为 initial merchant，category 按 illion 标签确定 |
| 跳过 merchant_kb 查询直接生成 catch_all 规则 | **必须**先查 merchant_kb |
| 看到 `pattern_type=merchant` 还分配到非 initial 引擎 | pattern_type 已经是明确信号，直接走 initial 流程 |

**判断流程（强制，每条 gap pattern 必须走完）**：

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

**反面教材**：

```
❌ 错误：PLAYTKA 是赌博平台 → catch_all keyword: PLAYTKA → Gambling
   错因：PLAYTKA 是品牌名，catch_all 只收通用描述词。
✅ 正确：查 merchant_kb → 未找到 → 新增 initial merchant: PLAYTKA, keywords=PLAYTKA, category=Gambling

❌ 错误：GARETON BV → catch_all keyword: GARETON → Gambling
✅ 正确：查 merchant_kb → 未找到 → 新增 initial merchant: GARETON BV, keywords=GARETON, category=Gambling

✅ 正确（通用文本）：INTEREST PAID 是通用利息描述 → 决策树: 入账类 → all_other_credit
```

**商户真实性验证（三步）**：

**Step A: 验证商户真实性** — 不能假设从交易文本中截取的名字就是真实商户名。
1. **illion third_party 交叉验证**（优先）：检查 gap_summary 中 `third_parties` 字段，非空且不是 "nan" → 直接采信
2. **联网搜索验证**：用商户名 + 地点搜索，例: `WebSearch("BROKEN HILL MUSICIANS club Australia")`
3. **都无法验证 → 丢弃该候选**，标记为 `unverified_merchant`

**Step B: 查 merchant_kb**：
```bash
python scripts/search_merchant.py --search "DORSETT GOLD COAST HOTEL"       # 精确匹配
python scripts/search_merchant.py --search "LAVERTON SUPERMARKE" --fuzzy    # 模糊搜索
python scripts/search_merchant.py --search "BETR" --field keywords          # keyword中搜索
```

**Step C: 根据查询结果采取行动**：
1. **商户已存在 + 已有 category** → 为什么交易没被匹配到？排查原因（keyword 遗漏变体？文本截断？），新增 keyword 变体写入 `initial_keyword_updates.csv`
2. **商户已存在 + category 为空** → 根据 illion 标签补充分类，写入 `initial_category_updates.csv`
3. **商户不存在 + 通过真实性验证** → 新商户，写入 `initial_candidates.csv`。⚠️ keyword 不能太短（≥4 字符），不能太泛
4. **商户不存在 + 无法验证真实性** → 直接丢弃

---

#### 3.2 引擎选择规则 + 约束速查

各引擎的适用范围：

| 引擎 | 处理什么 | 不处理什么 |
|------|----------|------------|
| **initial** | 具体商户名/品牌名的识别 | 通用关键词 |
| **transfer** | 仅 Internal Transfer / External Transfers | Gambling、Entertainment 等非转账分类 |
| **dishonour** | 银行拒付/退票消息 | — |
| **income** | 工资/Centrelink 等收入（需金额阈值） | 不满足金额阈值的文本 |
| **liability** | 贷款还款、信用卡还款、债务催收等 | 已被 income 分类的交易 |
| **all_other_credit** | 退款/返现/报销/**利息**等杂项入账 | 非入账类交易 |
| **fee** | 各类银行/账户费用 | — |
| **catch_all** | **仅**通用描述性关键词（非商户名），**且必须是其他 7 个引擎都无法处理的** | 任何具体商户名/品牌名；能被其他引擎处理的 |

**🚨 catch_all 硬性前置检查（分配任何规则到 catch_all 前必须通过）**：

```
1. 这是入账交易（credit）吗？→ 是 → 先检查 all_other_credit
   例: INTEREST PAID → all_other_credit，不是 catch_all
2. 这是费用描述吗？→ 是 → 归 fee_engine
   例: ATM OPERATOR FEE → fee
3. 这是转账描述吗？→ 是 → 归 transfer_engine
4. 这是商户名/品牌名吗？→ 是 → 走 3.1 流程 → initial
5. 只有以上全部为「否」时，才归 catch_all
   例: "RESTAURANT"（通用词，不是具体商户名）→ catch_all ✓
```

**代码级规则约束速查（生成规则前必读）**：

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

---

#### 3.3 对每个 gap 模式做决策

对已完成 3.1 商户排查的非商户 pattern，按以下决策树处理：

```
1. 这个模式有 illion 标签吗？
   - 有 → illion 标签作为 category 参考 → 跳到 3
   - 没有 → 自己判断 → 跳到 2

2. （无 illion 标签）我能可靠地判断分类吗？
   - 能（特征词非常明确）→ 给出保守的 category
   - 不能（太泛，如纯数字串、银行内部消息）→ 跳过
   - 不确定 → 标注为 "需要人工判断"

3. 这个模式是噪声吗？
   - 银行系统消息 → 跳过
   - 临时性事件 → 跳过
   - 已有规则可以覆盖 → 跳过，不重复生成

4. 匹配到哪个引擎？（按语义判断，不是按 gap_summary 中的引擎分组）
   - 转账描述 → transfer_engine
   - 拒付/退票 → dishonour_engine
   - 工资/Centrelink + 金额合理 → income_engine
   - 贷款/信用卡还款/催收 → liability_engine
   - 退款/返现/利息 → all_other_credit_engine
   - 费用 → fee_engine
   - 通用消费关键词（非商户名）→ catch_all_engine

5. 我有多确定不会误伤？（宁可漏判不误判）
   - 模式高度特异（≥3个词）→ confidence 0.85-0.90
   - 模式中等特异（2个词）→ confidence 0.80-0.85
   - 模式偏泛（1个常见词 + 有 illion 标签佐证）→ confidence 0.70-0.75
   - 模式太泛 + 无 illion 标签 → 直接跳过

6. 选择 match_type：优先 keyword（更安全），其次 regex
```

---

#### 3.4 生成候选规则

##### 3.4.1 Initial 引擎特殊流程：两轮生成（强制）

**🚨 Initial 引擎的候选规则必须分两轮生成，禁止一轮全部当新商户处理。**

**原因**：gap_summary 中的 pattern 可能是已有商户的不同写法（如 "BIG W SALISBURY" 只是 "Big W" 的变体），直接新建会导致 merchant_kb 中出现大量重复商户。

**第一轮：模糊匹配已有商户 → 生成 keyword 补充**

```
对每个归入 initial 的 pattern：
  1. 提取核心商户名（去除通道前缀、卡号、日期、地点后缀等）
  2. 用核心商户名在 merchant_kb 中模糊搜索：
     a. 精确匹配 merchant_name
     b. 关键词匹配（取核心名的每个词，在 keywords 列中搜索）
     c. 品牌名映射（维护 BRAND_MAP 字典：常见品牌名→KB中名称）
        例: "MCDONALDS" → "McDonald's", "WW METRO" → "Woolworths",
             "BP KAMBALDA" → "BP", "DIDIMOBILITY" → "DiDi"
  3. 匹配成功 → 生成 keyword 补充条目：
     - 写入 reviews/<date>/initial_keyword_updates.csv
     - 列: merchant_name, existing_keywords, new_keyword, category,
            illion_category, hit_count, samples, status
     - 操作: 在已有商户的 keywords 列追加新变体，不新建条目
```

**第二轮：剩余未匹配 → 生成新商户**

```
  4. 匹配失败 → 这才是真正的新商户：
     - 写入 reviews/<date>/initial_candidates.csv
     - 验证商户真实性（illion third_party 交叉验证或 WebSearch）
     - 无法验证的 → 标记 🔴高风险 或直接丢弃
```

**🚨 keyword 格式强制规范（生成新 merchant 和补充 keyword 都必须遵守）**：

keyword 必须是 `clean_text()` 归一化后的形式，因为引擎在加载和匹配时都会经过 `clean_text()` 处理。格式不符合的 keyword 永远无法匹配。

```
规则：
  1. 全部大写（UPPERCASE）
  2. 仅保留 [A-Z0-9 ]，所有特殊字符替换为空格
     去除的字符包括: * / & . , ' - ( ) + # $ @ ! 等
  3. 压缩多余空格（多空格→单空格，首尾去空格）

示例：
  "McDonald's"        → "MCDONALD S"
  "Temu.com"          → "TEMU COM"
  "ALH Venues/Swanston & Flinders" → "ALH VENUES SWANSTON FLINDERS"
  "Patreon*"           → "PATREON"
  "OPENAI *CHATGPT"    → "OPENAI CHATGPT"
  "Dumplings & More"   → "DUMPLINGS MORE"
  "Paramount+"         → "PARAMOUNT"
  "bestpdf.com"        → "BESTPDF COM"
  "ZLR*Vermont Convenience" → "ZLR VERMONT CONVENIENCE"
```

**品牌名映射字典**（持续积累，每次运行后补充新发现的映射）：

```python
BRAND_MAP = {
    'MCDONALD': "McDonald's", 'UBER': 'Uber', 'BIG W': 'Big W',
    'BP ': 'BP', 'TELSTRA': 'Telstra', 'OPTUS': 'Optus',
    'NETFLIX': 'Netflix', 'KFC': 'KFC', 'KMART': 'Kmart',
    '7 ELEVEN': '7-Eleven', 'WOOLWORTHS': 'Woolworths',
    'WW METRO': 'Woolworths', 'IGA': 'IGA',
    'NEDS': 'Neds', 'LADBROKES': 'Ladbrokes', 'BET365': 'Bet365',
    'TAB': 'TAB', 'POINTSBET': 'Pointsbet', 'UNIBET': 'Unibet',
    'TRANSPORTFORNSW': 'Transport for NSW',
    'DIDI': 'DiDi', 'RITCHIES': 'Ritchies', 'OTR': 'OTR',
    'PRIME VIDE': 'Prime Video', 'COLES': 'Coles',
    'GOOGLE PLAY': 'Google', 'AGL': 'AGL Energy',
    # 每次运行后如有新发现，追加到此字典
}
```

##### 3.4.1.1 keyword 变体发现方法（强制遵循）

生成 keyword 变体时，**必须先看 gap_summary.json 中的 `samples`（原始交易文本）**，与 merchant_kb 中已有关键词逐条对比，找出 clean_text 前后的差异。

**核心诊断流程**：

```
对每个 gap pattern：
  1. 取 samples 中的原始交易文本（如 "EFTPOS PUNTIQ.COM"）
  2. 模拟 clean_text：大写 + 仅保留 [A-Z0-9 ] + 压缩空格
     → "EFTPOS PUNTIQ COM"
  3. 取 KB 中已有关键词（如 "PUNTIQCOM"）
  4. 对比：clean_text后的文本中包含该关键词吗？
     → "PUNTIQ COM" 中找不到子串 "PUNTIQCOM" → 匹配失败！
  5. 生成缺失的变体：在 clean_text 后的文本中提取实际出现的词
     → 应添加 "PUNTIQ COM"（而非 "PUNTIQCOM"）
```

**常见 clean_text 导致的匹配失败模式**：

| 原始交易文本 | clean_text后 | KB已有keyword | 结果 | 修复 |
|-------------|-------------|---------------|------|------|
| `PUNTIQ.COM` | `PUNTIQ COM` | `PUNTIQCOM` | ❌ 不匹配 | 加 `PUNTIQ COM` |
| `BET365.COM` | `BET365 COM` | `BET365COM` | ❌ 不匹配 | 加 `BET365 COM` |
| `SURGE AU AMUSEDGROUP.C` | `SURGE AU AMUSEDGROUP C` | `AMUSEDGROUP` | ✅ 匹配 | 无需修复 |
| `Gareton BV Limassol CYP` | `GARETON BV LIMASSOL CYP` | `GARETON BV` | ✅ 匹配 | 可加更具体变体 |

**关键原则**：
- **keyword 必须是 clean_text 后的形式**（大写，仅 A-Z0-9，空格分隔）
- **不要凭空构造 keyword**——必须从 samples 中的实际文本提取
- **优先级**：从 samples 中提取 > 从 pattern_norm 中截取 > 猜测

##### 3.4.1.2 keyword_updates 写入规范

**商户名必须大小写精确匹配 KB**：
- 生成 `initial_keyword_updates.csv` 前，必须先 `search_merchant.py` 查询，获取 KB 中的精确 `merchant_name`
- **禁止**自己编造 merchant_name（如把 "Bet365" 写成 "BET365"）
- 写入脚本必须使用 **case-insensitive 匹配**来找商户

**推荐写入脚本模式**（每次运行复用）：

```python
# 核心逻辑：case-insensitive + 去重 + 追加
NAME_MAP = {}  # {update_csv中的名字: KB中的精确名字}
for _, row in updates.iterrows():
    search_name = NAME_MAP.get(row['merchant_name'], row['merchant_name'])
    mask = kb['merchant_name'].str.strip().str.lower() == search_name.lower()
    if mask.any():
        idx = kb[mask].index[0]
        existing_kw = kb.at[idx, 'keywords']
        for new_kw in row['new_keyword'].split(';'):
            kw = new_kw.strip()
            if kw and kw not in existing_kw.split('|'):
                existing_kw += '|' + kw
        kb.at[idx, 'keywords'] = existing_kw
```

**必须避开的坑**：
1. ❌ 用大写 merchant_name 去匹配 KB 的 CamelCase → 用 `.str.lower()` 做 case-insensitive
2. ❌ 写入前不做去重 → 会追加重复 keyword
3. ❌ 用 `pd.read_csv` 直接覆盖写回（可能破坏 BOM/编码）→ 用 `encoding='utf-8-sig'`
4. ❌ 不备份就直接改 → 必须先 `shutil.copy` 备份

```
##### 3.4.2 其他引擎：标准流程

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

---

#### 3.5 写入候选规则 CSV

写入 `reviews/<date>/<engine_id>_candidates.csv`。

**🚨 强制规则：永远用合并模式，禁止覆盖已有候选（否则会丢规则！）**

```
正确流程：
1. 如果 CSV 已存在 → 先读取已有条目
2. 新增条目追加到已有列表（去重：rule_name 相同则覆盖更新）
3. 合并后统一写入
4. 禁止直接 `w` 模式打开已有候选 CSV 写入新内容

错误示范（会导致规则丢失）：
  ❌ revise_candidates.py 用 `w` 模式覆盖了 initial_candidates.csv
  ❌ 第一次生成 51 条，第二次修改时覆盖只剩 19 条，丢失 50 条通用商户
```

格式要求：
- 列名与原始 CSV 完全一致（schema 见 CLAUDE.md）
- 额外增加列：`status`（默认 `☐ confirm`）、`hit_count`、`risk_level`、`illion_category`、`samples`、`target_file`（仅多文件引擎需要）
- 按 hit_count 降序排列

---

#### 3.6 AI 预筛（写在阶段三末尾）

**生成候选规则后，AI 立即逐条评估，预判每个规则的风险，为人工审核提供参考标签。**

**重要原则：AI 不能代替人工做最终决定。所有规则（包括低风险的）都必须进入阶段五弹窗，由人工逐条确认或拒绝。AI 的角色是提供「建议标签」降低人工判断负担。**

AI 建议标签：

| 标签 | 条件 | 含义 |
|------|------|------|
| 🟢 AI建议确认 | keyword 规则 + illion 标签一致 + 无冲突 + gain > 10 | 低风险，但人工仍需逐条点击 |
| 🔵 需仔细判断 | regex 规则、有冲突、无 illion 标签佐证、单关键词 ≤ 2 个词 | 人工独立判断 |
| 🔴 AI建议拒绝 | 引擎约束 ERROR、零增益+有冲突、pattern 太泛无法确定分类 | 默认拒绝，人工可改为确认 |

AI 在阶段三结束后打印预筛摘要：
```
📋 规则预筛结果
═══════════════════════════════════════
  🟢 AI建议确认: X 条（弹窗中绿色标签，仍需您逐条点击）
  🔵 需要您仔细判断: X 条
  🔴 AI建议拒绝: X 条（弹窗中红色标签，您可改为确认）
```

---

### 阶段四：验证

验证分三步：**语法验证** + **影响面分析** + **引擎级约束校验**。

#### 4.1 语法与 Schema 验证

```
python scripts/validate_candidates.py --review_dir reviews/<date>/
```

验证内容：正则语法、CSV 列匹配、已有规则重叠、引擎级约束校验（transfer 用小写、catch_all 用大写、all_other_credit 不用 regex、fee category 合法值等）。

每条约束标注严重程度：**ERROR**（规则不可用）/ **WARNING**（可能不工作）/ **INFO**（最佳实践建议）。

#### 4.2 基线影响分析

**候选规则生成前**先保存基线：
```
python scripts/baseline.py save --input input/<latest>.xlsx --output baseline/<date>/
```

**候选规则生成后**模拟影响面：
```
python scripts/baseline.py diff --candidates reviews/<date>/ --baseline baseline/<date>/ --input input/<latest>.xlsx
```

每条规则显示：**gain**（新分类多少条）、**conflicts**（覆盖多少条已分类交易）、`priority_conflict: true` 表示会真正覆盖已有分类。

#### 4.3 读取验证报告

综合 `validation_report.json` 和 `impact_report.json`，标记风险级别：
- `低`：语法 OK，无冲突，gain > 0
- `中 ⚠`：有少量冲突但优先级不覆盖，或有边界模糊
- `高 ✗`：有严重冲突或语法错误

---

### 阶段五：🔴 逐引擎 AskUserQuestion 审批

**阶段四验证通过后自动执行。按引擎逐个弹出 AskUserQuestion 让用户审批。**

#### 5.1 审批原则

- **🚨 弹窗前必须先打印规则详情（强制）**：在弹出 AskUserQuestion **之前**，必须先在对话中用文本逐条打印该引擎所有规则的完整详情（规则名、pattern、category、gain、conflict、风险等级、illion标签、样本等），让用户在对话记录中能清晰看到每条规则的具体内容。AskUserQuestion 弹窗只放**简短摘要和选项**，不要把大量规则详情塞进 question 字段。**违规即为流程错误。**
- **逐引擎审批**：每个引擎单独弹一个 AskUserQuestion
- **引擎顺序**：按规则数量从少到多排列（fee → transfer → all_other_credit → catch_all → initial）
- **使用 AskUserQuestion，不是 HTML**：审批交互依赖 Claude Code 原生弹窗

#### 5.2 弹窗格式

**先**在对话中完整打印规则详情，**然后**弹窗（只放简短摘要 + 选项）：

```
引擎: Fee Engine（2条，+23，0冲突）
┌─────────────────────────────────────────────┐
│ #1 international_txn_fee_upper             │
│    pattern: ^INTERNATIONAL\s+TRANSACTION\s+FEE
│    category: fee | gain: +12 | conflict: 0 │
│ #2 monthly_platinum_debit_fee              │
│    ...                                     │
├─────────────────────────────────────────────┤
│ 选项:                                      │
│   - 全部批准 (2条)                          │
│   - 仅批准 #1                              │
│   - 仅批准 #2                              │
│   - 全部跳过                               │
└─────────────────────────────────────────────┘
```

规则少的引擎（≤5条）可简要列出每条规则核心信息在 question 中；规则多的引擎只展示统计摘要。

#### 5.3 处理审批结果

- "全部批准" → 该引擎所有规则标记为 `confirmed`
- "排除有冲突的" → 有冲突的标记为 `review`，其余 `confirmed`
- "仅无冲突的" → 仅无冲突的标记为 `confirmed`
- "全部跳过" → 该引擎所有规则标记为 `rejected`
- "逐条审核" → 对该引擎进一步拆分，分批次 AskUserQuestion

审批完成后，将结果写回 `*_candidates.csv`（更新 status 列）。

#### 5.4 示例

```javascript
// 对话中先打印完整详情（略），然后弹窗：
AskUserQuestion({
  questions: [{
    question: "Fee Engine：2条规则，+23 gain，0冲突。详情见上方。是否批准？",
    header: "Fee 审批",
    options: [
      { label: "全部批准 (2条)", description: "两条都批准，继续下一个引擎" },
      { label: "仅批准 #1", description: "仅批准 international_txn_fee_upper" },
      { label: "仅批准 #2", description: "仅批准 monthly_platinum_debit_fee" },
      { label: "全部跳过", description: "两条都跳过" }
    ]
  }]
})
```

---

### 阶段六：test_rules.py 测试

阶段五确认后自动执行，产出 `test_report.json`。

---

### 阶段七：🔴 最终确认 AskUserQuestion

测试完成后，在对话中打印分析报告，然后弹出最终确认：

```
question: "test_rules.py 测试完成。以上是详细分析报告。\n\n"
  + "✅ 低风险（可直接写入）: X 条\n"
  + "⚠️ 中风险（需人工判断）: X 条\n"
  + "🔴 高风险（建议剔除）: X 条\n\n"
  + "是否确认写入这 X 条规则到 raw/？"
header: "最终确认"
options:
  - 确认写入 / 排除高风险 / 取消
```

---

### 阶段八：执行写入（自动执行）

**阶段七最终确认后自动触发。**

写入本地 `raw/` 目录：
```bash
python scripts/apply_rules.py --review_dir reviews/<date>/
```

同步到 finv_category_V2（可选）：
```bash
python scripts/apply_rules.py --review_dir reviews/<date>/ --sync_to <finv_path>
```

**注意**：initial 引擎的 keyword 补充（`initial_keyword_updates.csv`）需要单独处理——读取已有商户，在 keywords 列追加新变体，而非新增行。使用专用脚本：
```bash
# 预览
python scripts/apply_keyword_updates.py --review_dir reviews/<date>/ --dry-run
# 执行
python scripts/apply_keyword_updates.py --review_dir reviews/<date>/
```
`apply_rules.py` 只处理 `*_candidates.csv` 文件（新商户/新规则），不处理 keyword 补充。

#### 8.1 完成报告

```
✅ 规则入库完成
═══════════════════════════════════════
  写入规则: X 条
  涉及引擎: all_other_credit(N), initial(M), ...
  同步 finv: ✅/⏭️ 跳过
  下一步: 在 finv_category_V2 中重新跑流水线验证效果
```

---

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

## 脚本修改规范（硬性约束）

**🚨 修改 `scripts/` 目录下任何 .py 文件后，必须在提交前至少跑一次冒烟测试，确认脚本能正常启动不报错。**

```
修改脚本后的最小自检流程：
1. python scripts/<modified>.py --help     # 至少确保能解析到 main()
2. 如有条件，用最小输入跑一次完整流程
3. 确认无 ImportError / AttributeError / KeyError
```

**反面教材**：
- `label_compare.py` 从 2383 行重构到 461 行，`import common as base` 替换了自引用，但 `common.py` 缺少 `parse_args`、`build_config` 等函数 → 运行时报 `AttributeError`
- `search_merchant.py` 的 CSV 有 BOM 头导致 `﻿merchant_name` 列名 → 运行时报 `KeyError`
- 两个 bug 都是改完后没跑过冒烟测试就提交了

## illion 标签特别说明

- illion 是第三方数据富化服务，提供 `category` 和 `third_party`（商户识别）
- 其标签可作为**高置信度参考**，但不是 100% 准确的 ground truth
- 当 illion 标签与 finv_category 不一致时：
  - `illion 更具体、finv 更泛` → 考虑加规则细化
  - `illion 与 finv 粒度不同`（如 illion=Dining Out, finv=Entertainment）→ 取决于上下文
  - `illion 明显错误` → 忽略
- 当 illion 有标签而交易未被分类 → **优先生成规则**（这是最高价值的 gap）

## 各引擎规则格式参考

- **dishonour / all_other_credit**：`rule_type, pattern, required_terms`
- **fee**：`priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description`
- **catch_all**：`rule_name, category, pattern, match_type, confidence`
- **income**：`pattern_group, pattern, match_type, description`
- **liability**（多文件引擎，必须指定 `target_file`）：
  - `counterparty_keyword_rules.csv` → `keyword, counterparty, product_type, match_type`
  - `credit_card_rules.csv` → `priority, account_type, dr_cr, bank, match_type, keyword, min_prefix_len, counterparty, product_type, exclude_pattern`
  - `home_loan_car_loan_rules.csv` → `rule_id, target_field, rule_name, match_scope, match_type, pattern, account_type, dr_cr, bank, amount_gt, priority, enabled`
  - `debt_collection_rules.csv` → `counterparty, product_type, rule_id, match_type, keyword`
  - `debt_consolidation_rules.csv` → `keyword, counterparty, product_type, match_type`
  - `dishonours_rules.csv` → `rule_type, pattern, required_terms`
  - `overdrawn_rules.csv` → `counterparty, rule_id, match_type, pattern, note`
  - `bnpl_maximum_limits.csv` 是配置文件，不生成规则
- **transfer**（多文件引擎，必须指定 `target_file`）：
  - **只处理 Internal Transfer 和 External Transfers 两个 category**
  - `transfer_counterparty_rules.csv` → `keyword, counterparty, match_type`
  - `transfer_external_high_confidence_rules.csv` → `priority, rule_name, category, pattern, dr_cr, description`
  - `transfer_external_medium_confidence_rules.csv` → 同上 schema
  - `transfer_internal_regex_rules.csv` → `priority, rule_name, pattern, dr_cr, description`
  - 其余 exclusion/indicator/pairing 文件通常不自动生成
- **initial**：`merchant_name, keywords, category`
  - 候选 CSV 可以包含额外元数据列（link, category_source, status 等），apply_rules.py 会自动剥离 META_COLUMNS

## category 中文翻译参考

Dining Out=餐饮, Groceries=超市/杂货, Gambling=赌博, Entertainment=娱乐, Transport=交通, Health=医疗, Automotive=汽车, Retail=零售, Personal Care=个人护理, Gyms and other memberships=健身/会员, Education=教育, Travel=旅游, Fees=费用, Insurance=保险, Utilities=水电, Donations=捐款, Home Improvement=家装, Pet Care=宠物, Subscription TV=订阅电视, Rent=房租, Telecommunications=电信, All Other Credits=其他入账, Internal Transfer=内部转账, External Transfers=外部转账, Wages=工资, Centrelink=福利金, Non SACC Loans=非SACC贷款, SACC Loans=SACC贷款, Credit Card Repayments=信用卡还款, Debt Collection=债务催收, Debt Consolidation=债务整合, Dishonours=拒付, Overdrawn=透支, Unknown Loans=未知贷款, Information=信息, Department Stores=百货
