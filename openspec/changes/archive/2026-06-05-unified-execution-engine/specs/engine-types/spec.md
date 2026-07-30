## MODIFIED Requirements — 修改的需求

### Requirement: NodeType 枚举定义了 6 种执行行为 — NodeType enum defines 6 execution behaviors
`NodeType` 枚举应定义：CONVERSATION、TOOL_CALL、CONDITION、AGENT、KNOWLEDGE_RETRIEVAL、VARIABLE_SET、SUGGESTION、FAN_OUT、MERGE。

#### Scenario：会话节点 — Conversation node
- **当** 节点的类型为 CONVERSATION
- **则** Worker 应调用 LLM，包含上下文组装、记忆和 provider 塑造

#### Scenario：条件节点 — Condition node
- **当** 节点的类型为 CONDITION
- **则** Worker 应针对通道状态评估表达式以确定路由

#### Scenario：Agent 节点 — Agent node
- **当** 节点的类型为 AGENT
- **则** Worker 应将执行委派给代表另一个 agent 的子图

#### Scenario：建议节点 — Suggestion node
- **当** 节点的类型为 SUGGESTION
- **则** Worker 应生成开场白或跟进建议

#### Scenario：扇出节点 — Fan-out node
- **当** 节点的类型为 FAN_OUT
- **则** 运行时应在不调用 FAN_OUT 节点本身的 Worker 的情况下，并发地调度所有分支节点

#### Scenario：合并节点 — Merge node
- **当** 节点的类型为 MERGE
- **则** Worker 应从所有扇出分支子通道收集结果并产生聚合输出
