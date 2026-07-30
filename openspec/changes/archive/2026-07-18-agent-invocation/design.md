## Context — 背景

Hecate 的 Agent 执行目前有两条路径：

1. **AgentWorker**（引擎层）— 处理 Pregel 运行时中的 AGENT 类型节点。支持两种策略：通过 WorkflowExecutionService 的嵌套图执行（主要），以及通过 `EnginePort.agent_execute()` 的基于端口执行（回退）。

2. **AgentExecutionPort**（服务层）— Agent 执行的具体 EnginePort 适配器。目前是一个薄壳：从数据库加载 AgentModel，将角色 + 技能作为系统提示注入，调用 `llm_service.chat(tools=None)`。

差距：`AgentExecutionPort` 绕过了 `LLMWorker` 提供的完整 LLM 流水线（工具加载、知识检索、守卫钩子、上下文组装、令牌预算管理）。通过 `agent_execute` 调用的 Agent 获得降级的行为。

此外，Agent-as-Tool 能力（`engine/agent_tool.py` 中的 `AgentDefinition` + `AgentTool`）已完全构建，但缺少 DSL 级别的 `invocation_mode` 开关来从图定义中激活它。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 使 AgentExecutionPort 与 LLMWorker 的流水线（工具、知识库、钩子、上下文组装）达到同等水平
- 为 AGENT 节点 DSL 模式添加 `invocation_mode` 字段，用于 Agent-as-Tool 激活
- 在 AgentWorker 中接入 `invocation_mode`，在嵌套图执行和 Agent-as-Tool 之间路由
- 确保 `AgentDefinition.resolve_tools()` 过滤在 agent_execute 中端到端工作
- 为 AgentExecutionPort 添加单元测试

**非目标：**
- agent_execute 的流式支持（spec 返回 dict，而非 AsyncGenerator — 推迟到 P3）
- A2A 远程 Agent 执行（已在 AgentTool.execute_remote 中存在 — 不在范围内）
- Agent Handoff 变更（已在 handoff.py 中完全实现）
- WorkflowExecutionService 嵌套图执行路径的更改

## Decisions — 决策

### 决策 1：原地升级 AgentExecutionPort vs. 新建类

**选择**：原地升级 `AgentExecutionPort.agent_execute()`。

**理由**：该类已存在，已接入 `_ProductionEnginePort`，并具有正确的方法签名。创建新类需要更改适配器工厂和所有调用者。原地升级风险较低。

**考虑的替代方案**：
- 新的 `FullPipelineAgentExecutionPort` — 拒绝：不必要的间接层，相同的 DB 会话和 LLM 服务依赖。
- 内部委托给 LLMWorker — 拒绝：LLMWorker 期望 `WorkerResult`，而非 dict。返回类型契约不同。

### 决策 2：如何访问守卫钩子和上下文引擎

**选择**：在 `AgentExecutionPort.__init__()` 中接受可选的 `pre_hook`、`post_hook` 和 `context_engine` 参数，默认为 NoOp 变体。

**理由**：遵循 `LLMWorker.__init__()` 接受的相同模式，后者接受 `pre_llm_hook` 和 `post_llm_hook`。`_ProductionEnginePort` 工厂将接入实际的钩子。默认为 NoOp 保持向后兼容。

**考虑的替代方案**：
- 通过 `execution_context` 字典传递钩子 — 拒绝：脆弱、类型不安全、将引擎运行时耦合到服务层。
- 全局单例钩子 — 拒绝：违反 DI 原则，使测试更困难。

### 决策 3：invocation_mode 默认值

**选择**：默认 `invocation_mode` 为 `"graph"`（现有行为）。

**理由**：所有现有的 AGENT 节点都使用嵌套图执行。更改默认值会破坏现有图。`"tool"` 模式是可选加入的。

### 决策 4：在 agent_execute 中加载 Agent 工具的位置

**选择**：在 `agent_execute()` 中从 `AgentModel.tool_ids`（或等效）加载工具，然后如果提供了 AgentDefinition，则应用 `AgentDefinition.resolve_tools()` 过滤。

**理由**：Agent 配置的工具是基础集。AgentDefinition 的白名单/黑名单为特定调用缩小工具范围。这与现有的 `AgentTool.resolve_tools()` 设计一致。

### 决策 5：知识库集成范围

**选择**：当 Agent 配置了知识库时，在 agent_execute 中调用 `EnginePort.knowledge_query()`，并将结果作为上下文消息注入。

**理由**：这使 agent_execute 与 CONVERSATION 节点所能做到的达到同等水平。knowledge_query 方法已存在于 EnginePort 上，并在 AgentExecutionPort 中实现。

## Risks / Trade-offs — 风险 / 权衡

**[风险] 延迟增加** — 添加工具、知识库查询和钩子增加了 agent_execute 调用的延迟。→ 缓解：知识库查询并行运行（已实现）。钩子执行速度快（内存检查）。工具加载是单次数据库查询。

**[风险] 循环依赖** — AgentExecutionPort 需要调用 EnginePort 方法（knowledge_query、context_assemble），但其本身是 EnginePort 实现。→ 缓解：AgentExecutionPort 调用自己的方法（self.knowledge_query、self.context_assemble）。无循环导入 — 是同一对象。

**[风险] 破坏现有的 agent_execute 调用者** — 升级流水线可能会改变响应格式。→ 缓解：响应字典键（response、usage、model）不变。额外的键是新增的。无破坏性变更。

**[权衡] 无流式** — AgentExecute 返回 dict，而非流。父 Agent 无法从子 Agent 获取逐令牌输出。→ 可接受：Spec 定义 dict 返回。流式是独立问题（P3）。
