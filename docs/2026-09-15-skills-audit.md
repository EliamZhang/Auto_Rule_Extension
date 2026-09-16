# 5 个 skill 的实地复跑与问题清单

日期：2026-09-15 审计，2026-09-16 完成修复 · 范围：`.claude/skills/` 下 5 个 skill + 它们调用的脚本
方式：每个 skill 按自己的流程**实际跑一遍**（不是读代码），问题都以可复现的命令佐证。
改动原则：只做外科式修补，不动架构。

## 一句话结论

主链路（`auto-rule-extension`）能跑通、数字对得上（30 候选 ↔ 30 规则 ↔ 30 dry-run），
但有 **1 个会把结论判反的 bug**（liability 分隔符）和 **1 个只在中文 Windows 上炸的
bug**（performance-report 全线不可用）；另外发现 `income_pattern_rules.csv` 有 4 行
CSV 语法错误，**在 finv 生产侧静默失效**。

三类都已修复并验证（2026-09-16 补做了后两项的修复与影响面实测）。
`income_pattern_rules.csv` 那 4 行修好后经实测**在当前数据上是行为中性的（0 行分类变化）**
—— 详见下方第 2 节末尾的「修复后的实际影响」。

---

## 🔴 高：会把判断带偏

### 1. liability 多变体分隔符判反 —— `baseline diff` / `test_rules` 一直在给相反的答案

**已修** `scripts/common.py`

liability 的 `counterparty_keyword_rules.csv` 用**分号**分隔多变体
（引擎 `counterparty.py` 里是 `keyword.split(";")`），而 `common.py` 的
`alpha_edge` 分支按**竖线**拆。于是评分和引擎正好相反：

| keyword 写法 | 在引擎里实际是 | 修复前 diff | 修复后 diff |
|---|---|---|---|
| `A\|B`（竖线） | **死规则**（整串含字面 `\|`，永不命中） | `gain=7` ❌ | `gain=0` ✅ |
| `A;B`（分号） | 有效，命中 7 行 | `gain=0` ❌ | `gain=7` ✅ |

用磁盘上的真实候选 `reviews/2026-09-09_1025/liability_candidates.csv`
（`PRINCIPLE BALANCE ADJUSTMENT|PRINCIPAL BALANCE ADJUSTMENT`）实测：

```
$ python scripts/baseline.py diff --candidates <竖线版> --baseline .audit_tmp/baseline --input input/202609091024.xlsx
  Total gain:      0          ← 修复后：如实反映「这是死规则」
$ python scripts/baseline.py diff --candidates <分号版> --baseline .audit_tmp/baseline --input input/202609091024.xlsx
  candidate_0: gain=7, conflicts=0
  Total gain:      7          ← 修复后：如实反映「这条有效」
```

**影响范围**：`baseline.py diff`（阶段 4.2）、`test_rules.py`（阶段 6），
以及已经落盘的 `reviews/2026-09-09_1025/impact_report.json` —— 它现在写的是
`"pattern": "PRINCIPLE BALANCE ADJUSTMENT|PRINCIPAL BALANCE ADJUSTMENT", "gain": 7`，
也就是**给一条死规则配了 7 条假证据**（`gain_samples` 里甚至列了命中的文本）。
照这份报告做审批，会砍掉能用的规则、留下不能用的。

> 讽刺的是 `liability-enrichment/SKILL.md:161-166` 已经把这个坑写在文档里了
> （「改成 `;` 后 hit_count 7……差点被当成零增益扔掉」），但 `common.py` 没跟上，
> 所以文档的提醒一直是空转的。

**改动**：`split_keyword_variants()` 加 `sep` 参数（默认 `|` 不变），`alpha_edge` 传 `sep=";"`。

### 2. `raw/income_rule/income_pattern_rules.csv` 有 4 行 CSV 语法错误 —— 生产侧静默失效

**已修**（2026-09-16，经用户确认后执行）

第 **150 / 151 / 208 / 209** 行的 `pattern` 字段里含逗号却没加引号，
而 finv 的加载器是 `csv.DictReader`（`income_engine/domain/classification.py:66`），
按逗号切 → `pattern` 在第一个逗号处被**截断**，残余落进 `match_type`：

| 行 | 分组的语义 | 作者想写的 | finv 修复前实际加载成 | 后果 |
|---|---|---|---|---|
| 150 | `behavior_exclusion` | `\bTFR\s*FROM\s+\d{6,}\b` | `\bTFR\s*FROM\s+\d{6` | 死模式 |
| 151 | `behavior_exclusion` | `\bFUNDS\s*TFER\b.*\bFROM\s+\d{6,}\b` | `\bFUNDS\s*TFER\b.*\bFROM\s+\d{6` | 死模式 |
| 208 | `gig_personal_exclusion` | `\bFROM\s+\d{3,4}-\d{3}-\d{3,}\b` | `\bFROM\s+\d{3` | 死模式 |
| 209 | `gig_personal_exclusion` | `\bFROM\s+\d{7,}\b` | `\bFROM\s+\d{7` | 死模式 |

> ⚠️ **本报告初版在这里写错了一句**：初版称行 208 修复前是「命中面过宽，会命中任何
> FROM 后跟三位数的文本」。**这是错的。** 截断后 `\d{3` 里那个 `{` 没有闭合括号，
> Python `re` 把它当**字面字符**（实测 `re.search(r"\d{3", "204")` → `None`，
> 要文本里真出现 `2{3` 才命中）。所以行 208 和另外三条一样是**死模式**，不是过宽。
> 四条的方向一致：都该生效而没生效。

四个字段加引号即可（实测 CSV 侧 diff 恰好就是这 4 行，BOM 与 CRLF 都原样保留，
且 `.bak` 已备份）。

**修复后的实际影响（实测，不是推断）**

1. **解析层**：`csv.DictReader`（finv 用的）与 `pandas.read_csv`（ARE 用的）现在都能
   完整读出 4 条 pattern，行数仍是 217，全部 regex 可编译。
   附带修好了「pandas 直接抛 `ParserError` → ARE 侧整个文件读不出来」的老问题。
2. **行为层**：只有两处排除真的由死转活（行 151 命中 1,935 行、行 209 命中 2,394 行，
   去重后 **2,394 行**），行 150/208 在本数据集上命中 0 行。
3. **分类结果层：0 行变化。** 这 2,394 行里，2,373 行已被 transfer 引擎（priority 1，
   早于 income 的 200）认领，剩下的也没有一行落在被这两条排除 gate 住的规则上。
   finv 的 gate 位置是
   `income_engine/domain/classification.py:794 / 807 / 822`（`has_behavior_exclusion`）
   与 `:1130`（`has_gig_personal_exclusion_keyword`，只 gate
   `income_self_employed_gig_repeat_payer`）；按 `classification_rule_id` 逐一比对，
   命中行与这 4 条规则的**交集为 0**。

   所以这次修复在当前数据上是**中性**的 —— 它拆掉的是一批随时会踩的哑雷
   （行 209 的 `\bFROM\s+\d{7,}\b` 只要有一行自转账走到 gig 规则就会生效），
   而不是修正了当下的误判。

   > 顺带观察（**未修，也不属于本次范围**）：有 6 行
   > `ANZ M-BANKING TRANSFER … FROM 428281982`（credit / $500 / 同一来源账号重复 6 次）
   > 被判为 Wages（`salary_payg_wages_rule`）。它们的形态和行 209 想排除的自转账一致，
   > 但 `gig_personal_exclusion` 只 gate gig 规则，碰不到 `salary_payg`，
   > 所以修复救不了它们。这 6 行究竟是真工资还是自转账无法从数据断定，留作观察项。

**修完的状态**：`sync_rules.py status` 报 `income/income_pattern_rules.csv` 为
**raw 领先**（218 行 = 217 规则 + 表头）；`sync_upstream.py --dry-run` 报
`income_rule/income_pattern_rules.csv`「上游未变，但本地已改动」——
两者都符合预期。**推送仍需 `apply_rules.py --sync_to`，本次未推送。**


**副产品**：因为 pandas 默认遇到字段数不一致会直接抛 `ParserError`，
**整个文件在 ARE 侧读不出来** —— `validate_candidates.py` 每次只能打一行
`Could not load income_pattern_rules.csv` 就跳过 income 的重叠检查。
`analyze_gaps.py` 里那个「读规则文件算已存在 pattern」的函数也有同样的静默 `except`，
但它**全仓零调用**（见下），所以没造成重复候选。

### 3. `initial_candidates.csv` 的 Gambling 铁律没有机械拦阻，而上次真被绕过

**已修** `scripts/validate_candidates.py`

`_check_initial()` 只拦 `category == "Financial Institutions"`，**不拦 Gambling**。
而 2026-09-09 那次运行的 `reviews/2026-09-09_1025/initial_candidates.csv` 里就有
`NTRX`（category=`Gambling`），而且 `reviews/` 下**从来没有出现过
`gambling_candidates.csv`** —— 铁律（3.1/3.1-G）当时完全没起作用。

已补上对称的 ERROR。跑一次 `validate_candidates.py` 立刻报出来：

```
candidate_11: ENGINE_ERROR: initial: category='Gambling' —— 赌博商户自 2026-09-02 起已从
  merchant_kb 移出，由 priority 180 的 gambling_engine 接管。请写 gambling_candidates.csv
```

### 4. `performance-report` 在中文 Windows 上整个跑不起来

**已修** `modules/assessment/scripts/run_report.py`

不设 `PYTHONUTF8` 时实测：

```
[md_zh] 开始 …
[md_zh] 失败 (2.5s)
Traceback (most recent call last):
  File "...\run_report.py", line 130, in main
    print(f"         {tail}", flush=True)
UnicodeEncodeError: 'gbk' codec can't encode character '�' in position 25
```

`run_report.py:121` 用 `encoding="utf-8"` 解子进程 stderr，但子进程是往 GBK 管道写的，
解出 `U+FFFD`，再 print 到 GBK stdout 就炸。**这一炸发生在编排循环内部**，
所以 SKILL.md 承诺的「单个生成器失败不阻断其余生成器 + 汇总清单」根本不成立：
8 个生成器只跑了 1 个（还失败了），汇总没打印。

修完实测（**GBK 控制台、不设任何环境变量**）：`成功 8/8`、退出码 0、落 11 个文件。

**改动**：子进程注入 `PYTHONUTF8=1`（让编解码是一套）+ 父进程
`stdout/stderr.reconfigure(errors="replace")` 兜底。

---

## 🟡 中：不会崩，但会误导人

### 5. `category_catalog.json` 里 Gambling 的 owner 停在 `initial,gambling`

**已修**（2026-09-16，经用户确认后执行）`raw/category_catalog.json:16`

按 `common.py:481-486`，`owner_engine_id` 的第一个 engine 就是主归属
（`owners.split(",")[0]`），所以修复前 `Gambling → initial`。
但 Gambling 自 2026-09-02 起已全部移出 merchant_kb（KB 的 Gambling 行数为 0），
initial 不再认领这些行，**只有 gambling 引擎会输出 `Gambling`** ——
所以按 catalog 自己的单归属惯例（`Fees`→fee、`Wages`→income、`Internal Transfer`→transfer）
改成 `"gambling"` 即可，**不是** `"gambling,initial"`。

活着的触发路径：**illion 标了 Gambling、但文本没命中那 27 个 `gambling_indicators`**
的行 —— 它们会走 `ambiguous` 分支落到 catalog，然后被判给 **initial**，
正是铁律禁止的方向。

> 对照：`"Rent": "initial,rent"` **没有动，也不该动** —— rent 的 21,794 个租赁机构
> 在 merchant_kb 与 `rent_rules.csv` 里 100% 重合，这些行确实先被 initial 认领，
> 主归属写 `initial` 是对的。Gambling 与 Rent 表面同构，实则不同，这是本次唯一
> 需要区分的地方。

连带改动：`analyze_gaps.py` 加了一条与既有 Rent 分支对称的 `illion_cat == "Gambling"`
兜底（脚本侧，无同步冲突）—— 现在是双保险。

**实测**：重跑 `analyze_gaps.py` 后，5 条 NTRX 模式落进 **gambling 桶**
（`gambling: 5 patterns, top illion categories: [('Gambling', 5)]`）；
在此之前磁盘上那份 `gap_summary.json` 把它们放在 **transfer 桶**。
`gap_summary.json` 里的 `category_catalog.Gambling.owner_engine_id` 也如实变成 `gambling`。

改了之后 `sync_upstream.py --dry-run` 报 `category_catalog.json`「上游未变，但本地已改动」，
属预期（推送方向）。**本次未推送。**

### 6. `gap_summary.json` 里 `illion_category` 是字面字符串 `"nan"`

**已修** `scripts/analyze_gaps.py`

`str(row.get("category", ""))` 对空单元格返回字面 `"nan"`。2026-09-09 那次产出里有
**124 个 `"illion_category": "nan"`、131 个 `third_parties: ["nan"]`**，
日志里也照打 `top illion categories: [('nan', 60)]`。skill 在阶段二读它，
会把 `nan` 当成一个真实类别名。

修复前后对比（同一输入）：`124 → 0`、`131 → 0`，日志变成 `[('', 60)]`（如实表示「无 illion 标签」）。

### 7. 三处 validator 误报

**已修** `scripts/validate_candidates.py`

- **transfer 每轮必报 `schema_errors: 1`**：原本拿候选 CSV 去比**每一个**规则文件，
  只有「跟所有文件都不匹配」才报错。transfer 有 8 个规则文件、schema 各异，
  这个条件实际不可达。改成按每行的 `target_file` 比对之后，又发现
  `dr_cr` 的差异其实是**噪音** —— `apply_rules.py:211-215` 本来就会把缺列填成 `""`、
  把多列丢掉，而 transfer 现有 57 条规则里有 53 条 `dr_cr` 就是空的。
  所以列的差异降级为 log 提示，只保留真正可执行的「target_file 不属于本引擎」为 error。
  修复后 `schema_errors: 0`。
- **`re.search(r"[a-z]", pattern)` 误伤转义**：`\b`、`\s` 里的字母被当成小写字母，
  于是 `\bBPAY\s+CSA\b` 这种完全规范的全大写正则被报 WARNING，并因此
  （配合 `if issues:` 的判定）被踢出 `valid_candidates`。改成 `(?<!\\)[a-z]`。

### 8. `merchant-kb-maintenance` 的 gate 在 GBK 下崩溃，且**通过时也崩**

**已修** `.claude/skills/merchant-kb-maintenance/SKILL.md`

第 157/178/182 行的 `✗`(U+2717) / `✓`(U+2713) 在 cp936 下编不出来，
实测三个 print 全部 `UnicodeEncodeError`。要命的是第 182 行
`print("\n[gate] ✓ 通过")`——**校验通过才执行**，崩了之后退出码同样是 1，
和「未通过」在终端上长得一模一样。改成 ASCII `[PASS]`/`[FAIL]`，
并在「常见坑」里写了「不要改回符号」。

### 9. `performance-report` SKILL.md 的过时描述

**已修** `.claude/skills/performance-report/SKILL.md`，3 处：

- 第 35-37 行说模块内历史底稿是「35 类旧口径、会直接抛错」——
  **实测它就是 36 类**（5 张表齐全，第 23 行表头、24~59 行 36 个类别），`--with-charts`
  跑出 8/8、11 文件。真正的问题是它**不在 git 里**（被根 `.gitignore` 排除）。
- 第 111-113 行说 `python-docx`/`reportlab` 是「函数内延迟导入」——
  **都是模块级 import**；而且 4 个图表生成器都
  `from generate_docx_category_report import GROUP_OF, GROUP_ORDER, norm_cat, to_float`，
  所以它们**也硬依赖 `python-docx`**，只装 matplotlib/seaborn 修不好。
- 第 100 行的报错串 `KeyError: 工作表「02_业务聚类对比」不存在` 全仓不存在，
  实际是 openpyxl 的英文原文 `KeyError: 'Worksheet 02_业务聚类对比 does not exist.'`。

---

## 🟢 低：记录一下，不急

- **`analyze_gaps._load_existing_patterns` 是死代码**：约 40 行，定义在
  `analyze_gaps.py:56`，**全仓零调用**（HEAD 版本也一样）。名字很像是喂
  「已存在 pattern」的，容易让人以为它在工作。建议删掉或接上。
- **`reviews/2026-09-09_1025/gap_summary.json` 与当前脚本不是一代**：
  它把 5 条 NTRX 模式放在 **transfer** 桶，而用当前脚本重跑会放进 **gambling** 桶。
  skill 在阶段二读的是磁盘上这份旧产物、阶段三跑的却是新脚本，两边口径不一致。
  （这也解释了为什么「重跑一遍」的桶计数和磁盘上的对不上。）
- **`reviews/2026-09-09_1025/_gen_candidates.py`** 是当时 AI 现场写的生成器，
  硬编码了 `("NTRX", "NTRX|NTRX LIMASSOL", "Gambling", …)` 到 `initial_candidates.csv`
  —— 它把铁律违反**固化成了脚本**。建议从 review 目录里删掉，避免下次被复用。
- **4 个 initial 候选的 keyword 变体是死的**：`initial_candidates.csv` 里
  `PCYC CABOOLTURE`、`PCYC CABOOLTURE EAST MILTON`、`KILMORE LEISURE`、
  `BALWYN HIGH SCHOOL` 这 4 个变体在数据里没有任何命中。
- **`reports/` 无清理机制**：现在 5 个时间戳目录，会一直涨。
- **`✓`/`✗` 类字符在 skill 的 `print` 语句里还有一批**（`auto-rule-extension/SKILL.md`
  的阶段五/七汇总、`✅`/`🔴` 等）。它们的后果是「agent 看到 traceback 后重跑」，
  不像 merchant-kb 的 gate 那样会混淆成败，所以这次没动。
  一劳永逸的做法是给这类片段统一加 `PYTHONIOENCODING=utf-8`。

---

## 我改了什么（9 个文件）

前半是审计当天（2026-09-15）的改动，后两行是 2026-09-16 经用户确认后补的。

| 文件 | 改动 |
|---|---|
| `scripts/common.py` | `split_keyword_variants(sep=)`；`alpha_edge` 传 `sep=";"` |
| `scripts/validate_candidates.py` | `_check_initial` 加 Gambling 门；`(?<!\\)[a-z]` ×2；schema 检查改为按 `target_file` |
| `scripts/analyze_gaps.py` | 新增 `_label()`（空单元格 → `""` 而非 `"nan"`）；补 `illion_cat == "Gambling"` 路由 |
| `modules/assessment/scripts/run_report.py` | 子进程 `PYTHONUTF8=1`；父进程 stdout/stderr `errors="replace"` |
| `.claude/skills/classify-merchants/SKILL.md` | `1,819` → `1,822`（实测：gambling_rules.csv institution 层 1,822 行；merchant_kb.csv.bak 里 Gambling 也是 1,822 行；全仓其余 9 处都写 1,822） |
| `.claude/skills/merchant-kb-maintenance/SKILL.md` | gate 的 `✓`/`✗` → `[PASS]`/`[FAIL]`；常见坑补一行 |
| `.claude/skills/performance-report/SKILL.md` | 回退底稿口径、依赖说明、报错串、行号 1-based 注记 |
| `raw/income_rule/income_pattern_rules.csv` | 第 150/151/208/209 行的 `pattern` 加引号（`.bak` 已备份） |
| `raw/category_catalog.json` | `Gambling: "initial,gambling"` → `"gambling"`（`.bak` 已备份） |

**每个改过的脚本都跑了冒烟**：`py_compile` 全过；`analyze_gaps` 重跑出干净 JSON；
`validate_candidates` 出 `schema_errors: 0`；`baseline diff` / `test_rules` 用两种分隔符
各跑一次做对照；`run_report --with-charts` 在不设环境变量的 GBK 控制台下 8/8；
`apply_rules --dry-run` 报 30 条、无写入。
2026-09-16 改完 `raw/` 后**全链路重跑**：`analyze_gaps` 路由正确（NTRX 进 gambling 桶）、
`validate_candidates` 仍是 `30 / syntax 0 / schema 0 / overlap 4 / value 0 / constraint 2 / warning 0`、
`sync_rules.py status` 与 `sync_upstream.py --dry-run` 都如实反映两处本地改动。

## 原「需要你决定」的两件事 —— 都已按确认执行

1. ✅ **`income_pattern_rules.csv` 那 4 行已修**，影响面实测为 **0 行分类变化**（见第 2 节）。
   ⚠️ **还没有推给 finv**：`raw/` 现在领先，推送要走
   `apply_rules.py --review_dir <dir> --sync_to <finv_path>` 的审批门；
   不推的话 `sync_upstream.py` 会一直把这两个文件标成「本地已改动」。
2. ✅ **`category_catalog.json` 的 Gambling owner 已改成 `gambling`**，同样待推送。

> 另外提醒：`raw/` 下这两个文件改动后，**finv 那份还是旧的**。
> 要让行为真正生效需要推送，而推送前建议先跑一次 finv 侧流水线确认。

## 需要向你说明的两件事

- `reviews/2026-09-09_1025/validation_report.json` 被我的一次冒烟测试**重新生成**了
  （现在 `schema_errors: 0`、`engine_constraint_errors: 2`）。它是可重跑的派生产物，
  目录里的**候选 CSV 和 gap_summary.json 一个字节都没动**。
  `impact_report.json` 的时间戳也是今天（本次审计早期重跑过）。

- 所有测试产物都在 `.audit_tmp/` 下，跑完会删掉；`raw/` 未被写入。
