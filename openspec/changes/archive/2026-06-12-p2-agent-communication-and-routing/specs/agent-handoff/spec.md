## ADDED Requirements — 新增需求

### Requirement: Dynamic handoff edge type — 动态 Handoff 边类型
系统应支持 Graph DSL 中的 `"dynamic_handoff"` 边触发器。当 handoff 边具有 `trigger: "dynamic_handoff"` 时，`handoff_to_agent` 工具应被注入多个候选目标。LLM 在运行时决定移交到哪个目标。

#### Scenario: Dynamic handoff tool injected with multiple targets — 动态 Handoff 工具注入多个目标
- **当** 图谱 DSL 包含一条从 agent 节点 "triage" 出发的边，带有 `trigger: "dynamic_handoff"` 和目标字典 `{"billing": "billing_agent", "tech": "tech_agent"}`
- **则** 系统将 `handoff_to_agent` 工具注入到 "triage" agent 的工具列表中，`target` 参数接受值 `["billing_agent", "tech_agent"]`

#### Scenario: Dynamic handoff LLM selects target — 动态 Handoff LLM 选择目标
- **当** LLM 在动态 handoff 边上调用 `handoff_to_agent(target="tech_agent")`
- **则** worker 返回 `Command(goto="tech_agent")`，Pregel 运行时在下一个超步中执行 "tech_agent" 节点

#### Scenario: Dynamic handoff invalid target rejected — 动态 Handoff 无效目标被拒绝
- **当** LLM 调用 `handoff_to_agent(target="unknown_agent")` 且 "unknown_agent" 不在允许的目标列表中
- **则** worker 应向 LLM 返回错误响应，指示目标无效，提示重试

#### Scenario: Dynamic handoff cycle detection — 动态 Handoff 循环检测
- **当** 编译器处理 `dynamic_handoff` 边
- **则** 应应用与常规 handoff 边相同的循环检测逻辑

#### Scenario: Dynamic handoff edge parsed from DSL — 从 DSL 解析动态 Handoff 边
- **当** `parse_graph()` 收到一条边，内容为 `{"source": "router", "target": {"billing": "billing_agent", "tech": "tech_agent"}, "trigger": "dynamic_handoff"}`
- **则** 结果 `Edge` 应具有 `trigger="dynamic_handoff"` 和 `target={"billing": "billing_agent", "tech": "tech_agent"}`
