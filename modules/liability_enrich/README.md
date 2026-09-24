# liability_enrich — Liability 放贷商规则联网发现

从分类报告里找出 liability 引擎没认出来的放贷商，联网核实后生成候选规则，
再交给仓库原有的审批链路写库。

**本模块只产出候选，不写规则。** 规则落库仍然只能走 `apply_rules.py`，
并且必须经过人工审批（见仓库根 CLAUDE.md 的「重要约定」）。

## 四步流水线

```
Step 1  gap_source.py        数据侧找缺口        → reviews/<YYYY-MM-DD_HHMM>/liability_gaps.json
Step 2  liability-enrichment 联网侧核实（Skill）  → reviews/<YYYY-MM-DD_HHMM>/liability_candidates.csv
Step 3  evidence.py          数据侧验证（闸门）                → 就地回填候选 CSV + liability_evidence.json
Step 4  原有链路：validate_candidates → baseline diff → 🔴 人工审批 → test_rules → 🔴 确认 → apply_rules
```

Step 2 没有脚本 —— 判断「这个商户到底是不是放贷商」需要联网检索和常识，
由 `.claude/skills/liability-enrichment/SKILL.md` 承担。

## 用法

```bash
# Step 1：从分类报告找缺口
python modules/liability_enrich/gap_source.py \
    --input input/202609091024.xlsx \
    --output reviews/2026-09-09_1025/

# Step 2：跑 /liability-enrichment skill 联网核实，写出 liability_candidates.csv

# Step 3：用真实数据验证候选（就地回填 hit_count/risk_level/samples/status）
python modules/liability_enrich/evidence.py \
    --candidates reviews/2026-09-09_1025/liability_candidates.csv \
    --input input/202609091024.xlsx

# Step 4：走原有链路
python scripts/validate_candidates.py --review_dir reviews/2026-09-09_1025/
```

产物目录用 `reviews/<YYYY-MM-DD_HHMM>/`（与 `reviews/` 下的实际目录一致），
时间戳对应本次输入报告 —— 上面的例子即 `input/202609091024.xlsx` → `reviews/2026-09-09_1025/`。

## Step 1：三类缺口

`gap_source.py` 抽三类「疑似放贷商」，一条交易只归入一个类（优先级 A > B > C）：

| 类 | 条件 | 实测产出率（121,845 行样本） |
|----|------|------------------------------|
| `generic_loan_catchall` | `finv_category="Non SACC Loans"` 且 `counterparty="Generic Loans"` | 低。236 行里大量是「向某个说不出名字的放贷商还款」，提炼不出商户 |
| `unclassified_loan_signal` | 未分类 且文本命中放贷措辞 | **极低**。134 组模式里只有 1 组命中强信号，其余几乎全是 ATM 取现、现金存入、利息 |
| `illion_liability_missed` | illion 标签是负债类但 finv 没归到负债类 | **最高**。真实放贷商 `SECURE FUNDING`、`CHARTER MERCANTILE`、`AUTO LEASING CAR LOANS` 都在这里 |

**「负债类」的定义是主归属**（`owner_engine_id` 的第一个 engine），不是包含 liability。
`Retail`（`initial,liability,catch_all`，来自 Cash Converters Retail 修正）、
`Dishonours`、`Overdrawn` 都含 liability 但不属于放贷商发现的范围 ——
用子串匹配会把 `Retail` 拉进来，实测让这一类从 90 行虚增到 427 行。

`unclassified_loan_signal` 类里每条带 `signal_strength` 字段：
`strong` = 命中了 LOAN/LENDER/LENDING/LEND/BORROW/PAYDAY/PAWN/BNPL，
`weak` = 只命中 CASH/CREDIT/FINANCE。弱信号排最后但不丢弃（宁可漏判不要误判）。

## Step 3：证据闸门

`evidence.py` 用**引擎一模一样的匹配**在真实交易上重放每条候选。
目标文件由候选的 `target_file` 决定，两个文件两套语义（`_shared.resolve_rule_file`
是唯一的判据，遇到契约外的文件名直接报错而不是猜）：

| target_file | pattern 列 | 文本侧 | 匹配 |
|-------------|-----------|--------|------|
| `counterparty_keyword_rules.csv` | `keyword`（`;` 分隔的字面量） | 压缩空格 + 大写 | `(?<![A-Za-z])` + `re.escape(kw)` + `(?![A-Za-z])` |
| `home_loan_car_loan_rules.csv` | `pattern`（**regex**，`match_type=regex`） | **原始 `text` 列**，不压缩不转大写 | `re.search(pattern, text, IGNORECASE)`；`text_or_counterparty` 时 counterparty 列也匹配一遍 |

⚠️ 两边都不是 `\b` 也不是「宽松 `str.contains`」：counterparty 侧数字可穿透
（`360 CASH LOANS` 也会命中 `1360 CASH LOANS`），Format A 侧数字**不**穿透。
这是 `_shared.py` 存在的理由 —— 用错一套语义会报出线上根本不会发生的命中，
而候选表看起来完全正常。

把 A 文件的候选按 B 文件的语义打分（或反过来）在两边都是静默的：
`evidence.py` 看着通过，production 里永远不命中。所以 `_load_candidates` 会在读取时
就校验 `target_file`，未知文件名直接 `ValueError`。

每条候选得到：

| 字段 | 含义 |
|------|------|
| `hit_count` | 匹配到的行数（**有 problems 的候选一律记 0** —— 规则在 production 里就是不命中） |
| `hit_unclassified` | 其中尚未被任何引擎认领的 —— **真正的增益** |
| `hit_liability` | 其中已经是负债类的 —— 只是加固 |
| `hit_other` | 其中**当前属于其他引擎**的 —— liability 优先级 300，这些行会被抢走 |
| `risk_level` | 由被抢比例决定：`高`（≥50%）、`中`（有抢）、`低`（无抢）、`零增益`（零命中）、`死规则`（规则本身非法） |

`hit_other` 是这个闸门存在的意义：liability 排在 transfer(1)/initial(10)/dishonour(150)/
gambling(180)/income(200) **之后**、all_other_credit(400)/fee(500)/rent(800)/catch_all(999) **之前**。
orchestrator 里后执行的引擎**按行覆盖**前面的（`orchestrator.py:92-96`），
所以这批 `hit_other` 的大头恰恰是 transfer/initial/dishonour/gambling 认领的行 ——
liability 会把它们整行抢走，这正是风险所在；排在它后面的 fee 等引擎则可能把行再抢回去。

⚠️ **已知的口径偏差：`hit_other` / `risk_level` 在这些情形下会高估风险。**
`evidence.py` 只按 `finv_category` 是否属负债类分桶（`evidence.py:287`
`m_other = mask & ~m_unclassified & ~m_liability`），**没有复刻 orchestrator 的候选集排除**，
以下命中会被计进 `hit_other` 却根本抢不走（或会被抢回）：

- finv_category 是 `Wages` / `Centrelink` 的行 —— liability 的 candidates 直接排除它们（`orchestrator.py:98-102`）
- **gambling(180) 已认领**的行 —— liability 在其上的预测在提交前被丢弃，赌博认领是终局（`orchestrator.py:141-147`）
- 之后会被 fee(500) 抢回去的行 —— fee 在 liability 之后**不带排除**重跑，非 `unclassified_only` 的费用规则会重新认领

所以 `risk_level=高` 只说明「按 `finv_category` 看这批命中大多不在负债类」，
不等于「这些行真的会被抢走」；审批时要结合 `liability_evidence.json` 的 `conflict_engines` 明细判断。

### status 的取值

| 值 | 含义 |
|----|------|
| `☐ confirm` | 值得人工看一眼 |
| `✗ 零增益` | 零命中，联网假设未获数据支持，建议直接丢弃 |
| `✗ 死规则` | 规则本身非法，永远不会按预期生效。counterparty 侧是空 keyword / 数字开头 / 含 `\|`；Format A 侧还有一类更隐蔽的：`match_scope` 拼错、`enabled=0`、regex 编译不过、`target_field` 写错、`match_scope=all` 配 `match_type=keyword`（pattern 被整段忽略，等价于通配） |

⚠️ **判 `死规则` 先看 `liability_evidence.json` 里那条的 `problems`**，别一律丢弃 ——
它写明了原因和改法，多数是格式问题而非假设错误（实测：`A|B` 改成 `A; B` 后就是一条有效候选）。

⚠️ **`apply_rules.py` 只写入 `status == "confirmed"` 的行**（`apply_rules.py:137`），
上面三个值都不是 `confirmed`，所以这一步不会意外落库 —— 必须人工改写状态。

## 候选 CSV 契约

列 = **两个目标文件列的并集**，每行只填自己那个文件用得到的列，尾部是元数据列：

```
target_file,keyword,counterparty,product_type,match_type,status,hit_count,risk_level,
illion_category,samples,evidence_source,
rule_id,target_field,rule_name,match_scope,pattern,account_type,dr_cr,bank,amount_gt,priority,enabled
```

前 11 列是 counterparty 文件的列 + 元数据，后 11 列是 `home_loan_car_loan_rules.csv`
的 Format A 列。`apply_rules.py:206-215` 按 `target_file` 各取所需（`matching_cols`），
所以并集是安全的 —— 但**列名必须完全正确**：`home_loan_car_loan_rules.csv`
没有 `keyword` 列，把 regex 写进 `keyword` 会在写入时被丢掉，落库后成一条
空 `pattern` 的死规则（`enabled=1` 也救不回来，loader 在 `:357` 就跳过它）。

- `target_file` 必须是 `counterparty_keyword_rules.csv` 或 `home_loan_car_loan_rules.csv`
  （liability 有 7 个规则文件，不写会被 `apply_rules._detect_target_file` 落到第一个文件上；
  契约外的文件名 `evidence.py` 直接报错）
- `keyword` 用**分号**分隔多变体（与引擎的 `split_upper_terms` 一致，不是 `|`）；
  Format A 的 `pattern` 本来就是 regex，`|` 在那里合法（见 `CL039`）
- ⚠️ **多变体候选在 `baseline.py` / `test_rules.py` 里会报 0 命中**：这两处把整格
  pattern 直接交给 `str.contains(..., regex=False)`（`baseline.py:246-247`、`test_rules.py:182-183`），
  **不切分号** —— `A; B` 被当成一个字面量，永远不可能命中。能正确切 `;` 的只有
  `_shared.split_variants`（`evidence.py` 用）与 `validate_candidates.py:492`。
  后果：多变体候选在 `impact_report.json` 里 gain/conflict 都是 0，看起来「无影响」，
  可能被误当无收益丢弃、或掩盖真实冲突 —— 判读影响面时要手工拆开变体核对。
- `product_type` 只能填 skill 约定的 7 个值（`personal_loan`/`car_loan`/`home_loan`/
  `bnpl`/`wage_advance`/`generic_loan`/`bank`），**没有任何一层会校验它**：
  `evidence.py` 原样记录（`evidence.py:335`），`validate_candidates.py` 的
  `_validate_value_constraints`（`:93-114`）只查 `match_type`/`rule_type`/`confidence`。
  拼写变体会一路进 `raw/`，而 finv 的 `streams.py:1878-1888`（`PRODUCT_RULES`）
  只按固定值分发 stream —— 引擎照样给出 counterparty，但拿不到 stream_id，
  `add_finv_category` 因此也给不出 `finv_category`：**看着生效、分类落空**。
- `evidence_source` 记联网来源 URL 供审批复核。它已加入 `scripts/common.py` 的
  `META_COLUMNS`，写入前会被剥离，不会污染 `raw/`

`evidence.py` 的额外诊断（`hit_other` 明细、冲突引擎、已被谁认领、`is_new_merchant`）写在
同目录的 `liability_evidence.json` 里，**不放 CSV** —— 任何不在 `META_COLUMNS` 里的列
都会被 `apply_rules` 当规则数据写进 `raw/`。审批要用的联网来源 `evidence_source`
也一并带进 JSON（它在 CSV 里本来就有，`META_COLUMNS` 会在写入时剥离），让审批视图自洽：
顶层 `new_merchants_without_source` 列出**确认是新商户却查不到来源**的名字 ——
新商户唯一的审核依据就是「凭什么说这家是放贷商」，没来源等于盲审。

## 硬约束

违反即产生死规则或误伤（详见仓库根 CLAUDE.md 的 liability 章节）。
前两条只适用于 **counterparty_keyword_rules.csv**，第三条只适用于
**home_loan_car_loan_rules.csv** —— 两套语义的结论经常相反，别互相搬。

### counterparty_keyword_rules.csv

1. **只生成 keyword 规则**。CSV 没有 `rule_type` 列，loader 只读 `rule_type`，
   写 `match_type=regex` 的行会被当 keyword 处理（转大写 + `re.escape`）。
   现有 CSV 里那条 `DT\.[A-Za-z0-9]+\s+Sunshine` 就是这样一条死规则。
2. **不得生成以数字开头的 keyword**。边界是 `(?<![A-Za-z])` 而非 `\b`，
   `360 CASH LOANS` 会误伤 `1360 CASH LOANS`。
   反过来说，Format A 的数字开头 keyword 是**匹配不到**（`\b` 拦住了），方向相反。
3. keyword 大小写不敏感（引擎把文本转大写），但统一写大写便于比对。

### home_loan_car_loan_rules.csv（Format A）

4. `match_scope` 只能是 `text` / `text_or_counterparty` / `all`。写成别的值
   loader 拼不出 `bucket_key`，**静默丢弃该行**（`counterparty.py:383`）。
5. `match_type` 只能是 `keyword` / `regex` / `always`，且 `always` 只在
   `match_scope=all` 且无 `pattern` 时才成立。带 pattern 的 `always` 会被当 regex 编译。
6. ⚠️ **`match_scope=all` 配 `match_type=keyword` 是个陷阱**：loader 把文本存进
   `rule["keywords"]`（`:373`），而 `_apply_flag_rules` 的 `all_rules` 分支只找
   `rule["pattern"]`（`:607`）—— 文本条件被整段忽略，规则变成「命中所有满足
   account_type/dr_cr/bank/amount_gt 的行」。要限定文本就改 `match_type=regex`
   或换 `text` / `text_or_counterparty`。
7. `target_field` 只能是 `is_home_loan` / `is_car_loan`；`enabled=0` 的行直接不加载。
8. Format A 匹配的是**原始 `text` 列**（不压缩空白、不转大写），所以
   `pattern` 里要用小写 + `\s*` 容忍空格（`toyota\s*finance`）；写死一个空格
   会在双空格文本上失配。
9. `rule_id` / `rule_name` 被 loader 忽略，仅供人读和 `search_merchant.py` 检索 ——
   但它们**是**这个文件里唯一的商户名记录，别名必须挂在已有商户的 `rule_name` 下。

> 现有 `counterparty_keyword_rules.csv` 里有 7 处踩了上面第 1、2 条
> （row 2 的 `360 Cash Loans`/`30/12 Financier`、row 189 的 `1 of 4`…`4 of 4`、
> row 243 的死 regex）。实测这些 keyword 在 2026-09-09 的数据上命中数为 0，
> 属于潜在风险而非现行故障。**改它们属于规则变更，需要单独走审批。**

## 检索源优先级（Step 2 用）

`AFCA 会员名录`（澳洲持牌放贷商必须加入，覆盖最全）> `ASIC 信贷牌照注册` >
`ABR`（`modules/merchant_kb` 已有 XML 管线可复用）> 贷款对比站。

## 文件

| 文件 | 用途 |
|------|------|
| `_shared.py` | 两侧共用的 liability 类别判定 + 引擎级匹配语义 |
| `gap_source.py` | Step 1：找缺口 |
| `evidence.py` | Step 3：数据验证闸门 |
| `README.md` | 本文件 |
