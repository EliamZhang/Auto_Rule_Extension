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

# Deduplicate and clean keywords
python dedup_keywords.py --full
python dedup_keywords.py --changed-since 2026-07-28

# Merge manual entries
python merge_manual_entries.py --add-dir manual_entries/

# Classify KB merchants via DeepSeek (batch mode, no web search)
python label_merchants.py --api-key "$DEEPSEEK_API_KEY"

# Verify bank transaction counterparties (three-tier matching)
python verify_merchants.py --api-key "$DEEPSEEK_API_KEY"
python verify_merchants.py --api-key "$DEEPSEEK_API_KEY" --row-limit 100 --batch-size 5
```

> `settings.FINAL_OUTPUT` 已指向仓库根的 `raw/initial_rule/merchant_kb.csv`，
> 各脚本的 `--input`/`--target`/`--merchant-kb` 默认值**都已指向它**，因此不带路径参数即可。
> 命令行里显式传入的相对路径仍相对当前工作目录。

## Merchant classification (primary workflow)

Use the **`/classify-merchants`** skill (仓库根 `.claude/skills/classify-merchants.md`) —
a self-contained skill that reads the KB, web-searches each uncategorized merchant, and writes
results back. Tracks searched merchants in `cache/web_classify_tracking.json` so none are
re-processed. It runs from the **仓库根**（它的路径都是相对仓库根的）。

整个模块的流程入口见 **`/merchant-kb-maintenance`** skill（脚本编排 + 校验 + 审批收尾）。

**当前 KB 的空 `category` 记录为 0**（874,600 行已全部有分类），所以这条工作流目前只在
新增商户后才有实际工作量。

## Architecture

```
xml_input/*.xml  →  build_knowledge_base.py  →  data/kb_internal.csv  →  raw/initial_rule/merchant_kb.csv
                       ├── classify-merchants skill (web search, self-contained)   ← 3 列，finv 加载
                       ├── label_merchants.py (DeepSeek batch, no web search)
                       └── verify_merchants.py (three-tier: KB → cache → DeepSeek)
```

- **`settings.py`** — All configuration: entity-type filters (PRV/PUB), cancel cutoff date,
  stopwords, known abbreviations, keyword length thresholds, payment prefix words, CSV column
  definitions. `FINAL_OUTPUT` / `FINAL_OUTPUT_COLUMNS` 是 3 列契约；`KB_INTERNAL_COLUMNS` 描述的是
  **中间产物** `data/kb_internal.csv`（13 列），两者不是同一个文件
- **`utils.py`** — Shared helpers: keyword splitting/cleaning, JSON extraction from LLM responses, DeepSeek API client (`post_json`), safe URL validation
- **`build_knowledge_base.py`** — Parses ABR XML, filters to PRV/PUB entities active after 2023, merges into the KB (updates existing by match_key, appends new)
- **`dedup_keywords.py`** — Cleans keyword fields: removes short tokens, stopwords, case-duplicates, keywords with zero token overlap with the merchant name
- **`merge_manual_entries.py`** — Merges hand-curated `manual_entries/*.csv` into the KB by merchant name
- **`verify_merchants.py`** — Three-tier verification: KB keyword match → API cache → DeepSeek batch. Propagates verified keywords across the CSV. Uses atomic saves (`*.tmp` + `replace`) with `atexit` for crash safety
- **`label_merchants.py`** — Direct DeepSeek batch classification (no web search), uses API cache
- **`merge_category.py`** — 合并分类结果到 KB
- **`split_uncategorized.py`** / **`update_category.py`** — Legacy batch workflow helpers.
  ⚠️ `split_uncategorized.py` 会 `rmtree` 重建 `knowledge-base-split/` 后**才**读 KB；
  当前 KB 无空 category 记录，跑它只会得到 20 个空 `[]` 分片。它带有 argparse 仅为防误触

## 输出契约（⚠️ 改动前必读）

`raw/initial_rule/merchant_kb.csv` 必须满足：

- **3 列**：`merchant_name, keywords, category`（finv 的 `initial_engine/domain/classification.py`
  以 `usecols` 只读这三列，多余列会被静默忽略；历史上曾有 7 列，2026-08-27 起重建为 3 列）
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
- `data/kb_internal.csv` — 含全部元数据的中间产物（13 列，未入库）
- `sample.csv` — Bank transaction sample for verification
- `xml_input/*.xml` — ABR bulletins
- `cache/` — API call caches + `web_classify_tracking.json`
- `knowledge-base-split/` — 未分类商户 JSON 分片（当前为空分片）
