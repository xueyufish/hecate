## Why — 为什么

Hecate 的 `EnginePort.agent_execute()` 作为抽象接口存在，并在 `AgentExecutionPort` 中有具体实现，但该实现是一个薄壳：它加载 Agent 的角色和技能，然后直接调用 `llm_service.chat(tools=None)`。这绕过了完整的 LLM 流水线 — 没有工具加载、没有知识库检索、没有守卫钩子（PreLLM/PostLLM）、没有上下文组装、没有令牌预算管理。通过 `agent_execute` 调用的 Agent 与通过 `LLMWorker` 执行的 CONVERSATION 节点相比，体验降级。此外，Agent-as-Tool 能力（`AgentDefinition` + `AgentTool`）已在引擎层完全构建，但缺少 DSL 级别的 `invocation_mode` 开关来从图定义中激活它。

## What Changes — 变更内容

- **升级 `AgentExecutionPort.agent_execute()`** — 加载 Agent 配置的工具、查询知识库、接入 PreLLMHook/PostLLMHook、调用 `context_assemble`、并将工具传递给 LLM。使 Agent 执行与 LLMWorker 的流水线达到同等水平。
- **在 AGENT 节点 DSL 模式中添加 `invocation_mode` 字段** — 支持 `"graph"`（默认，通过 WorkflowExecutionService 的嵌套执行）和 `"tool"`（通过 AgentDefinition 将 Agent 暴露为可调用的工具）模式。
- **在 AgentWorker 中接入 `invocation_mode`** — 从节点配置读取字段，路由到嵌套图执行或 Agent-as-Tool 注册。
- **在 agent_execute 中集成 `AgentDefinition.resolve_tools()`** — 当提供 AgentDefinition 时应用白名单/黑名单过滤。
- **添加 AgentExecutionPort 测试** — 目前 Agent 执行适配器的具体实现测试覆盖率为零。

## Capabilities — 能力

### 新能力

_(无 — 所有能力引用现有规范)_

### 修改的能力

- `agent-invocation`：升级的 agent_execute 流水线（工具、知识库、钩子、上下文组装），AGENT 节点 DSL 中的新 invocation_mode 字段，与 AgentDefinition 的工具过滤集成。

## Impact — 影响

- **修改的文件**：
  - `src/hecate/services/orchestration/agent_execution_port.py` — 重写 agent_execute 以使用完整流水线
  - `src/hecate/engine/graph-dsl.schema.json` — 在 AGENT 节点配置中添加 invocation_mode
  - `src/hecate/engine/workers/agent_worker.py` — 读取 invocation_mode，当 mode=tool 时路由到 AgentTool
  - `src/hecate/engine/compiler.py` — 在编译期间验证 invocation_mode
- **新文件**：
  - `tests/test_services/test_orchestration/test_agent_execution_port.py` — AgentExecutionPort 单元测试
- **无破坏性变更**：invocation_mode 默认为 "graph"（现有行为）。agent_execute 升级是新增的 — 现有调用者自动获得更丰富的行为。
- **无新依赖**：使用现有的 LLMService、KnowledgeBaseService、GuardrailHooks、ContextEngine。
