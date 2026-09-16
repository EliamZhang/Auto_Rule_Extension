# Auto Rule Extension

基于 Claude Code 的智能规则维护系统，为 [finv_category_V2](https://github.com/EliamZhang/ServiFlow-AI)（上游仓库 `ServiFlow-AI`，本地检出目录名是 `../finv_category_V2`）交易分类流水线的 10 个引擎自动发现并补充分类规则。

## 解决的问题

金融交易分类引擎的规则库仅基于小样本人工提炼。当百万级新交易数据到来时，大量交易模式无法被现有规则覆盖，被标记为 **unclassified**。

本系统通过 **统计层 → 智能层 → 人工审核层** 三层架构，实现规则的半自动化发现、生成、验证和入库，将分类覆盖率从 ~85% 提升至 ~95%+。

## 架构

```
┌─────────────────────────────────────────────────┐
│ 输入：finv_category_V2 流水线处理后的 .xlsx 报告   │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 统计层 (analyze_gaps.py + label_compare.py)      │
│ 发现高频未覆盖模式 + illion vs finv 分类差异质检   │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 智能层 (Claude Code Skill)                       │
│ 解读两份报告 → 逐引擎分析 → 生成候选规则 CSV       │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 验证层 (validate_candidates.py + baseline.py)     │
│ 语法检查 → Schema 校验 → 重叠检测 → 影响面分析     │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 测试层 (test_rules.py)                           │
│ 确认规则在真实数据上的增益/冲突表现                │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 审核层 (人工确认 x2)                              │
│ 规则确认弹窗 → 测试分析报告 → 最终确认弹窗         │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 执行层 (apply_rules.py)                           │
│ 写入 raw/ → 可选同步回 finv_category_V2            │
└─────────────────────────────────────────────────┘
```

## 支持的分类引擎

| 优先级 | 引擎 | 规则文件数 | 职责 |
|--------|------|-----------|------|
| 1 | transfer | 8 | 转账识别（含博彩/放贷配对排除） |
| 10 | initial | 1 | 商户名精确匹配（874,600 商户） |
| 150 | dishonour | 1 | 拒付/退票检测 |
| 180 | gambling | 1 | 赌博/博彩识别（双层：1,822 商户 + 16 通用关键词） |
| 200 | income | 2 | 工资、福利等收入 |
| 300 | liability | 8 | 贷款、信用卡还款 |
| 400 | all_other_credit | 1 | 退款、返现等杂项入账 |
| 500 | fee | 1 | 各类费用识别 |
| 800 | rent | 1 | 房租/租金识别 |
| 999 | catch_all | 1 | 兜底关键词匹配 |

## 项目结构

```
Auto_Rule_Extension/
├── scripts/                   ← 共 13 个 .py（11 在用 + 2 历史遗留，详见 CLAUDE.md）
│   ├── common.py              ← 共享工具模块
│   ├── sync_upstream.py       ← 同步层 1：GitHub → raw/（HTTPS 直取，不经过 finv）
│   ├── sync_rules.py          ← 同步层 2：finv 工作副本 ↔ raw/（只拉不推）
│   ├── analyze_gaps.py        ← 统计层：发现高频未覆盖模式
│   ├── label_compare.py       ← 质检层：illion vs finv 分类差异报告
│   ├── search_merchant.py     ← 工具：搜规则 CSV 的商户/keyword
│   ├── validate_candidates.py ← 验证层：语法+Schema+值校验
│   ├── baseline.py            ← 基线层：save 保存快照 / diff 影响面
│   ├── test_rules.py          ← 测试层：确认规则实际表现
│   ├── apply_rules.py         ← 执行层：写入规则到 raw/
│   └── apply_keyword_updates.py ← 执行层（initial 专用）：追加 keyword 变体到已有商户
├── raw/                       ← 各引擎规则的本地副本
├── modules/                   ← 合并进来的运维模块
│   ├── merchant_kb/           ← ABR XML → merchant_kb.csv 构建流水线
│   ├── assessment/            ← 分类性能报告生成（md/docx/pdf/图表）
│   └── liability_enrich/      ← 放贷商缺口发现 + 候选验证
├── config.json                ← 引擎定义、分析参数、分类关键词
├── CLAUDE.md                  ← Claude Code 项目上下文
├── .claude/skills/            ← Claude Code Skill 定义（见下表）
├── input/                     ← 数据入口（.xlsx，不入库）
├── reviews/                   ← 每次运行产出（不入库）
├── reports/                   ← modules/assessment 的报告产物（不入库）
└── baseline/                  ← 基线快照（不入库）
```

## Skills

| Skill | 用途 |
|-------|------|
| `/auto-rule-extension` | 10 引擎规则发现与补充（主链路） |
| `/liability-enrichment` | 联网核实疑似放贷商 → liability 候选规则 |
| `/merchant-kb-maintenance` | merchant_kb 构建/清洗/合并/校验 |
| `/classify-merchants` | 新商户联网分类（写 `category` 列） |
| `/performance-report` | 出分类性能报告（md/docx/pdf/图表） |

## 快速开始

### 前置条件

```bash
pip install pandas openpyxl
```

### 运行完整流程

```bash
# 0. 规则同步（分析前必做 —— 拿过期规则做分析会得出错误候选）
python scripts/sync_upstream.py            # GitHub → raw/（HTTPS 直取，不经过 finv）
python scripts/sync_rules.py status        # 看 raw/ 与 finv 的漂移
python scripts/sync_rules.py pull          # 有「finv 领先」则拉（自动 .bak 备份）

# 1. 保存基线（在分析之前，保留原始分类快照）
python scripts/baseline.py save \
    --input input/classification_report.xlsx \
    --output baseline/2026-08-07/

# 2. 发现缺口（高频未覆盖模式）
python scripts/analyze_gaps.py \
    --input input/classification_report.xlsx \
    --output reviews/2026-08-07/

# 3. 分类差异质检（illion vs finv 对比）
python scripts/label_compare.py \
    --input input/classification_report.xlsx \
    --output reviews/2026-08-07/label_compare_report.xlsx

# 4. 启动 Claude Code Skill（由 Claude 分析两份报告并生成候选规则）
# /auto-rule-extension

# 5. 验证候选规则
python scripts/validate_candidates.py \
    --review_dir reviews/2026-08-07/

# 6. 影响面分析
python scripts/baseline.py diff \
    --candidates reviews/2026-08-07/ \
    --baseline baseline/2026-08-07/ \
    --input input/classification_report.xlsx

# 7. 确认后测试规则实际表现
python scripts/test_rules.py \
    --rules reviews/2026-08-07/confirmed_rules.json \
    --input input/classification_report.xlsx \
    --output reviews/2026-08-07/test_report.json

# 8. 人工审核后，应用规则
python scripts/apply_rules.py \
    --review_dir reviews/2026-08-07/

# 9. （可选）同步到 finv_category_V2
python scripts/apply_rules.py \
    --review_dir reviews/2026-08-07/ \
    --sync_to ../finv_category_V2/
```

> **注意**：`raw/initial_rule/merchant_kb.csv`（**74 MB / 70.9 MiB**）首次使用时会由
> `sync_upstream.py` 从上游下载。它体积大但**已纳入 Git**（`.git` 目前 **94 MB**），
> 且同时是 `modules/merchant_kb` 的产物 —— 「把 KB 移出 git 并清理历史」是已识别但尚未执行的决定。
>
> 2026-09-15 已把 **53.8 MB** 的 `merchant_kb.csv.keyword_bak`（2026-08-11 旧快照）取消 git 追踪，
> 文件仍在磁盘上；`.gitignore` 补了 `raw/initial_rule/*_bak` 规则（原有 `*.bak` 拦不住 `_bak` 后缀）。

## 设计原则

- **宁可漏判不要误判**：新规则优先保证 precision 而非 recall
- **人工审核是必须环节**：所有规则变更必须经过人工确认
- **只追加 CSV 数据**：不修改 finv_category_V2 的任何引擎逻辑
- **完全独立项目**：自行维护 raw/ 副本，运行时不依赖 finv_category_V2
