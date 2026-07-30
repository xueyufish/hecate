## MODIFIED Requirements — 修改的需求

### Requirement: NodeType 枚举定义 6 种执行行为 — NodeType 枚举定义 6 种执行行为
`NodeType` 枚举 SHALL 定义：CONVERSATION、TOOL_CALL、CONDITION、AGENT、KNOWLEDGE_RETRIEVAL、VARIABLE_SET、FAN_OUT、MERGE。

#### Scenario: 对话节点
- **WHEN** 节点的类型为 CONVERSATION
- **THEN** worker SHALL 使用当前通道状态调用 LLM

#### Scenario: 条件节点
- **WHEN** 节点的类型为 CONDITION
- **THEN** worker SHALL 根据通道状态求值表达式以确定路由

#### Scenario: 代理节点
- **WHEN** 节点的类型为 AGENT
- **THEN** worker SHALL 将执行委托给表示另一个代理的子图

#### Scenario: 扇出节点
- **WHEN** 节点的类型为 FAN_OUT
- **THEN** 运行时 SHALL 并发分发所有分支节点，而不在 FAN_OUT 节点本身上调用 worker

#### Scenario: 合并节点
- **WHEN** 节点的类型为 MERGE
- **THEN** worker SHALL 从所有扇出分支子通道收集结果并生成聚合输出