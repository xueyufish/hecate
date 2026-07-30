## Why — 为什么

Hecate 当前的多智能体支持涵盖静态图模式（顺序、扇出、条件、广播、反射）和基本的智能体调用/交接。然而，智能体无法**在运行时动态通信**、**协商结果**、**将任务分配给最合适的智能体**，或**将其他智能体作为受控权限的工具调用**。这些能力对于 P2 的多智能体编排目标至关重要，并且是 P3 的分布式团队编排（13.15）和 A2A 协议（2.10）的先决条件。

对 11+ 平台（AutoGen、LangGraph Swarm、CrewAI、JiuwenSwarm、Deer-flow、Claude Code、Google A2A、AgentScope、Coze）的研究确认，这四个能力是标准的多智能体协作原语。

## What Changes — 变更内容

- **EventBus（2.3a）**：新的 `EventBus` ABC，包含 `publish`/`subscribe`/`unsubscribe` 方法，用于在同一会话内智能体之间的实时发布/订阅消息传递。使用 `asyncio.Queue` 的 `InMemoryEventBus` 实现。新的 `CollaborationEvent` 类型扩展了现有的 `EventType` 枚举，包含智能体特定事件（AGENT_MESSAGE、AGENT_REQUEST、AGENT_RESPONSE、TASK_ASSIGNED、TASK_COMPLETED、NEGOTIATION_PROPOSAL、NEGOTIATION_ACCEPT、NEGOTIATION_REJECT）。与 PregelRuntime 的 `execution_context` 集成。

- **协商模板（2.3b）**：新的图模板工厂函数（`build_negotiation_graph`、`build_debate_graph`），遵循 `engine/templates.py` 中的现有模板模式。协商使用 EventBus 在图超步循环内进行智能体间消息传递。模板生成与 `GraphCompiler` 兼容的标准 `GraphConfig` 实例。

- **TaskAllocator（2.3c）**：新的 `TaskAllocator` ABC，包含 `SemanticTaskAllocator` 实现，使用基于 LLM 的语义匹配从候选池中选择最合适的智能体。接口为 P3 动态智能体创建（JiuwenSwarm `spawn_member` 模式）预留了 `create_if_not_found` 标志。

- **Agent-as-Tool（2.3d）**：新的 `AgentDefinition` dataclass，指定每次调用的智能体配置（遵循 Deer-flow 的 `SubagentConfig` 模式的双轨工具白名单+黑名单、模型覆盖、上下文隔离模式、最大轮次、超时）。新的 `AgentTool` 类将 `AgentDefinition` 包装为可调用工具，通过 `EnginePort.agent_execute()` 与现有工具执行基础设施集成。

## Capabilities — 能力

### New Capabilities — 新能力
- `event-bus`：用于会话内智能体间通信的实时发布/订阅事件总线——EventBus ABC、InMemoryEventBus、CollaborationEvent 类型、PregelRuntime 集成
- `negotiation-templates`：用于多智能体协商和辩论模式的图模板——build_negotiation_graph、build_debate_graph 工厂函数
- `task-allocator`：带基于 LLM 的语义匹配的抽象任务分配——TaskAllocator ABC、SemanticTaskAllocator 实现
- `agent-tool`：带受控权限的 Agent-as-Tool 能力——AgentDefinition dataclass、AgentTool 类、双轨工具白名单/黑名单控制

### Modified Capabilities — 修改的能力
- `engine-types`：添加 COLLABORATION_EVENT 字符串枚举值，以支持 EventBus 特定事件类型与现有 EventType 并存
- `engine-ports`：扩展 `agent_execute()` 合约以接受可选的 `AgentDefinition` 覆盖参数（工具过滤器、上下文模式、模型覆盖）
- `agent-invocation`：扩展 AGENT 节点配置以支持带基于 AgentDefinition 的权限范围限定的 `invocation_mode: "tool"`（完善现有规约）

## Impact — 影响

- **引擎层**：新文件 `engine/eventbus.py`、`engine/negotiation.py`、`engine/task_allocator.py`、`engine/agent_tool.py`。修改 `engine/types.py`（新事件类型）、`engine/pregel.py`（execution_context 中的 EventBus 集成）、`engine/templates.py`（新模板导出）。
- **服务层**：服务适配器更新以支持带有 AgentDefinition 覆盖的 `agent_execute()`。
- **API 层**：无变更——所有能力都是通过现有执行路径暴露的引擎层原语。
- **依赖**：无新的外部依赖。基于 LLM 的语义匹配通过 EnginePort 重用现有的 `llm_invoke()`。
- **P3 演进路径**：EventBus ABC 支持未来的 `RedisEventBus` 实现。TaskAllocator ABC 为动态智能体创建预留了 `create_if_not_found`。两者都在 design.md 中记录为 P3 扩展点。
