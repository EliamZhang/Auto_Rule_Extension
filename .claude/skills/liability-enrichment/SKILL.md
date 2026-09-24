---
name: liability-enrichment
description: 联网核实疑似放贷商，把商户补进 liability 引擎的商户文件（counterparty_keyword_rules.csv / home_loan_car_loan_rules.csv），产出 liability_candidates.csv 供 evidence.py 打分。用户说"补充放贷商"、"更新放贷商"、"liability 缺口"、"/liability-enrichment" 时触发。
---

# Liability Enrichment Skill

Step 2 of the `modules/liability_enrich` pipeline. Reads the gap list produced by
`gap_source.py`, web-verifies each suspected lender, and writes
`liability_candidates.csv` for `evidence.py` to score.

## 语言要求（强制）

- **所有对话输出必须使用中文**（简体中文）
- **代码、脚本名、字段名、命令行参数**保持英文原文
- **商户名、category 名、product_type** 保持英文原文

## 这个 skill 做什么

**联网搜索 → 核实商户 → 写进 liability 的商户文件**。两个主文件：

| 文件 | 里面装什么 | 匹配列 | 命中后的输出 |
|------|-----------|--------|-------------|
| `raw/liability_rule/counterparty_keyword_rules.csv` | 一般放贷商（349 条） | `keyword`（大写、`;` 分隔多变体） | `counterparty` = **商户名** |
| `raw/liability_rule/home_loan_car_loan_rules.csv` | 房贷/车贷放贷商（139 条） | `pattern`（**小写 regex**） | `is_home_loan`/`is_car_loan` 标志 → counterparty 为空时填 `Home Loan`/`Car Loan` |

> ⚠️ **两个文件的建模方式不同，别按同一个套路写**：counterparty 文件是「文本 → 商户名」，
> home_loan 文件是「文本 → 产品标志」，它**不写商户名**（商户名只放在 `rule_name` 列里，
> 而该列被 loader 忽略、仅供 `search_merchant.py` 读）。
>
> 第三个文件 `debt_collection_rules.csv` 也装商户（收债商），但属**契约外路由**，见下。

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
    "product_types": { "personal_loan": 224, "car_loan": 54, ... },
    "flag_rule_file": {                                                // 第 2 个文件
      "source_file": "raw/liability_rule/home_loan_car_loan_rules.csv",
      "row_count": 139,
      "merchants": ["Advantedge", "Toyota Finance", ...],              // 137 个，取自 rule_name
      "regex_patterns": [...],                                         // 128 条
      "keyword_patterns": [...],                                       // 10 条，已大写
      "target_fields": { "is_home_loan": 45, "is_car_loan": 94 },
      "match_scopes": { "text_or_counterparty": 128, "text": 10, "all": 1 }
    }
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

> 顶层那三组键（`counterparties` / `keywords` / `product_types`）描述的**只是
> `counterparty_keyword_rules.csv`**；第二个文件在 `existing_rules.flag_rule_file` 里，
> 商户名是 `merchants`（取自 `rule_name` 列 —— 那是该文件里唯一的商户名记录）。
> 判定「新商户还是已有商户的别名」**两个都要查**，只查第一个就会把 `Advantedge`、
> `Toyota Finance`、`Firstmac` 这些已存在的房贷/车贷商户当新商户重复添加。

## 处理优先级（重要）

实测产出率差异极大，**按这个顺序做，别平铺**：

| 顺序 | 类 | 做法 |
|------|----|------|
| 1 | `illion_liability_missed` | 全部处理。illion 说是负债类而 finv 没归到，是最强信号。⚠️ 该类由 `gap_source.py` **按主归属**（`owner_engine_id` 的第一个 engine 是 liability）筛出，**不要自行用包含匹配重算** —— liability 也产出 `Retail`/`Dishonours`/`Overdrawn`，子串匹配会把它们一起拉进来，实测让该类从 90 行虚增到 427 行 |
| 2 | `generic_loan_catchall` | 先看 `samples`，**只处理能看出商户名的**。大量是「向某个说不出名字的放贷商还款」，直接跳过 |
| 3 | `unclassified_loan_signal` 且 `signal_strength=strong` | 处理 |
| 4 | `unclassified_loan_signal` 且 `signal_strength=weak` | **默认跳过**。实测 134 组里 133 组是 ATM 取现/现金存入/利息 |

## 执行流程

### 0. 参数确认 —— 🔴 AskUserQuestion

**不要自己拍默认值直接开跑，也不要每次都用默认值静默开跑。** 用 `AskUserQuestion`
确认本次范围，再进第 1 步：

| 参数 | 默认 | 说明 |
|------|------|------|
| `gap_class` | 全部 | 只处理某一类（多选：`illion_liability_missed` / `generic_loan_catchall` / `unclassified_loan_signal`(strong)） |
| `batch_size` | 15 | 本次核实多少个缺口 |
| `max_batches` | 1 | 最多几批（0 = 不限） |
| `review_dir` | `reviews/` 里最新的 | 指定 `reviews/<date>/`；若无 `liability_gaps.json` 先跑前置条件里的 `gap_source.py` |

### 1. 读取缺口

读 `liability_gaps.json`，按上面的优先级排序，取前 `batch_size` 条。
**打印将要核实的清单**（编号 + pattern_norm + count + 类别）让用户看到。

### 2. 逐个联网核实

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

⚠️ **`.gov.au` 在本机被 WebFetch 硬拦、curl 也超时**，AFCA/ASIC 的页面拿不到时，
退到机构官网的 PDF（信贷牌照、FAQ、产品页）在证据强度上等价，写进 `evidence_source` 即可。

**c. 查已有商户 —— 两个文件都要查**（判定「新商户」还是「已有商户的别名」）：

```bash
# 一般放贷商
python scripts/search_merchant.py \
    --merchant-file raw/liability_rule/counterparty_keyword_rules.csv \
    --search "<关键名称>" --fuzzy

# 房贷/车贷放贷商
python scripts/search_merchant.py \
    --merchant-file raw/liability_rule/home_loan_car_loan_rules.csv \
    --search "<关键名称>" --fuzzy
```

两个文件的列名不同（`counterparty`/`keyword` vs `rule_name`/`pattern`），
`search_merchant.py` 按**角色**自动识别，不需要 `--field`。**必须加 `--fuzzy`**，
否则只做整串相等匹配。

**但多数时候不用跑脚本** —— `existing_rules` 已经把两边都列出来了，直接比对即可：

| 查什么 | 用什么 |
|--------|--------|
| 商户是不是已有 | `counterparties`（281 个）+ `flag_rule_file.merchants`（137 个） |
| 这个变体是不是已被覆盖 | `keywords`（574 条，counterparty 侧）+ `flag_rule_file.regex_patterns` / `keyword_patterns` |

脚本留给「名字只是部分相似、要看清整行怎么写」的情形。
⚠️ `merchants` 是 137 个而第二个文件有 139 行 —— `Mortgage`、`Police Bank Home Loan`
各出现两次，按名字检索会命中两条。

**d. 三选一：**

| 判定 | 动作 |
|------|------|
| 真·新放贷商 | 生成新候选。`counterparty`/`rule_name` 用官方商号（不是交易文本里的截断写法） |
| 已有放贷商的别名/截断写法 | **不新增商户**，在对应文件里给那个已有商户补 keyword/pattern |
| 根本不是放贷商 | 丢弃，不写入候选 |

### 3. 路由到目标文件

按**商户的主营产品**决定写哪个文件 —— 不是按交易文本里出现了什么词：

| 情况 | `target_file` | 行形状 |
|------|--------------|--------|
| 房贷 / 车贷放贷商 | **两个文件各写一行**（见下） | counterparty 行 + flag 行 |
| 其余放贷商（`personal_loan` / `bnpl` / `wage_advance` / 说不清的 `generic_loan`） | 只写 `counterparty_keyword_rules.csv` | 见下方 `product_type` 7 值 |
| 收债商（illion 标 `Debt Collection`） | `debt_collection_rules.csv` ⚠️ **契约外**，先问用户 | — |

#### 房贷 / 车贷商户：两个文件各写一行（这是库里的既有约定）

实测：`counterparty_keyword_rules.csv` 里 `product_type ∈ {home_loan, car_loan}` 的
**69 条**中，**47 条**在 `home_loan_car_loan_rules.csv` 里有同名行
（Toyota Finance、Advantedge、New Start Auto Loans、Allied Retail Finance …）。
两行分工不同，**不是二选一**：

| 行 | 作用 | 谁在消费它 |
|----|------|-----------|
| `counterparty_keyword_rules.csv` 行 | 给出**具体商户名**（`counterparty` 列） | 下游按 counterparty 归组 |
| `home_loan_car_loan_rules.csv` 行 | 置 `is_home_loan` / `is_car_loan` **标志** | `_TARGET_METADATA_MAP` 据它写 `product_type`；`streams.py` 据它分组（home_loan 优先级 25 / car_loan 27） |

执行顺序保证两行不打架：counterparty 规则先跑（Step 1）写名字，flag 规则后跑（Step 2）时
`_TARGET_METADATA_MAP` 只在 counterparty **为空**时才写 `Home Loan`/`Car Loan`
（`counterparty.py:664` 的 `cp_empty`），但 `product_type` 是**无条件**覆写的（`:668`）。
结果 = 具体商户名 + 正确的 product_type，**两行都要**。

**例**：`AFSH NOMINEES`（Advantedge 的持牌主体，`HL012` 已有 `advantedge`）→ 写两行：

```
① → counterparty_keyword_rules.csv
   keyword=AFSH NOMINEES   counterparty=Advantedge   product_type=home_loan   match_type=keyword
   ⚠️ counterparty 用库里已有的 "Advantedge"，不要新起 "AFSH Nominees"

② → home_loan_car_loan_rules.csv
   rule_id=HL046   target_field=is_home_loan   rule_name=AFSH Nominees
   match_scope=text_or_counterparty   match_type=regex   pattern=afsh\s*nominees
   account_type=*   dr_cr=*   bank=*   amount_gt=(空)   priority=100   enabled=1
```

⚠️ **别名必须挂到原商户名下**，别为同一家放贷商造第二个 `counterparty`：否则下游按
counterparty 分组时它会裂成两组。`rule_name` 列填别名（loader 忽略该列，但
`search_merchant.py` 按它检索，填别名才能被搜到）。

**`product_type` 只能是这 7 个值**（现有规则的实际分布）：

| product_type | 现有条数 | 用于 |
|--------------|---------|------|
| `personal_loan` | 224 | 个人贷款（默认） |
| `car_loan` | 54 | 车贷 |
| `home_loan` | 15 | 房贷 |
| `bnpl` | 38 | 先买后付 |
| `wage_advance` | 16 | 工资预支 |
| `generic_loan` | 1 | 说不清品类、但确定是贷款（如 `Generic Loans` 这种描述性对照方） |
| `bank` | 1 | 银行自身产品 |

### 4. 写出候选 CSV

写到 `reviews/<date>/liability_candidates.csv`，UTF-8 with BOM。

**列 = 两个目标文件列的并集**（每行只填自己那个文件用得到的列，其余留空）：

```
target_file,keyword,counterparty,product_type,match_type,status,hit_count,risk_level,
illion_category,samples,evidence_source,
rule_id,target_field,rule_name,match_scope,pattern,account_type,dr_cr,bank,amount_gt,priority,enabled
```

前 11 列是原有约定，后 11 列是 `home_loan_car_loan_rules.csv` 的 Format A 列。
`apply_rules.py:207` 只保留目标文件已有的列、其余丢弃，所以并集是安全的 ——
但**列名必须完全正确**：`home_loan_car_loan_rules.csv` 没有 `keyword` 列，
把 regex 写进 `keyword` 会在写入时被丢掉，落库后成一条空 `pattern` 的死规则。

**`counterparty_keyword_rules.csv` 行**（`keyword` 列）：

- 用**分号**分隔多变体（引擎按 `;` 切分，**不是 `|`**）
- 写**大写**（引擎在转大写的文本上匹配）
- 变体来自 `samples` 里实际出现的写法，不要凭空造
- **绝不能以数字开头** —— 见下方硬约束

**`home_loan_car_loan_rules.csv` 行**（`pattern` 列）：

- `rule_id`：下一个未占用编号（现 `HL001–HL045`、`CL001–CL094` 无空号 → 从 `HL046` / `CL095` 起）
- `rule_name`：**商户名**（loader 忽略，但 `search_merchant.py` 用它做名字列）
- `match_scope`：`text_or_counterparty`（商户名，128 条先例）｜`text`（通用词，10 条先例）
- `match_type`：`regex`（商户名）｜`keyword`（通用词）
- `pattern`：**小写 regex**，空格写 `\s*`（`toyota\s*finance`）；连写商号直接连写（`carfinanceaustralia`）
- `account_type`/`dr_cr`/`bank`：`*`；`amount_gt`：空；`priority`：`100`；`enabled`：`1`

例（AFSH Nominees —— 房贷商户走两行，见步骤 3）：

```
# ① counterparty 行（填 keyword / counterparty / product_type）
target_file=counterparty_keyword_rules.csv  keyword=AFSH NOMINEES
counterparty=Advantedge  product_type=home_loan  match_type=keyword

# ② flag 行（填 rule_id … enabled 那段 Format A 列）
target_file=home_loan_car_loan_rules.csv  rule_id=HL046  target_field=is_home_loan
rule_name=AFSH Nominees  match_scope=text_or_counterparty  match_type=regex
pattern=afsh\s*nominees  account_type=*  dr_cr=*  bank=*  amount_gt=（空）
priority=100  enabled=1
```

同一行 CSV 里，① 用不到的那 11 个 Format A 列留空，② 用不到的 `keyword`/`counterparty`
等列留空 —— `apply_rules.py` 按 `target_file` 各取所需。

其余约定（两个文件通用）：

- `match_type` 列恒为对应列的类型（上个例子里是 `regex`；counterparty 文件恒为 `keyword`）
- `status` 恒为 `☐ confirm`（`evidence.py` 会按验证结果改写）
- `hit_count` / `risk_level` / `samples` 留空，由 `evidence.py` 回填
- `illion_category` 取该缺口 `illion_categories` 的第一个值（可能为空）
- `evidence_source` 记联网来源 URL，供人工审批复核 —— **新商户必填**（步骤 6 会卡这条），
  已有商户的别名行也建议填

### 5. 交棒给 `evidence.py`

```bash
python modules/liability_enrich/evidence.py \
    --candidates reviews/<date>/liability_candidates.csv \
    --input input/<同一份报告>.xlsx
```

**不用指定目标文件** —— `evidence.py` 按每行的 `target_file` 自动选匹配语义
（counterparty 侧是压缩空格+大写+字母边界，Format A 侧是原始 text+IGNORECASE）。
契约外的文件名（如 `debt_collection_rules.csv`）会**直接报错**而不是猜一套语义 ——
这正是上面「契约外路由」要求先问用户的原因。

然后**读回 `liability_evidence.json`**，向用户汇报：

- 顶层 `files`：两个文件各多少条；`new_merchant_count`：确认是**新商户**的有几条
- `new_merchants_without_source`：其中几条**没有联网来源** —— 有的话先回步骤 2b 补搜，别送审
- 有多少条 `零增益`（联网假设未获数据支持，建议丢弃）
- 有多少条 `死规则`（规则本身非法）—— ⚠️ **不要一律丢弃，先看 `problems`**，它写明了原因和改法：
  - **counterparty 侧**：含 `|` 只是分隔符写错（上一步要求用 `;`），改成 `;` 重跑往往就是有效候选；
    数字开头 / 空 keyword 才是真废。实测：历史候选
    `PRINCIPLE BALANCE ADJUSTMENT|PRINCIPAL BALANCE ADJUSTMENT` 被判死规则，
    改成 `;` 后 hit_count 7、`☐ confirm`、零抢占 —— 差点被当成「零增益」扔掉
  - **Format A 侧**：`match_scope` 拼错、`enabled=0`、regex 编译不过、`target_field` 写错、
    `match_scope=all` 配 `match_type=keyword`（pattern 被整段忽略，等于通配）
  - 死规则的 `hit_count` 一律记 **0** —— 规则在 production 里就是不命中，
    报一个按错误语义算出来的数字只会误导审批
- 有多少条会 `hit_other`（从别的引擎抢行），抢的是哪些引擎、多少行
- 真正有 `hit_unclassified` 增益的是哪几条

**怎么读房贷/车贷（flag）行的三个命中数：**

| 情况 | hit 分布 | 说明 |
|------|---------|------|
| 规则有效、确实是新车贷商户 | `hit_liability` ≈ `hit_count` | 那些行本来就 `product_type=car_loan`（可能已由别的 pattern 命中），flag 行是在**加固**，`gain=0` 属正常 |
| 规则有效、原来靠 `Generic Loans` 兜底 | `hit_liability` 高（finv_category 已是 `Non SACC Loans`）、`hit_other` 低 | 典型场景：`AFSH NOMINEES` 实测 5 命中里 4 条是 `Generic Loans` |
| 规则写得比已有规则宽 | `hit_other` 高 | 会从别的引擎抢行，按风险判 |

⚠️ **`gain = 0` 不等于没用**：flag 行的价值是把 `product_type` 从 `generic_loan`/空
纠正成 `home_loan`/`car_loan`（`streams.py` 的分组优先级 25/27），
这属于**同引擎内的产品修正**，`baseline.py` 不报（同引擎优先级相同 → `priority_conflict=False`）。

### 6. 🔴 逐条审批 —— AskUserQuestion

**弹窗前必须先打印候选详情（强制）**：按 `target_file` 分两组，逐条套下面的模板。
让用户在对话记录里能看清每条；AskUserQuestion 里只放**按 `target_file` / 风险分组的摘要和选项**，
不要把明细塞进 `question` 字段。**违规即为流程错误。**

数据全部取自 `liability_evidence.json` 的 `candidates[]`，不用再翻 CSV。
**标题行放商户名**（= `label`：counterparty 文件是 `counterparty`，flag 文件是 `rule_name`）。

**模板 A —— `counterparty_keyword_rules.csv` 行**

```
[R1] Liberty Financial                                   ☐ confirm
  商户   已有商户  product_type=personal_loan
  规则   keyword  变体 1 个   SECURE FUNDING
  命中   25 = 未分类 0 + 负债 13 + 会抢走 12     风险 中
  抢占   transfer × 12
  样本   SECURE FUNDING CommBank app BPAY 64956 20915757 | PAYMENT TO SECURE FUNDING P 4357068
  来源   https://www.afca.org.au/...
```

**模板 B —— `home_loan_car_loan_rules.csv` 行（Format A）**

```
[R4] AFSH Nominees                                       ☐ confirm
  商户   ⚠️ 新商户  target_field=is_home_loan
  规则   HL046  text_or_counterparty + regex  afsh\s*nominees
  命中   5 = 未分类 0 + 负债 4 + 会抢走 1       风险 中
  抢占   transfer × 1
  样本   AFSH Nominees CommBank app BPAY 11544 607610185 | ...
  来源   https://www.advantedge.com.au/...
```

**打印规则：**

1. 编号 `[Rn]` **全局连续**（不按组重新从 1 开始），与 AskUserQuestion 里引用的编号一致
2. 按 `target_file` 分两组，组标题写文件名 + 条数（如 `## home_loan_car_loan_rules.csv — 3 条`）
3. **A 的 `规则` 行必须写出 keyword 原文** —— 审批人要看的就是这串字本身。
   变体超过 3 个时写前 3 个 + `…（共 N 个）`
4. `抢占` 行取自 `conflict_engines`（为空写 `无`）；`样本` 最多 3 条、` | ` 分隔
5. 只打印 `status == ☐ confirm` 的行 —— `✗ 死规则` / `✗ 零增益` 按步骤 5 处理干净
   （改格式重跑 → 变 `confirm`，或丢弃），**不带进审批**
6. ⚠️ **flag 行的 `hit_liability` 含义与 counterparty 行不同**（见步骤 5 的表），
   打印时照抄数字，别自行写「加固 N 条」这种对 flag 行会误导的解读
7. ⚠️ 房贷/车贷走两行时，**两行的「新商户」判定各查各的文件，可能一行标新、一行标已有**
   （`counterparty=Advantedge` 已在库里，`rule_name=AFSH Nominees` 是新别名）——
   这是正常的，别当成矛盾去「修正」

⚠️ **来源是必打项，新商户尤其不能省。** 联网挖出来的商户，人工审核时唯一能核的就是
「你凭什么说这家是放贷商」—— 把 `evidence_source` 原样带出来（AFCA / ASIC / 机构官网 PDF 的 URL），
**必须是具体 URL**，「已核实」「查过 AFCA」这种没有链接的概括不算。
**新商户（`is_new_merchant=true`）没有来源就不打印、不送审**，退回步骤 2b 补搜；
已有商户的别名行也建议带，因为「这个截断写法 = 那家商户」同样是联网判断。

`evidence.py` 跑完会把这批漏网的列出来：顶层 `new_merchants_without_source`（商户名数组）
+ 日志里一条 `⚠️` warning。看到它就别往下走。

分组建议（按目标文件分，因为落库后归属不同）：

```
question: "liability 引擎：N 条候选（X 条 → counterparty_keyword_rules.csv，
           Y 条 → home_loan_car_loan_rules.csv），净增益 …，会改写 …。批准哪些？"
options:
  - 全部确认
  - 仅确认净增益、零冲突的
  - 按 target_file 分组确认（counterparty / home_loan 分开）
  - 逐条审核（拆成多个弹窗）
```

**审批结果写回**：把选中的行 `status` 改成 `confirmed`，其余改成 `rejected`。

⚠️ **不要跳过第 5 步直接改 `status`** —— 没有 `liability_evidence.json`
就没有命中数和抢占数，人工审批等于盲审。

### 7. 交棒下游

```bash
python scripts/validate_candidates.py --review_dir reviews/<date>/
python scripts/baseline.py diff --candidates reviews/<date>/ --baseline baseline/<date>/ --input input/<latest>.xlsx
# 🔴 人工审批
# 导出 confirmed_rules.json → test_rules.py → 🔴 最终确认 → apply_rules.py
```

⚠️ **`gain` 只数「unclassified → 有分类」。** 本 skill 的候选大量是
**把误标成别的类别的行改回来**（从 transfer 抢），这类候选 `gain = 0` 但会显示
`conflicts = N` —— `conflicts` 来自 transfer（priority 1，最先执行、本来就最容易被
后续引擎覆盖）时是**正常路径**，不是误伤。判读见 `modules/liability_enrich/README.md`
的「hit_other 会夸大风险」一节。

## 契约外路由

`debt_collection_rules.csv`（列 `counterparty,product_type,rule_id,match_type,keyword`）
不在本 skill 的原始契约里，但它是 liability 引擎第二个装商户的文件。两条已知约束：

1. 它的匹配边界是 `\b`（`_load_flag_rules_format_b`），**不是** counterparty 文件的
   `(?<![A-Za-z])` —— 数字**不**穿透。已有 3 条 Charter Mercantile 规则
   （`charter merc` / `chartermerc` / `chartermercantil`）全部匹配不上带空格的全文
   `CHARTER MERCANTILE`，正是因为 `charter merc` 后面跟着字母 `a`。
2. 候选行缺 `rule_id` 列时 `validate_candidates.py` 只报
   「列差异（apply_rules 会自行对齐）」**警告**，`apply_rules.py` 会把 `rule_id` 填空。

**要用这个文件前先问用户**，别默认它是允许的目标。

## 硬约束（违反即产生死规则或误伤）

1. **只认自己目标文件的匹配语义。** `counterparty_keyword_rules.csv` 的 `keyword` 是
   大写文本 + `(?<![A-Za-z])` 边界；`home_loan_car_loan_rules.csv` 的 `pattern` 是
   **小写 regex** + 只加 `IGNORECASE`、**不做空白压缩**（Format A 匹配的是原始 `text` 列）。
   两个文件的关键词格式**不能互换**。

2. **绝不能生成以数字开头的 counterparty keyword。** 匹配边界是
   `(?<![A-Za-z])keyword(?![A-Za-z])`，**不是 `\b`** —— 数字可以穿透。
   `360 CASH LOANS` 会误伤 `1360 CASH LOANS`。

3. **`counterparty` / `rule_name` 不能凭空造。** 要么是联网核实出的官方商号，
   要么是 `existing_rules.counterparties`（或第二个文件的 `rule_name`）里已有的名字。

4. **`samples` 为空且 `count < 3` 的缺口要降低优先级。** 单次出现的交易文本
   聚类噪声很大，容易把一次性转账当放贷商。

5. **不要把 `|` 当分隔符写进 counterparty 的 `keyword`。** 那是 merchant_kb /
   rent / gambling institution 的分隔符；liability 只按 `;` 拆。反过来，
   home_loan 文件的 `pattern` 本来就是 regex，`|` 在那里是**合法**的（见 `CL039`）。

### Format A 的 loader 陷阱（只适用于 `home_loan_car_loan_rules.csv`）

`counterparty.py` 的 `_load_flag_rules` 会**静默丢弃**加载不进来的行 ——
csv 写得好好的、`search_merchant.py` 也搜得到，跑起来就是不命中。以下几种都会中招：

6. **`match_scope` 只能是 `text` / `text_or_counterparty` / `all`。** 写成别的值
   loader 拼不出 `bucket_key`，整行**静默丢弃**（`counterparty.py:383`）。

7. ⚠️ **`match_scope=all` 配 `match_type=keyword` 是个陷阱。** loader 把文本存进
   `rule["keywords"]`（`:373`），而 `_apply_flag_rules` 的 `all_rules` 分支只找
   `rule["pattern"]`（`:607`）—— **文本条件被整段忽略**，规则变成「命中所有满足
   account_type/dr_cr/bank/amount_gt 的行」。要限定文本就改 `match_type=regex`
   或换 `text` / `text_or_counterparty`。

8. **`match_type` 只能是 `keyword` / `regex` / `always`**，且 `always` 只在
   `match_scope=all` 且**无 `pattern`** 时才成立（如 `HL009`）。带 pattern 的
   `always` 会被当 regex 编译；`all` + `always` + 无任何条件则等于给全库打标。

9. **`target_field` 只能是 `is_home_loan` / `is_car_loan`；`enabled=0` 的行不加载。**
   拼错 `target_field` 同样走不到想要的分支。

10. **Format A 匹配的是原始 `text` 列**（不压缩空白、不转大写），所以 `pattern`
    里用小写 + `\s*` 容忍空格（`toyota\s*finance`）；**写死一个空格会在双空格文本上失配**。

11. **Format A 的 keyword 是 `\b` 边界，数字开头匹配不到** ——
    与 counterparty 文件**方向相反**（那边数字会穿透、数字开头才是危险）。
    别把第 2 条的结论搬过来。

以上任一条命中，`evidence.py` 都会判 `✗ 死规则` 并在 `problems` 里写明原因 ——
但那是**事后兜底**，写候选时就该避开。

## 完整链路

```
gap_source.py            → liability_gaps.json      数据侧找缺口
  ↓
liability-enrichment     → liability_candidates.csv  联网核实（本 skill）
  ↓                        ↑ 步骤 6 🔴 AskUserQuestion 审批后才改 status
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
apply_rules.py           → 写入 raw/liability_rule/<target_file>
```
