fee_engine 规则说明
===================

fee_engine 的规则从 fee_classification_rules.csv 动态加载，不再硬编码在 Python 源码中。

CSV Schema:
  priority, rule_name, category, pattern, counterparty, match_type, zero_amount_reject, description

关键约束:
  - priority: 升序排列，数字越小优先级越高（先匹配先得）
  - category: "fee"（自动转为 "Fees"）或 "Overdrawn"
  - pattern: ^ 锚定的 regex，大小写敏感（不使用 re.IGNORECASE）
  - zero_amount_reject: true 表示金额为 $0.00 时撤销该规则匹配
  - 添加新规则直接追加 CSV 即可，无需修改 Python 源码
