# Auto Rule Extension

基于 Claude Code 的智能规则维护系统，为 [finv_category_V2](https://github.com/EliamZhang/finv_category_V2) 交易分类流水线的 8 个引擎自动发现并补充分类规则。

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
│ 统计层 (analyze_gaps.py)                         │
│ 发现高频未覆盖模式，利用 illion 标签辅助分类        │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 智能层 (Claude Code Skill)                       │
│ 逐引擎分析 gap → 生成候选规则 CSV                  │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 验证层 (validate_candidates.py + baseline.py)     │
│ 语法检查 → Schema 校验 → 重叠检测 → 影响面分析     │
└──────────────────┬──────────────────────────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│ 审核层 (人工确认)                                  │
│ 逐引擎审核候选规则 → 确认/拒绝/修改                 │
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
| 1 | initial | 1 | 商户名精确匹配 (~9,000 条) |
| 100 | transfer | 4 | 转账识别 + 赌博排除 |
| 150 | dishonour | 1 | 拒付/退票检测 |
| 200 | income | 1 | 工资、福利等收入 |
| 300 | liability | 7 | 贷款、信用卡还款 |
| 400 | all_other_credit | 1 | 退款、返现等杂项入账 |
| 500 | fee | 1 | 各类费用识别 |
| 999 | catch_all | 1 | 兜底关键词匹配 |

## 项目结构

```
Auto_Rule_Extension/
├── scripts/
│   ├── common.py              ← 共享工具模块
│   ├── analyze_gaps.py        ← 统计层：发现高频未覆盖模式
│   ├── validate_candidates.py ← 验证层：语法+Schema+值校验
│   ├── baseline.py            ← 基线层：save 保存快照 / diff 影响面
│   └── apply_rules.py         ← 执行层：写入规则到 raw/
├── raw/                       ← 各引擎规则的本地副本
├── config.json                ← 引擎定义、分析参数、分类关键词
├── CLAUDE.md                  ← Claude Code 项目上下文
├── SKILL.md                   ← Claude Code Skill 工作流定义
├── input/                     ← 数据入口（.xlsx，不入库）
├── reviews/                   ← 每次运行产出（不入库）
└── baseline/                  ← 基线快照（不入库）
```

## 快速开始

### 前置条件

```bash
pip install pandas openpyxl
```

### 运行完整流程

```bash
# 1. 发现缺口
python scripts/analyze_gaps.py \
    --input input/classification_report.xlsx \
    --output reviews/2026-08-07/

# 2. 保存基线
python scripts/baseline.py save \
    --input input/classification_report.xlsx \
    --output baseline/2026-08-07/

# 3. 启动 Claude Code Skill（由 Claude 分析 gap 并生成候选规则）
# /auto-rule-extension

# 4. 验证候选规则
python scripts/validate_candidates.py \
    --review_dir reviews/2026-08-07/

# 5. 影响面分析
python scripts/baseline.py diff \
    --candidates reviews/2026-08-07/ \
    --baseline baseline/2026-08-07/ \
    --input input/classification_report.xlsx

# 6. 人工审核后，应用规则
python scripts/apply_rules.py \
    --review_dir reviews/2026-08-07/

# 7. （可选）同步到 finv_category_V2
python scripts/apply_rules.py \
    --review_dir reviews/2026-08-07/ \
    --sync_to ../finv_category_V2/
```

## 设计原则

- **宁可漏判不要误判**：新规则优先保证 precision 而非 recall
- **人工审核是必须环节**：所有规则变更必须经过人工确认
- **只追加 CSV 数据**：不修改 finv_category_V2 的任何引擎逻辑
- **完全独立项目**：自行维护 raw/ 副本，运行时不依赖 finv_category_V2
