# Merchant Extraction — 澳大利亚商户知识库数据处理工具集

对澳大利亚商户数据进行解析、过滤、合并、关键词清洗、分类标注和 AI 验证的工具集。

产出 `raw/initial_rule/merchant_kb.csv` —— finv_category_V2 `initial_engine` 实际加载的规则文件。

> ⚠️ **本文件已按本仓库现状修正**。它是 ME 项目 README 的副本，原版对本仓库有多处失真
> （见文末「相对上游 README 的修正」）。**权威现状请看同目录的 `CLAUDE.md`**，
> 本文件只保留脚本级用法参考。

## 快速开始

```bash
cd modules/merchant_kb

# 1. ABR XML → 知识库（写仓库根的 raw/initial_rule/merchant_kb.csv）
python build_knowledge_base.py

# 2. 关键词清洗
python dedup_keywords.py --full
python dedup_keywords.py --changed-since 2026-07-28

# 3. 手工补充合并
python merge_manual_entries.py --add-dir manual_entries/

# 4. AI 分类 / 验证（需要 DEEPSEEK_API_KEY）
python label_merchants.py --api-key "$DEEPSEEK_API_KEY"
python verify_merchants.py --api-key "$DEEPSEEK_API_KEY" --row-limit 100 --batch-size 5
```

大部分脚本的 `--input` / `--target` / `--merchant-kb` 默认值已指向 `FINAL_OUTPUT`
（即仓库根的 `raw/initial_rule/merchant_kb.csv`），**不带参数即可**。
命令行里显式传入的相对路径仍然相对当前工作目录。

## 脚本清单

| 脚本 | 用途 | 备注 |
|------|------|------|
| `build_knowledge_base.py` | ABR XML → KB（官方企业库构建） | 解析 → 过滤 PRV/PUB + 注销日期 → 按 match_key 合并 |
| `dedup_keywords.py` | 关键词清洗去重 | `--full` 全量 / `--changed-since` 增量 / `--report` 出清洗报告 |
| `merge_manual_entries.py` | 手工补充 CSV 合并 | 按商户名匹配，已有补空字段，新的插到文件顶部 |
| `label_merchants.py` | DeepSeek 批量分类（无 web search） | 用 `cache/merchant_category_cache.json` 缓存 |
| `verify_merchants.py` | 交易对手方 AI 验证 | 三层匹配：KB → 缓存 → DeepSeek batch |
| `merge_category.py` | 合并分类结果到 KB | |
| `split_uncategorized.py` / `update_category.py` | 遗留分批工作流 | ⚠️ 见下 |

### ⚠️ 遗留脚本

`split_uncategorized.py` **会先 `rmtree` 重建 `knowledge-base-split/` 再读 KB**，
误触即删目录（它带 argparse 纯粹是为了让 `--help` 不触发执行）。
且当前 KB 的空 `category` 记录为 **0**，跑它只会得到 20 个空分片。

`update_category.py` 读 `knowledge-base-classify/` 下的 JSON 回写 KB —— 该目录当前不存在。

### 已废弃：`classify_batch.py`

**该脚本从不存在**（上游 ME 项目里也没有）。分批分类走 `classify-merchants` skill 即可。

## 数据流

```
xml_input/*.xml  →  build_knowledge_base.py  →  data/kb_internal.csv  →  raw/initial_rule/merchant_kb.csv
                       ├── classify-merchants skill（web search，自包含）    ← 3 列，finv 加载
                       ├── label_merchants.py（DeepSeek 批量，无 web search）
                       └── verify_merchants.py（三层：KB → 缓存 → DeepSeek）
```

KB 分类的推荐方式是 `.claude/skills/classify-merchants.md` —— 自包含 skill，
直接读写 CSV，不需要外部脚本，通过 `cache/web_classify_tracking.json` 追踪已搜索商户。

> 当前 KB 的 874,600 行**全部已有分类**，所以这条工作流只在新增商户后才有实际工作量。

## 输出契约（⚠️ 改动前必读）

`raw/initial_rule/merchant_kb.csv` 必须满足：

- **3 列**：`merchant_name, keywords, category`（`settings.FINAL_OUTPUT_COLUMNS`）
- `keywords` 用 `|` 分隔，每商户最多 50 个变体（引擎加载时截断，超出静默丢弃）
- keyword 必须大写、仅 `[A-Z0-9 ]`、压缩空格 —— 引擎加载时**不再**自动 `clean_text()`
- `category` 不能为 `"Financial Institutions"`（整行在加载时被丢弃）
- 单行 field 可达 137 KB，读写前须 `csv.field_size_limit(10_000_000)`

完整约束（含 BOM、超长记录等已知数据质量问题）见 `CLAUDE.md` 的「输出契约」一节。

## 配置（`settings.py`）

| 配置项 | 说明 |
|--------|------|
| `KEEP_ENTITY_TYPES` | 保留的实体类型（PRV、PUB） |
| `CANCEL_CUTOFF_DATE` | 注销日期阈值（2023-01-01） |
| `MIN_KEYWORD_LEN` | 最短关键词长度 |
| `MIN_DISTINCTIVE_KEYWORD_TOKENS` | 清洗后至少保留的非泛化词 token 数 |
| `KEYWORD_NAME_SIMILARITY_THRESHOLD` | 关键词与商户名 token 重叠率阈值 |
| `STOPWORDS` | 停用词集合（城市名、商业通用词、支付通道词等） |
| `KNOWN_ABBREVIATIONS` | 知名缩写白名单（BP、KFC、ALDI、BWS 等） |
| `PAYMENT_PREFIX_WORDS` | 支付渠道前缀词（APPLE、GOOGLE、PAYPAL、SQUARE 等） |
| `KB_INTERNAL_COLUMNS` | **中间产物** `data/kb_internal.csv` 的列定义（13 列） |
| `FINAL_OUTPUT_COLUMNS` | **最终产出**的列定义（3 列）—— 不是同一个文件 |

路径常量：`BASE_DIR` = 本模块目录，`REPO_ROOT` = 仓库根，`FINAL_OUTPUT` = `REPO_ROOT/raw/initial_rule/merchant_kb.csv`。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 必填 |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | 模型名称 | 见 `settings.py` |
| `DEEPSEEK_THINKING_TYPE` | 思考模式 | `none` |
| `DEEPSEEK_REASONING_EFFORT` | 推理强度 | `none` |

## 数据规模

| 文件 | 规模 | 位置 |
|------|------|------|
| `merchant_kb.csv` | **874,600 行 / 1,338,895 keywords** | 仓库根 `raw/initial_rule/`，**已入 git** |
| `data/kb_internal.csv` | 13 列中间产物 | 本模块，未入库 |
| `sample.csv` | 交易对手方样本 | 本模块，未入库 |

## 注意事项

- 所有写操作使用原子保存（写 `.tmp` 后 `replace`），`atexit` 注册确保中断时也能保存进度
- 缓存文件对成本控制至关重要 —— DeepSeek API 调用不是免费的
- 测试时建议用 `--row-limit` 限制处理量
- **修改 KB 等于修改线上分类规则，必须经过人工确认**（见仓库根 CLAUDE.md 的「重要约定」）

## 本模块目录里没有的东西

以下目录在上游 ME 里是 gitignore 的数据目录，**没有随合并进入本仓库**，
用到时需要另行准备或让脚本自行创建：

`xml_input/`（ABR 报文）、`data/`、`manual_entries/`、`cache/`、`output/`、
`backup/`、`historical_kb/`、`knowledge-base-classify/`、`knowledge-base-web-classify/`

`knowledge-base-split/` 存在但是 20 个空分片（见「遗留脚本」）。
`cbcbcb已经清洗/` 是历史清洗数据，同样 gitignore。

## 相对上游 README 的修正

| 上游 README 说的 | 实际 |
|------------------|------|
| `classify_batch.py` 及整套 `extract/merge/status/next-file` 用法 | **该脚本不存在**，从未存在 |
| 最终输出 6 列（含 `link`、`category_source`、时间戳） | **3 列**；7 列版是 2026-08-27 之前的旧结构 |
| `merchant_kb.csv` ~256 万行 | **874,600 行** |
| `category_source` / `category_updated_at` 会被写回 | 这两列已不存在 |
| `.agents/` 存放自定义 agent 定义 | 目录不存在，也没有 agent 定义 |
| `--input merchant_kb.csv` 等裸文件名 | 默认值已指向仓库根的真实路径，不需要裸文件名 |
| 数据文件在 `.gitignore` 中 | KB 本身**已被 git 追踪**（未入库的只有 `data/`、`cache/` 等中间产物） |

## 与 finv_category_V2 的同步

本模块的产出同时存在于本仓库 `raw/initial_rule/`（本地工作副本）和 finv_category_V2
（线上事实源）。**不要手工 cp**：

```bash
python scripts/sync_rules.py status                                    # 看漂移
python scripts/sync_rules.py pull --engine initial --include-large     # finv → raw（自动 .bak）
```
