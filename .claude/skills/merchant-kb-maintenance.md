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
| 单行 field 上限 | 可达 137 KB | 读写前必须 `csv.field_size_limit(10_000_000)`，否则抛 `_csv.Error` |

> ⚠️ `build_knowledge_base.py` 的 `write_merged_kb()` 用 `encoding="utf-8"` 写（**不写 BOM**）。
> 跑一次就会静默抹掉 BOM —— 这是已知的字节级副作用，不是数据损坏。

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

# 1. 确认输入就位
ls -la xml_input/*.xml

# 2. 先备份（这是线上规则文件）
python -c "import shutil; shutil.copy('../../raw/initial_rule/merchant_kb.csv','../../raw/initial_rule/merchant_kb.csv.bak')"

# 3. 导入 ABR XML（按 match_key 更新已有 + 追加新的）
python build_knowledge_base.py

# 4. 清洗 keyword（去短词、停用词、大小写重复、与商户名零重叠的）
python dedup_keywords.py --changed-since <上次日期>

# 5. 校验
python - <<'EOF'
import csv, collections
csv.field_size_limit(10_000_000)
rows = list(csv.DictReader(open('../../raw/initial_rule/merchant_kb.csv', encoding='utf-8-sig')))
print('行数', len(rows), '| 列', list(rows[0].keys()))
print('空 category', sum(1 for r in rows if not r['category'].strip()))
print('Financial Institutions', sum(1 for r in rows if r['category']=='Financial Institutions'))
over = [(r['merchant_name'], len(r['keywords'].split('|'))) for r in rows if len(r['keywords'].split('|')) > 50]
print('keyword 超 50 的商户', len(over), over[:5])
bad = set()
for r in rows:
    for kw in r['keywords'].split('|'):
        if kw != kw.upper() or not set(kw) <= set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 '):
            bad.add((r['merchant_name'], kw))
print('格式违规 keyword', len(bad), list(bad)[:5])
EOF
```

**任何一项非零/非空都要停下来报告**，不要继续。

### 场景 B：新增人工条目

```bash
cd modules/merchant_kb
ls manual_entries/                              # 确认有新的 CSV
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
# 推 raw → finv 必须走 apply_rules 的审批门
python scripts/apply_rules.py --sync_to <finv_path>
```

`sync_rules.py` 是**只拉不推**的。往 finv 推只能走 `apply_rules.py --sync_to`。

## 遗留脚本（默认不要跑）

| 脚本 | 状态 |
|------|------|
| `split_uncategorized.py` | ⚠️ 会先 `rmtree` 重建 `knowledge-base-split/` **再**读 KB。当前 KB 无空 category，跑它只会得到 20 个空 `[]` 分片 |
| `update_category.py` | 配套上述批量流程的写回脚本 |
| `merge_category.py` | 合并分类结果到 KB |

## 常见坑

| 现象 | 原因 |
|------|------|
| `_csv.Error: field larger than field limit` | 没设 `csv.field_size_limit(10_000_000)` |
| keyword 明明写了却不匹配 | 没大写／含 `[A-Z0-9 ]` 以外的字符／是 97 个 STOPWORDS 之一 |
| 改动后 `sync_rules.py` 报「raw 领先」 | 正常 —— 你确实改了 raw。要么走审批推送，要么用 pull 放弃 |
| BOM 消失 | `build_knowledge_base.py` 用 `utf-8` 写。属已知副作用 |
| `git status` 显示 74 MB 的 csv 变更 | `merchant_kb.csv` 在 git 里。**移出 git 的决定已通过但未执行** |

## 相关文档

- `modules/merchant_kb/CLAUDE.md` — 模块级细节（脚本职责、架构）
- `modules/merchant_kb/README.md` — 快速开始 + 相对上游 README 的修正
- 仓库根 `CLAUDE.md` 的 initial_engine 章节 — 引擎侧加载/匹配语义
