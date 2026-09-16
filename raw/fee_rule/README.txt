fee_engine 规则说明
===================

fee_engine 的规则从 fee_classification_rules.csv 动态加载，不再硬编码在 Python 源码中。

CSV Schema:
  priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description, unclassified_only, dr_cr

关键约束:
  - priority: 升序排列，数字越小优先级越高（先匹配先得）
  - category: "fee"（自动转为 "Fees"）或 "Overdrawn"
  - pattern: ^ 锚定的 regex，**大小写不敏感**
      （2026-08-20 起引擎用 re.compile(pattern, re.IGNORECASE)；此前为大小写敏感）
  - zero_amount_reject: true 表示金额为 $0.00 时撤销该规则匹配
  - unclassified_only: true 表示只处理尚未被其他引擎分类的行
  - dr_cr: "credit" 或 "debit" 时只在对应方向生效；留空匹配所有方向
      （2026-09-15 实测：109 条空 + 4 条 debit，**没有 credit 规则**）
  - 添加新规则直接追加 CSV 即可，无需修改 Python 源码
