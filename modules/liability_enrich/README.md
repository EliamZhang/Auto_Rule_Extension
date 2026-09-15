# liability_enrich — Liability 放贷商规则联网发现

从分类报告里找出 liability 引擎没认出来的放贷商，联网核实后生成候选规则，
再交给仓库原有的审批链路写库。

**本模块只产出候选，不写规则。** 规则落库仍然只能走 `apply_rules.py`，
并且必须经过人工审批（见仓库根 CLAUDE.md 的「重要约定」）。

## 四步流水线

```
Step 1  gap_source.py        数据侧找缺口        → reviews/<date>/liability_gaps.json
Step 2  liability-enrichment 联网侧核实（Skill）  → reviews/<date>/liability_candidates.csv
Step 3  evidence.py          数据侧验证（闸门）   → 就地回填候选 CSV + liability_evidence.json
Step 4  原有链路：validate_candidates → baseline diff → 🔴 人工审批 → test_rules → 🔴 确认 → apply_rules
```

Step 2 没有脚本 —— 判断「这个商户到底是不是放贷商」需要联网检索和常识，
由 `.claude/skills/liability-enrichment.md` 承担。

## 用法

```bash
# Step 1：从分类报告找缺口
python modules/liability_enrich/gap_source.py \
    --input input/202609091024.xlsx \
    --output reviews/2026-09-14/

# Step 2：跑 /liability-enrichment skill 联网核实，写出 liability_candidates.csv

# Step 3：用真实数据验证候选（就地回填 hit_count/risk_level/samples/status）
python modules/liability_enrich/evidence.py \
    --candidates reviews/2026-09-14/liability_candidates.csv \
    --input input/202609091024.xlsx

# Step 4：走原有链路
python scripts/validate_candidates.py --review_dir reviews/2026-09-14/
```

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
`strong` = 命中了 LOAN/LENDER/LEND/BORROW/PAYDAY/PAWN/BNPL，
`weak` = 只命中 CASH/CREDIT/FINANCE。弱信号排最后但不丢弃（宁可漏判不要误判）。

## Step 3：证据闸门

`evidence.py` 用**引擎一模一样的正则**在真实交易上重放每条候选：

```
(?<![A-Za-z])  + re.escape(keyword)  +  (?![A-Za-z])      在「压缩空格 + 大写」的文本上
```

⚠️ 不是 `\b` —— 数字可以穿透。这是 `_shared.py` 存在的理由：
如果这里用了宽松的 `str.contains`，会报出线上根本不会发生的命中，
还会漏掉数字穿透这类真实故障。

每条候选得到：

| 字段 | 含义 |
|------|------|
| `hit_count` | 匹配到的行数 |
| `hit_unclassified` | 其中尚未被任何引擎认领的 —— **真正的增益** |
| `hit_liability` | 其中已经是负债类的 —— 只是加固 |
| `hit_other` | 其中**当前属于其他引擎**的 —— liability 优先级 300，这些行会被抢走 |
| `risk_level` | 由被抢比例决定：`高`（≥50%）、`中`（有抢）、`低`（无抢）、`零增益`（零命中）、`死规则`（keyword 本身非法） |

`hit_other` 是这个闸门存在的意义：liability 排在 transfer(1)/initial(10)/dishonour(150)/income(200)
**之后**、all_other_credit(400)/fee(500)/rent(800)/catch_all(999) **之前**，
所以它既抢不了前面的引擎，也会把后面引擎认领的行整行覆盖掉。

### status 的取值

| 值 | 含义 |
|----|------|
| `☐ confirm` | 值得人工看一眼 |
| `✗ 零增益` | 零命中，联网假设未获数据支持，建议直接丢弃 |
| `✗ 死规则` | keyword 本身非法（空、以数字开头），永远不会按预期生效 |

⚠️ **`apply_rules.py` 只写入 `status == "confirmed"` 的行**（`apply_rules.py:137`），
上面三个值都不是 `confirmed`，所以这一步不会意外落库 —— 必须人工改写状态。

## 候选 CSV 契约

列名与 `raw/liability_rule/counterparty_keyword_rules.csv` 一致，尾部追加元数据列：

```
target_file,keyword,counterparty,product_type,match_type,status,hit_count,risk_level,illion_category,samples,evidence_source
```

- `target_file` 必须是 `counterparty_keyword_rules.csv`（liability 有 7 个规则文件，
  不写会被 `apply_rules._detect_target_file` 落到第一个文件上）
- `keyword` 用**分号**分隔多变体（与引擎的 `split_upper_terms` 一致，不是 `|`）
- `evidence_source` 记联网来源 URL 供审批复核。它已加入 `scripts/common.py` 的
  `META_COLUMNS`，写入前会被剥离，不会污染 `raw/`

`evidence.py` 的额外诊断（`hit_other` 明细、冲突引擎、已被谁认领）写在同目录的
`liability_evidence.json` 里，**不放 CSV** —— 任何不在 `META_COLUMNS` 里的列
都会被 `apply_rules` 当规则数据写进 `raw/`。

## 硬约束

违反即产生死规则或误伤（详见仓库根 CLAUDE.md 的 liability 章节）：

1. **只生成 keyword 规则**。CSV 没有 `rule_type` 列，loader 只读 `rule_type`，
   写 `match_type=regex` 的行会被当 keyword 处理（转大写 + `re.escape`）。
   现有 CSV 里那条 `DT\.[A-Za-z0-9]+\s+Sunshine` 就是这样一条死规则。
2. **不得生成以数字开头的 keyword**。边界是 `(?<![A-Za-z])` 而非 `\b`，
   `360 CASH LOANS` 会误伤 `1360 CASH LOANS`。
3. keyword 大小写不敏感（引擎把文本转大写），但统一写大写便于比对。

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
