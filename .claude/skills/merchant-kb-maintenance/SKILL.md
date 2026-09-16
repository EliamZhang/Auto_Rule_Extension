---
name: merchant-kb-maintenance
description: 构建与维护 merchant_kb.csv（initial 引擎的商户知识库）—— ABR XML 导入、keyword 清洗、人工条目合并、新增商户联网分类。用户说"更新知识库"、"ABR"、"导入商户"、"merchant_kb"、"补 keyword"、"商户分类"、"/merchant-kb-maintenance" 时触发。
---

# Merchant KB Maintenance Skill

`modules/merchant_kb` 的流程入口。把该模块零散的脚本串成一条有顺序、有校验、有回滚点的流水线。

## 语言要求（强制）

- **所有对话输出必须使用中文**（简体中文）
- **脚本名、字段名、命令行参数、商户名、category 名**保持英文原文

## 这个 skill 不做什么

**不直接写 `raw/initial_rule/merchant_kb.csv` 以外的任何规则文件**，也不改变 initial 引擎逻辑。
`merchant_kb.csv` **就是线上规则文件**，改它等于改线上分类 —— 人工确认不可省。

## 铁律：输出契约（改前必读）

`raw/initial_rule/merchant_kb.csv` 由 finv 的 `initial_engine` 以 `usecols` 锁定：

| 约束 | 值 | 违反后果 |
|------|----|---------|
| 列数 | **恰好 3 列**：`merchant_name, keywords, category` | 多余列被静默忽略；`category_source`/`category_updated_at` **已不存在** |
| 编码 | UTF-8（当前带 BOM） | finv 按 `utf-8-sig` 读，BOM 增删都能读，但会改变字节 → `sync_rules.py` 判为一次本地改动 |
| `keywords` 分隔符 | **`|`**（管道） | 别的分隔符 → 整串被当成一个 keyword |
| 每商户 keyword 上限 | **50** | 超出部分**静默丢弃**（`Australia Post` 有 5,486 个，5,436 个从未生效） |
| keyword 格式 | **大写，仅 `[A-Z0-9 ]`，压缩空格** | 引擎加载时**不再**自动 `clean_text()`，写错即静默失配，无兜底 |
| `category` | **不能是 `Financial Institutions`** | 整行在加载时被丢弃（由 liability/dishonour 处理） |
| 单行 field 上限 | 可达 137 KB | `settings.py` 导入时已设 `csv.field_size_limit(10_000_000)`，跑本模块脚本无需自理；**手写一次性片段、或单独 import `utils` 之外的东西时**仍要自己设，否则抛 `_csv.Error` |

> ⚠️ **BOM 会被 5 处写入点抹掉，不是只有 `build_knowledge_base.py`。** 这些地方都用
> `encoding="utf-8"` 写临时文件再原子替换，跑一次就静默丢掉 BOM：
>
> | 脚本 | 函数 | 触发场景 |
> |------|------|---------|
> | `build_knowledge_base.py` | `write_merged_kb()` | 场景 A 第 3 步 |
> | `dedup_keywords.py` | `preclean_keywords()` | 不带参数的默认运行 |
> | `dedup_keywords.py` | `process_keywords()` | **场景 A 第 4 步 `--full`**（最常踩） |
> | `merge_manual_entries.py` | `merge_add_files()` | 场景 B |
> | `merge_category.py` | `merge()` | DeepSeek 分类回填 |
>
> 后果只是字节变了 —— finv 按 `utf-8-sig` 读，有无 BOM 都能读，数据本身没有损坏。
> 但 `sync_rules.py` 会因此判为一次本地改动。**收尾时按第 27 行的编码约束对一下**，
> 想保持 BOM 就照 `update_category.py:31` 的 `ENCODING = "utf-8-sig"` 写法收尾。
>
> （`dedup_keywords.py` 另有一处 `encoding="utf-8"` 是写**报告** CSV、
> `label_merchants.py` 那处是写缓存 JSON —— 都不碰 KB，别误改。）

## 工作目录

本模块的脚本用**相对当前工作目录**的路径。跑之前先：

```bash
cd modules/merchant_kb
```

`settings.FINAL_OUTPUT` 已指向仓库根的 `raw/initial_rule/merchant_kb.csv`，
各脚本的 `--input`/`--target`/`--merchant-kb` 默认值**都已指向它**，不带路径参数即可。

## 流程

### 场景 A：ABR 季度更新（有新的 XML 公告）

```bash
cd modules/merchant_kb

# 1. 准备 ABR 报文 → xml_input/
#    来源：data.gov.au 上的 "ABN Bulk Extract" 数据集（ABR 的官方全量导出，
#    不是 ABN Lookup 的网页/API）。下载页上通常是 <yyyymmdd>Public01.zip …
#    Public20.zip 这样的分卷，以页面实际显示为准。
mkdir -p xml_input manual_entries
#    解压全部 zip 到 xml_input/ —— 脚本用 sorted(glob("*.xml")) 扫，文件名任意，
#    但必须是「每个分卷一个 .xml」平铺在里面，不要嵌套子目录。
unzip -o '下载目录/*Public*.zip' -d xml_input/
ls -la xml_input/*.xml

#    先确认拿对了数据集：每个 XML 应是一串 <ABR> 记录，
#    内含 <ABN status="..." ABNStatusFromDate="..."> 与
#    <MainEntity><NonIndividualName><NonIndividualNameText>。
#    用下面这条抽前几个记录看一眼，对不上就是数据集拿错了，别往下跑：
python -c "
import xml.etree.ElementTree as ET, glob
f = sorted(glob.glob('xml_input/*.xml'))[0]
for _, el in ET.iterparse(f, events=('end',)):
    if el.tag == 'ABR':
        print(ET.tostring(el, encoding='unicode')[:400]); break
"
#    ⚠️ 全量解压后体积很大（数十 GB 量级），先确认磁盘空间。
#    xml_input/ 已 gitignore，不入库。

# 2. 先备份（这是线上规则文件）
python -c "import shutil; shutil.copy('../../raw/initial_rule/merchant_kb.csv','../../raw/initial_rule/merchant_kb.csv.bak')"

# 2.5 取「改前基线」——第 5 步要靠它做差，务必先跑
#     不取就没法区分「本次跑坏的」和「本来就有的存量」。存量有多少：
#     Financial Institutions 约 5.08 万行、keyword 超 50 的商户 239 家
#     （都是既有状态，前者由 initial_engine 加载时整行丢弃、后者静默截断）。
#     按老写法「任何一项非零就停」，这个校验永远过不去。
#     快照落在 backup/（整个目录 gitignore），不会污染 raw/。
python - <<'EOF'
import csv, json, pathlib
csv.field_size_limit(10_000_000)
kb = pathlib.Path('../../raw/initial_rule/merchant_kb.csv')
rows = list(csv.DictReader(kb.open(encoding='utf-8-sig')))
bad = sum(
    1 for r in rows for kw in r['keywords'].split('|')
    if kw != kw.upper() or not set(kw) <= set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ')
)
snap = {
    'rows': len(rows), 'columns': list(rows[0].keys()),
    'empty_category': sum(1 for r in rows if not r['category'].strip()),
    'fin_inst': sum(1 for r in rows if r['category'] == 'Financial Institutions'),
    'over_50': sum(1 for r in rows if len(r['keywords'].split('|')) > 50),
    'bad_format': bad,
}
p = pathlib.Path('backup/kb_gate_before.json')
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding='utf-8')
print('[gate] 改前基线:', json.dumps(snap, ensure_ascii=False))
EOF

# 3. 导入 ABR XML
#    归并键**不是** match_key（那是内部 KB 才有的列，最终 3 列里根本没有）。
#    实际是 find_existing_owner()：先按 normalize_lookup(法定名称) 命中已有商户，
#    否则拿该实体的各 keyword 去 keyword_owner 索引里认领主；都认不出的才当新商户追加。
python build_knowledge_base.py

# 4. 清洗 keyword（去短词、停用词、大小写重复、与商户名零重叠的）
#    必须 --full：KB 是 3 列、没有 keyword_updated_at 列，
#    带 --changed-since 会被 dedup_keywords.py 直接 parser.error 退出；不带参数的
#    默认路径只做 preclean 归一化，不走去重流水线。
python dedup_keywords.py --full

# 5. 校验：拿第 2.5 步的基线做差，只追究「本次引入的」问题
python - <<'EOF'
import csv, json, pathlib
csv.field_size_limit(10_000_000)
kb = pathlib.Path('../../raw/initial_rule/merchant_kb.csv')
rows = list(csv.DictReader(kb.open(encoding='utf-8-sig')))
bad = set(
    (r['merchant_name'], kw) for r in rows for kw in r['keywords'].split('|')
    if kw != kw.upper() or not set(kw) <= set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ')
)
now = {
    'rows': len(rows), 'columns': list(rows[0].keys()),
    'empty_category': sum(1 for r in rows if not r['category'].strip()),
    'fin_inst': sum(1 for r in rows if r['category'] == 'Financial Institutions'),
    'over_50': sum(1 for r in rows if len(r['keywords'].split('|')) > 50),
    'bad_format': len(bad),
}
try:
    before = json.loads(pathlib.Path('backup/kb_gate_before.json').read_text(encoding='utf-8'))
except FileNotFoundError:
    raise SystemExit('[gate] [FAIL] 找不到改前基线，第 2.5 步没跑。补跑后再校验。')

fails = []
# ── 硬门：契约不变量，必须干净 ──
if now['columns'] != ['merchant_name', 'keywords', 'category']:
    fails.append(f"列结构变了: {now['columns']}（必须是恰好 3 列）")
if now['empty_category']:
    fails.append(f"空 category {now['empty_category']} 行")
if now['bad_format']:
    fails.append(f"格式违规 keyword {now['bad_format']} 个，例: {list(bad)[:5]}")

# ── 存量项：非零本身是既有状态，只追究增量 ──
print(f"行数                      {before['rows']:>9,} → {now['rows']:>9,}  (Δ{now['rows']-before['rows']:+,})")
for key, label in (('fin_inst', 'Financial Institutions'),
                   ('over_50', 'keyword 超 50 的商户')):
    d = now[key] - before[key]
    print(f"{label:<24} {before[key]:>9,} → {now[key]:>9,}  (Δ{d:+,})")
    if d > 0:
        fails.append(f"{label} 比改前多 {d} 条 —— 确认是有意为之再继续")

if fails:
    print("\n[gate] [FAIL] 未通过：")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("\n[gate] [PASS] 通过")
EOF
```

**硬门（列结构 / 空 category / keyword 格式）必须干净；存量项只追究增量。**
任何一项 fail 都要停下来报告，不要继续。行数涨是正常的（新 ABR 商户），
不构成 fail；但 `Financial Institutions` 或「超 50」比改前**变多**要人工确认 ——
前者会被 initial_engine 整行丢弃、后者静默截断，都可能是白干。

### 场景 B：新增人工条目

```bash
cd modules/merchant_kb
mkdir -p manual_entries                      # 目录不入库，首次使用先建
ls manual_entries/                           # 确认有新的 CSV
python merge_manual_entries.py --add-dir manual_entries/
```

合并后跑一遍场景 A 的校验块。

### 场景 C：新增商户的联网分类

新增的 ABR 商户没有 category，由 **`classify-merchants`** skill 处理：

```
/classify-merchants batch_size=10
```

它直接读写 `merchant_kb.csv`，用 `cache/web_classify_tracking.json` 记录已搜索过的商户。
**它不调用本模块的脚本。**

> 当前 KB 的空 `category` 记录为 **0**（874,600 行全部有分类），所以这条流程
> 只在场景 A 导入新商户后才有实际工作量。

### 场景 D：DeepSeek 批量分类（不联网，成本低）

```bash
cd modules/merchant_kb
export DEEPSEEK_API_KEY=<key>
python label_merchants.py --api-key "$DEEPSEEK_API_KEY"
```

联网证据优先用 `classify-merchants`；DeepSeek 只适合批量粗分类。

## 🔴 收尾：人工确认 + 同步

**不要自动提交。** 改动 `merchant_kb.csv` 之后：

1. 报告 diff 摘要给用户：行数变化、新增/修改的商户数、category 分布变化
2. 让用户确认
3. 确认后才同步到 finv：

```bash
# 看漂移状态
python scripts/sync_rules.py status

# ⚠️ pull 是 finv → raw，方向相反
# 推 raw → finv：走 apply_rules 的审批门（--review_dir 是必填参数）
python scripts/apply_rules.py --review_dir reviews/<date>/ --sync_to <finv_path>
```

⚠️ **`apply_rules.py` 不是 KB 变更的同步通道**：它只 glob `reviews/<date>/*_candidates.csv`，
只把**本轮刚由候选追加写出**的规则文件复制到 finv（sync 只发生在「写入成功」分支内）。
本 skill 的场景 A/B/D 是直接改写 `merchant_kb.csv`（build / merge / dedup / DeepSeek 分类），
**这些改动不会被它推送** —— 确认后只能人工把 `raw/initial_rule/merchant_kb.csv` 复制到
`<finv>/initial_engine/merchant_kb.csv`，并自行核对落地结果。

`sync_rules.py` 是**只拉不推**的。

## 遗留脚本（默认不要跑）

| 脚本 | 状态 |
|------|------|
| `split_uncategorized.py` | ⚠️ 会先 `rmtree` 重建 `knowledge-base-split/` **再**读 KB。当前 KB 无空 category，跑它只会得到 20 个空 `[]` 分片 |
| `update_category.py` | 配套上述批量流程的写回脚本 |
| `merge_category.py` | 合并分类结果到 KB |

## 常见坑

| 现象 | 原因 |
|------|------|
| `_csv.Error: field larger than field limit` | 手写片段没设 `csv.field_size_limit(10_000_000)`（模块脚本已由 `settings.py` 设好） |
| keyword 明明写了却不匹配 | 没大写／含 `[A-Z0-9 ]` 以外的字符／是 97 个 STOPWORDS 之一 |
| 改动后 `sync_rules.py` 报「raw 领先」 | 正常 —— 你确实改了 raw。要么走审批推送，要么用 pull 放弃 |
| BOM 消失 | **5 处写入点**都用 `utf-8` 写（见上方输出契约表下的清单），不只是 `build_knowledge_base.py`。属已知副作用 |
| gate 段抛 `UnicodeEncodeError: 'gbk' codec can't encode character '✓'` | `✓`/`✗`(U+2713/U+2717) 在 cp936 控制台下编不出来。**已改成 ASCII `[PASS]`/`[FAIL]`，不要改回符号** —— 这条 gate **通过时也要 print**，崩了退出码同样是 1，和「未通过」长得一模一样 |
| 第 5 步校验「存量项」报错 | 看 Δ 而不是绝对值：`Financial Institutions` 5 万行、超 50 的 239 家是**既有**状态，只有比改前**变多**才要人工确认 |
| `git status` 显示 74 MB 的 csv 变更 | `merchant_kb.csv` 在 git 里。**移出 git 的决定已通过但未执行** |

## 相关文档

- `modules/merchant_kb/CLAUDE.md` — 模块级细节（脚本职责、架构）
- `modules/merchant_kb/README.md` — 快速开始 + 相对上游 README 的修正
- 仓库根 `CLAUDE.md` 的 initial_engine 章节 — 引擎侧加载/匹配语义
