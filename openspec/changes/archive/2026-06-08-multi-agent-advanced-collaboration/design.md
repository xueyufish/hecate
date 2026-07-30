## Context — 上下文

Hecate 的引擎层通过 PregelRuntime（BSP 执行循环）、用于状态的 ChannelManager、基于模板的图构建和 WorkerPool 分发提供基于图的多智能体编排。当前的多智能体支持包括：

- **AgentWorker**（`engine/workers/agent_worker.py`）：通过嵌套图执行来执行 AGENT 类型节点，委托给 `execution_service` 或 `port.agent_execute()`。
- **智能体调用**（`agent-invocation` 规约）：`EnginePort.agent_execute()` 用于带上下文隔离的子智能体执行。
- **智能体交接**（`agent-handoff` 规约）：`handoff_to_agent` 工具注入和 `Command(goto=)` 用于控制转移。
- **模板**（`engine/templates.py`）：`build_chat_graph`、`build_three_layer_graph`、`build_fan_out_pipeline`、`build_conditional_pipeline`、`build_reflection_loop`、`build_sequential_pipeline`、`build_broadcast_pipeline`。
- **EventStore**（`engine/eventstore.py`）：带 11 种事件类型的仅追加审计日志，与 PregelRuntime 集成。

缺少的：实时智能体间消息传递、运行时协商、智能任务分配和受控的智能体即工具调用。这些是 P2 多智能体编排和 P3 分布式团队编排（13.15）所需的四个原语。

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**
- 提供一个 EventBus ABC，用于图执行期间会话范围的发布/订阅消息传递
- 添加遵循现有模板约定的协商和辩论图模板
- 实现带基于 LLM 的语义匹配的 TaskAllocator ABC，用于最合适的智能体选择
- 创建一个 AgentTool，将智能体包装为可调用工具，带每次调用的权限控制
- 确保所有 P2 实现使用内存数据结构，无外部依赖
- 为 P3 演进预留 ABC 接口（RedisEventBus、动态智能体创建）

**Non-Goals — 非目标：**
- 跨会话或跨进程 EventBus（P3——功能 13.15）
- 分布式智能体发现或注册（P3——功能 13.15）
- 运行时动态智能体创建（P3——`create_if_not_found=True`）
- A2A 协议合规（P3——功能 2.10）
- 协商/任务分配配置的 UI 变更
- 对现有 EventStore 的变更（仅追加审计日志保持不变）

## Decisions — 决策

### D1: EventBus 与 EventStore 并行，而非扩展它

**Decision — 决策**：创建与现有 EventStore 并行的独立 `EventBus` ABC。EventStore 保持为用于可观测性的仅追加审计日志。EventBus 是用于智能体协调的实时发布/订阅。

**Rationale — 理由**：EventStore 的语义（追加、获取事件、重放、版本化）与发布/订阅（发布、订阅、取消订阅、过滤）根本不同。组合它们会违反单一职责并使两者复杂化。JiuwenSwarm 遵循相同的分离。

**Alternative considered — 考虑的替代方案**：用 subscribe() 扩展 EventStore——被拒绝，因为 EventStore 是设计上仅追加的，订阅者需要轮询。

### D2: EventBus 通过 PregelRuntime execution_context 集成

**Decision — 决策**：向 PregelRuntime 的构造函数添加 `event_bus: EventBus | None = None`。通过 `execution_context` 字典将其传递给工作器，与现有的 `event_store` 一起。

**Rationale — 理由**：这遵循了已建立的 EventStore 集成模式。需要发布/订阅的工作器（例如，协商期间的 AgentWorker）通过 `execution_context["event_bus"]` 访问 EventBus。无需更改 Worker ABC 签名。

### D3: 协商模板生成标准 GraphConfig

**Decision — 决策**：`build_negotiation_graph()` 和 `build_debate_graph()` 返回 `GraphConfig` 实例，与所有其他模板函数相同。它们使用带 EventBus 感知通道配置的标准 AGENT 节点。

**Rationale — 理由**：GraphConfig → GraphCompiler → PregelRuntime 是已建立的管道。协商图只是专门的图拓扑——无需特殊的运行时支持。图本身编码了协商协议（轮次结构、终止条件、消息路由）。

### D4: TaskAllocator 使用 LLM 语义匹配，而非嵌入相似度

**Decision — 决策**：`SemanticTaskAllocator` 调用 `port.llm_invoke()` 来分析任务描述与候选智能体描述的匹配，生成评分排名。无嵌入模型依赖。

**Rationale — 理由**：添加嵌入模型（例如，通过 sentence-transformers）会引入新的 ML 依赖并需要模型管理。基于 LLM 的匹配重用现有的 `llm_invoke()` 端口，并提供更丰富的语义理解。AutoGen 的 SelectorGroupChat 使用相同的方法。

**Alternative considered — 考虑的替代方案**：嵌入余弦相似度——由于新依赖和对短智能体描述的质量较低而被拒绝。

### D5: Agent-as-Tool 使用双轨白名单+黑名单（Deer-flow 模式）

**Decision — 决策**：`AgentDefinition` 指定 `tools: list[str] | None`（白名单，None=继承全部）和 `disallowed_tools: list[str]`（黑名单，默认排除 `["agent_execute"]` 以防止嵌套）。

**Rationale — 理由**：Deer-flow（字节跳动）在生产中验证了此模式。仅白名单（Claude Code）对于不同调用者需要不同权限的企业场景过于僵化。仅黑名单（调研的平台中没有一个采用）不足以实现精确控制。双轨机制同时处理"仅这些工具"和"除这些外的所有工具"场景。

**Resolution order — 解析顺序**：如果 `tools` 不为 None → 使用白名单减去黑名单。如果 `tools` 为 None → 继承全部减去黑名单。

### D6: AgentDefinition 是每次调用的，而非每个 AgentModel 的

**Decision — 决策**：`AgentDefinition` 在工具调用时传递，而非存储在 `AgentModel` 上。同一个智能体可以被不同的调用者以不同的权限集调用。

**Rationale — 理由**：在多智能体图中，智能体 A 可能需要用只读工具调用"研究员"，而智能体 B 用完全工具访问调用同一个"研究员"。每个 AgentModel 的定义将是全局的和僵化的。

### D7: context_mode 支持"inherited"和"isolated"

**Decision — 决策**：`AgentDefinition.context_mode` 字段：`"inherited"`（默认）与子智能体共享父级的消息通道；`"isolated"` 为子智能体创建新的消息上下文。

**Rationale — 理由**：Claude Code 默认使用上下文隔离（子智能体从空白开始）。对于 Hecate 的基于图的执行，继承上下文更常见（子智能体需要对话历史），但对于只应看到特定任务而非完整对话的专家智能体，隔离模式至关重要。

## Risks / Trade-offs — 风险 / 权衡

**[TaskAllocator 的 LLM 成本]** → SemanticTaskAllocator 每次分配都调用 LLM，增加了延迟和成本。缓解措施：在会话内缓存相同任务描述的分配结果；允许对成本敏感的部署回退到轮询分配器。

**[EventBus 内存使用]** → InMemoryEventBus 将所有发布的事件存储在内存中，直到订阅者消费它们。缓解措施：每个主题队列大小限制，采用最旧丢弃策略；事件是会话范围的，在会话结束时被 GC。

**[协商图复杂性]** → 协商模板生成可能运行许多超步的多轮图。缓解措施：模板构建器中的可配置 max_rounds 参数；PregelRuntime 现有的 max_supersteps 保护。

**[Agent-as-Tool 递归]** → 智能体 A 将智能体 B 作为工具调用，智能体 B 将智能体 A 作为工具调用。缓解措施：默认 `disallowed_tools=["agent_execute"]` 防止工具级别嵌套；图编译器的循环检测防止交接级别嵌套。

**[EventBus + EventStore 混淆]** → 两个事件系统可能使开发人员困惑。缓解措施：清晰的命名（EventBus 用于实时协调，EventStore 用于审计跟踪）；EventBus 事件默认不持久化。
