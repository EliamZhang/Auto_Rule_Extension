# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 本模块由独立的 `Merchant-Extraction-new` 项目合并而来（见
> `docs/superpowers/specs/2026-09-14-monorepo-merge-design.md` §2）。

## Project overview

Australian merchant knowledge-base data pipeline. Parses ABR (Australian Business Register)
XML bulletins, classifies merchants via web search, and verifies bank transaction counterparties
against the knowledge base.

**最终产出 `raw/initial_rule/merchant_kb.csv` 不是一个普通的模块产物** —— 它是
finv_category_V2 `initial_engine` 实际加载的规则文件，列结构由该引擎决定，本模块无权自由调整。
修改它等于修改线上分类规则，**必须经过人工确认**（见仓库根 CLAUDE.md 的「重要约定」）。

## Key commands

```bash
# 构建知识库（先跑这个；产出直接写 raw/initial_rule/merchant_kb.csv）
python build_knowledge_base.py

# Deduplicate and clean keywords（⚠️ 默认总会 preclean 归一化 keywords 列）
python dedup_keywords.py --full
# ⚠️ 不要用 --changed-since：KB 是 3 列、没有 keyword_updated_at 列，
#    脚本会直接 parser.error 退出（见 dedup_keywords.py:765-769）

# Merge manual entries
python merge_manual_entries.py --add-dir manual_entries/

# Classify KB merchants via DeepSeek (batch mode, no web search)
python label_merchants.py --api-key "$DEEPSEEK_API_KEY"

# Verify bank transaction counterparties (three-tier matching)
# ⚠️ --input 默认是 sample.csv，该文件未随合并进入本仓库 —— 必须显式传 --input
python verify_merchants.py --api-key "$DEEPSEEK_API_KEY" --input <transactions.csv>
python verify_merchants.py --api-key "$DEEPSEEK_API_KEY" --input <transactions.csv> --row-limit 100 --batch-size 5
```

> `settings.FINAL_OUTPUT` 已指向仓库根的 `raw/initial_rule/merchant_kb.csv`，
> **写 KB 的**参数（`--target` / `--merchant-kb`，以及 `dedup_keywords.py --input`）默认已指向它，
> 因此不带路径参数即可。**但读输入的参数不一定**：`verify_merchants.py --input` 默认是
> `sample.csv`（本仓库没有此文件），`merge_category.py --source` 默认是
> `merchant_category_kb.csv`（同样不存在）。命令行里显式传入的相对路径仍相对当前工作目录。

## Merchant classification (primary workflow)

Use the **`/classify-merchants`** skill (仓库根 `.claude/skills/classify-merchants/SKILL.md`) —
a self-contained skill that reads the KB, web-searches each uncategorized merchant, and writes
results back. Tracks searched merchants in `cache/web_classify_tracking.json` so none are
re-processed. It runs from the **仓库根**（它的路径都是相对仓库根的）。

整个模块的流程入口见 **`/merchant-kb-maintenance`** skill（仓库根
`.claude/skills/merchant-kb-maintenance/SKILL.md`，脚本编排 + 校验 + 审批收尾）。

**当前 KB 的空 `category` 记录为 0**（874,600 行已全部有分类），所以这条工作流目前只在
新增商户后才有实际工作量。

## Architecture

```
xml_input/*.xml  →  build_knowledge_base.py  ────────────────────────→  raw/initial_rule/merchant_kb.csv
                       ├── classify-merchants skill (web search, self-contained)   ← 3 列，finv 加载
                       ├── label_merchants.py (DeepSeek batch, no web search)
                       └── verify_merchants.py (three-tier: KB → cache → DeepSeek)
```

`build_knowledge_base.py` **直接写 `FINAL_OUTPUT`**，不产出任何中间 CSV —— 脚本 docstring 明确
"no parsed, filtered, internal, or changelog CSV files are created"。
`data/kb_internal.csv` 是历史设计里的产物，**现在不生成、`data/` 目录也不存在**。

- **`settings.py`** — All configuration: entity-type filters (PRV/PUB), cancel cutoff date,
  stopwords, known abbreviations, keyword length thresholds, payment prefix words, CSV column
  definitions. `FINAL_OUTPUT` / `FINAL_OUTPUT_COLUMNS` 是 3 列契约。
  ⚠️ 另有一批**已废弃/零引用**的常量：`DATA_DIR`、`PARSED_DIR`、`FILTERED_FILE`、`INTERNAL_FILE`、
  `CHANGELOG_FILE`、`BACKUP_DIR`、`MATCH_KEY_LENGTH`、`STATUS_GONE`、`KB_INTERNAL_COLUMNS`
  —— 除 settings.py 自身外全仓无引用（它们注释里提到的 `parse.py`/`filter.py` 在本仓库不存在），
  描述的 `data/kb_internal.csv`（13 列中间产物）也不再生成。**不要照着它们写新代码**。
- **`utils.py`** — Shared helpers: keyword splitting/cleaning, JSON extraction from LLM responses, DeepSeek API client (`post_json`), safe URL validation
- **`build_knowledge_base.py`** — Parses ABR XML, filters to PRV/PUB entities active after 2023,
  merges into the KB (existing rows matched by `find_existing_owner()` —— `normalize_lookup(merchant_name)`
  查已有商户名集合，再查 `keyword_owner` 索引；KB 里**没有** `match_key` 列；未命中则追加新行)
- **`dedup_keywords.py`** — Cleans keyword fields: removes short tokens, stopwords, case-duplicates,
  **generic tokens**（剩不下 `MIN_DISTINCTIVE_KEYWORD_TOKENS` 个非泛化词）、与商户名零 token 重叠的
  keywords —— 共 5 类计数：`removed_len / removed_stopword / removed_dup / removed_generic /
  removed_namemismatch`。⚠️ **默认总会 preclean**（按 `clean_keyword_text` 归一化 keywords 列）：
  不带 `--full` 也会重写全表 keywords 格式，只有 `--no-preclean` 才关闭
- **`merge_manual_entries.py`** — Merges hand-curated `manual_entries/*.csv` into the KB by merchant name
- **`verify_merchants.py`** — Three-tier verification: KB keyword match → API cache → DeepSeek batch. Propagates verified keywords across the CSV. Uses atomic saves (`*.tmp` + `replace`) with `atexit` for crash safety —— **例外**：`write_rows()`（`--output` 结果 CSV 与 checkpoint CSV）是直写（`verify_merchants.py:759-765`），不是原子保存
- **`label_merchants.py`** — Direct DeepSeek batch classification (no web search), uses API cache
- **`merge_category.py`** — 合并分类结果到 KB
- **`split_uncategorized.py`** / **`update_category.py`** — Legacy batch workflow helpers.
  ⚠️ `split_uncategorized.py` 会 `rmtree` 重建 `knowledge-base-split/` 后**才**读 KB；
  当前 KB 无空 category 记录，跑它只会得到 20 个空 `[]` 分片。它带有 argparse 仅为防误触

## 输出契约（⚠️ 改动前必读）

`raw/initial_rule/merchant_kb.csv` 必须满足：

- **3 列**：`merchant_name, keywords, category`（finv 的 `initial_engine/domain/classification.py`
  以 `usecols` 只读这三列，多余列会被静默忽略；历史上曾有 7 列，**2026-08-07 起**——commit
  `29ae8b5`「压缩了kb大小」时该文件就已经是这 3 列。⚠️ 2026-08-27 的 commit `c45ac5a`
  （「优化收入引擎的文本规则，补充赌博商户」）只是给赌博商户补 keyword 变体，**不是 3 列化的起点**，
  两者别混）
- **UTF-8**（当前两侧文件均带 BOM）—— finv 侧按 `utf-8-sig` 读，带不带 BOM 都能读，
  但 BOM 的增删会改变文件字节，被 `sync_rules.py` 判定为一次本地改动。
  注意 `build_knowledge_base.py` 的 `write_merged_kb()` 用 `encoding="utf-8"` 写（**不写 BOM**），
  跑一次就会静默抹掉 BOM
- **`keywords` 用 `|` 分隔**，每个商户最多 50 个变体（引擎加载时截断，超出部分静默丢弃）
- **keyword 必须是大写、仅含 `[A-Z0-9 ]`、压缩空格** —— 引擎加载时**不再**自动 `clean_text()`，
  写错格式即静默失配，无兜底
- **category 不能为 `"Financial Institutions"`**（整行在加载时被丢弃，由 liability/dishonour 处理）
- **单行 field 可达 137 KB**（如 "Australia Post" 的 5,486 个变体），读写 CSV 前必须
  `csv.field_size_limit(10_000_000)`，否则抛 `_csv.Error`

> ⚠️ **已知数据质量问题**：`Australia Post` 有 5,486 个 keyword，但引擎只取前 50 个，
> 其余 5,436 个从未生效。同类超长记录需 `dedup_keywords.py` 清理或拆分。

## 与 finv_category_V2 的同步

本文件同时存在于仓库根的 `raw/initial_rule/`（本地工作副本）和 finv_category_V2
（线上事实源）。**不要手工 cp** —— 用：

```bash
python ../../scripts/sync_rules.py status                  # 看漂移
python ../../scripts/sync_rules.py pull --engine initial --include-large   # finv → raw（自动 .bak）
```

## Data files

- `raw/initial_rule/merchant_kb.csv` — **Main knowledge base, 874,600 rows / 1,338,895 keywords
  （3 列）**。位于仓库根，**已被 git 追踪**（本模块的 `.gitignore` 不作用于该路径）
- ⚠️ `data/kb_internal.csv`（13 列含全部元数据的中间产物）**不存在且不再生成** ——
  `build_knowledge_base.py` 直接写 `FINAL_OUTPUT`，`data/` 目录也不存在。
  同批废弃的还有 `settings.KB_INTERNAL_COLUMNS` 等相关常量（见上方 Architecture）
- ⚠️ `sample.csv` — Bank transaction sample for verification。**未随合并进入本仓库**
  （`verify_merchants.py:28` 的默认输入仍指向它），运行必须显式传 `--input`
- `xml_input/*.xml` — ABR bulletins（**当前不存在**，需另行准备或让脚本自行创建）
- `cache/` — API call caches + `web_classify_tracking.json`。**未入库**（被 `.gitignore` 忽略）
  但**本地存在**：`cache/web_classify_tracking.json` 目前是空脚手架
- `output/` — 脚本输出（`clean_report.csv` 等）。同样**未入库但本地存在**
- `knowledge-base-split/` — 未分类商户 JSON 分片（当前为空分片）
