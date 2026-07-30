## Context — 上下文

Hecate 的引擎层提供了多智能体编排所需的所有原语：TOPIC 通道（追加-归约消息累积）、LAST_VALUE 通道（单值状态）、PregelRuntime 中的顺序边解析（线性图每超步一个节点）、FAN_OUT/MERGE 用于并行分发，以及 AgentWorker 用于智能体节点。

`engine/templates.py` 中存在五个工厂函数：`build_chat_graph()`、`build_three_layer_graph()`、`build_fan_out_pipeline()`、`build_conditional_pipeline()`、`build_reflection_loop()`。每个都构造一个具有手动接线通道（每个节点的可读/可写通道）、边和状态声明的 `GraphConfig`。

`data/orchestration_templates/` 中存在六个 JSON 模板：chat、three-layer、fan-out、conditional、reflection 和 content-pipeline。content-pipeline 模板（`content-pipeline.json`）演示了带有手动通道接线的顺序 researcher→writer→reviewer 模式，但这是一个单一的硬编码用例。

差距：想要通用顺序管道或广播模式的开发人员必须理解通道接线语义并手动构造 Graph DSL 字典。这是一个 DX 问题，而非引擎能力差距。

## Goals / Non-Goals — 目标 / 非目标

**Goals — 目标：**
- 提供 `build_sequential_pipeline(stages=[...])`，自动为线性 A→B→C→D 工作流接线通道
- 提供 `build_broadcast_pipeline(participants=[...])`，创建共享通道轮询图
- 将两种模式的 JSON 模板添加到编排模板目录
- 严格遵循现有工厂函数约定（返回 `GraphConfig`，使用相同的参数模式）

**Non-Goals — 非目标：**
- 新的引擎原语——所有需要的原语（TOPIC、LAST_VALUE、AGENT 节点、CONDITION 节点、顺序边解析）已存在
- 编译器变更——无新的验证规则或编译路径
- 类型化的阶段输入/输出合约（例如，用于阶段间数据的 Pydantic 模型）——未来增强
- 运行时动态管道构造——工厂生成静态的 GraphConfig
- 流式支持变更——PregelRuntime 流式按原样工作

## Decisions — 决策

### D1: 顺序管道使用专用的每阶段 LAST_VALUE 通道

**Decision — 决策**：每个阶段获得一个 `{stage_id}_output` LAST_VALUE 通道。阶段 N 写入 `{stage_id}_output`，阶段 N+1 从 `{stage_id}_output`（前一阶段的输出通道）读取。共享的 `messages` TOPIC 通道累积所有智能体交互。

**Alternatives considered — 考虑的替代方案**：
- 用于阶段间数据的单个共享 LAST_VALUE 通道→混淆输出，阶段 N+2 无法在不解析消息的情况下引用阶段 N 的输出
- 无阶段间通道，仅依赖 messages TOPIC→智能体必须解析消息历史以提取结构化数据

**Rationale — 理由**：匹配 `content-pipeline.json` 模式（research_data、draft、review_status）和 `build_reflection_loop()` 模式（draft、quality_status）。显式的每阶段通道使数据流可见且可调试。

### D2: 广播管道使用顺序轮询，非并发分发

**Decision — 决策**：广播参与者按固定顺序顺序执行，所有参与者共享同一个 `messages` TOPIC 通道。每个参与者看到所有先前的消息（来自 TOPIC 的追加-归约行为）并追加自己的响应。

**Alternatives considered — 考虑的替代方案**：
- 并发执行（所有智能体在同一超步）→智能体在同一步骤内无法看到彼此的响应；需要 MERGE 语义；失去对话式"讨论"质量
- 具有实时消息可见性的真正发布/订阅→需要新的引擎原语用于超步内的消息广播

**Rationale — 理由**：匹配 AutoGen 的 `RoundRobinGroupChat` 和 AgentScope 的 `MsgHub` 带顺序执行。共享的 TOPIC 通道自然地累积所有消息。这是实际多智能体讨论中最有用的广播模式。

### D3: 顺序管道中修订循环可选

**Decision — 决策**：`build_sequential_pipeline()` 接受可选的 `revision_config` 参数。当提供时，会在最后阶段后追加 CONDITION 节点和修订循环。当省略时，管道严格线性。

**Rationale — 理由**：`content-pipeline.json` 模板显示了一个修订循环（reviewer → check_revision → writer），但许多管道是纯线性的（ETL 风格）。使其可选覆盖了两种情况，无需单独的工厂函数。

### D4: 广播支持通过 max_supersteps 配置轮次限制

**Decision — 决策**：广播工厂使用映射到 PregelRuntime 的 `max_supersteps` 参数的 `max_turns` 值设置 `GraphConfig` 元数据。不需要新的终止条件原语。

**Rationale — 理由**：PregelRuntime 已有 `max_supersteps` 作为安全限制。对于广播轮询，轮次数 = 参与者数量 × 轮数。调用者可以通过运行时而非图配置来配置它。

### D5: 工厂函数签名遵循现有模式

**Decision — 决策**：使用 `TypedDict` 进行阶段/参与者定义，而非 Pydantic 模型，匹配现有的 `NodeConfig.config` 字典模式。每个阶段/参与者是一个普通的 dict，包含 `id`、`model`、`system_prompt` 键。

**Rationale — 理由**：与现有工厂保持一致。引擎层没有新的 Pydantic 依赖。`NodeConfig.config` 字段已经是 `dict[str, Any]`。

## Risks / Trade-offs — 风险 / 权衡

- **[通用管道可能不适用于所有用例]** → 缓解措施：工厂生成标准的 `GraphConfig`，用户可以在构造后修改。不是锁定抽象。
- **[长管道的每阶段通道造成通道激增]** → 缓解措施：对于典型的 3-7 阶段管道是可接受的。如果将来需要，"紧凑"模式可以使用单个 `pipeline_state` 字典通道。
- **[广播轮询假设固定顺序]** → 缓解措施：匹配 AutoGen/CrewAI 约定。动态排序（例如，LLM 选择的发言者）是一个单独的功能。
- **[阶段间无类型化数据合约]** → 缓解措施：有意不在此范围。阶段通过通道通信，而非类型化接口。以后可以作为单独的增强添加 Pydantic 合约。
