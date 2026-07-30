## MODIFIED Requirements — 修改的需求

### Requirement: 编排模板的生产级 Worker 集成 — Production Worker integration for orchestration templates
`templates.py` 中的所有图模板（build_three_layer_graph、build_fan_out_pipeline、build_conditional_pipeline、build_reflection_loop）应能被 PregelRuntime 使用生产级 Workers 执行，而非 _TestWorker。

#### Scenario: 使用生产级 Workers 的三层图 — Three-layer graph with production Workers
- **当** `build_three_layer_graph()` 的输出被编译并通过 PregelRuntime 使用生产级 Workers 执行
- **则** guard 节点应调用 LLM 进行安全检查，planner 节点应调用 LLM 进行规划，tool_call 节点应通过 EnginePort 执行真实工具

#### Scenario: 使用生产级 Workers 的扇出管道 — Fan-out pipeline with production Workers
- **当** `build_fan_out_pipeline()` 的输出被编译并通过 PregelRuntime 使用生产级 Workers 执行
- **则** researcher、analyst 和 summarizer 节点应通过 EnginePort.agent_execute() 调用真实的子 agent
