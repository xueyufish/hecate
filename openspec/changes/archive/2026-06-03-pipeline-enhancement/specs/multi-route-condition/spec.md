## ADDED Requirements — 新增需求

### Requirement: 多键条件路由 — 多键条件路由
CONDITION 节点 SHALL 支持路由到超过两个分支，允许边目标字典包含超出 "true" 和 "false" 的任意字符串键。由 CONDITION 节点表达式求值写入的 `_route` 值 SHALL 用作边目标字典中的查找键。

#### Scenario: 三个分支的多键路由
- **WHEN** CONDITION 节点求值表达式并写入 `_route: "high"`
- **AND** 出向边目标为 `{"high": "priority_handler", "medium": "standard_handler", "low": "batch_handler"}`
- **THEN** 执行 SHALL 路由到 "priority_handler"

#### Scenario: 回退到默认键
- **WHEN** CONDITION 节点求值表达式并写入 `_route: "unknown"`
- **AND** 边目标字典有 "default" 键
- **THEN** 执行 SHALL 路由到 "default" 键指定的节点

#### Scenario: 与 true/false 路由向后兼容
- **WHEN** CONDITION 节点求值表达式并写入 `_route: "true"`
- **AND** 边目标为 `{"true": "node_a", "false": "node_b"}`
- **THEN** 执行 SHALL 路由到 "node_a"——与当前行为相同

#### Scenario: 保留旧版 false 回退
- **WHEN** CONDITION 节点求值表达式并写入 `_route: "unknown"`
- **AND** 边目标字典没有 "default" 键但有 "false" 键
- **THEN** 执行 SHALL 回退到 "false" 键以保持向后兼容

### Requirement: 条件表达式求值产生路由键 — 条件表达式求值产生路由键
CONDITION 节点的 `expression` 配置字段 SHALL 支持比较表达式，产生字符串路由键而不仅仅是布尔值。支持的表达式 SHALL 包括：相等（`field == value`）、大于（`field > threshold`）、小于（`field < threshold`）和直接字段引用。

#### Scenario: 相等表达式产生路由键
- **WHEN** CONDITION 节点的表达式为 `category` 且 "category" 的通道值为 "finance"
- **THEN** `_route` 值 SHALL 为 "finance"

#### Scenario: 阈值表达式产生路由键
- **WHEN** CONDITION 节点的表达式为 `score > 80 ? "high" : "low"` 且 "score" 的通道值为 90
- **THEN** `_route` 值 SHALL 为 "high"