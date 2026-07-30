## 1. AgentExecutionPort Upgrade — AgentExecutionPort 升级

- [x] 1.1 向 `AgentExecutionPort.__init__()` 添加 `pre_hook`、`post_hook`、`context_engine` 可选参数，默认 NoOp
- [x] 1.2 在 `agent_execute()` 中从 AgentModel 加载 Agent 配置的工具（通过 agent 的 tool_ids 查询 ToolModel）
- [x] 1.3 通过 `self.knowledge_query()` 查询 Agent 的知识库，并将结果作为上下文消息注入
- [x] 1.4 当提供 agent_definition 时，应用 `AgentDefinition.resolve_tools()` 过滤（白名单/黑名单）
- [x] 1.5 在 `agent_execute()` 中的 LLM 调用周围接入 PreLLMHook 和 PostLLMHook
- [x] 1.6 在 LLM 调用前调用 `self.context_assemble()` 以应用上下文工程流水线
- [x] 1.7 将组装好的工具传递给 `llm_service.chat()`（当前硬编码为 `tools=None`）

## 2. DSL Schema & Compiler — DSL 模式与编译器

- [x] 2.1 在 `graph-dsl.schema.json` 的 AGENT 节点定义中添加 `invocation_mode` 字段（枚举："graph"、"tool"，默认 "graph"）
- [x] 2.2 在 `GraphCompiler` 中验证 `invocation_mode` 值 — 拒绝无效值并显示清晰的错误信息

## 3. AgentWorker Routing — AgentWorker 路由

- [x] 3.1 在 `AgentWorker.execute()` 中从 node_config 读取 `invocation_mode`
- [x] 3.2 当 `invocation_mode == "tool"` 时，从配置中的 `agent_definition` 创建 `AgentTool` 并注册到父工具列表中，而不是执行

## 4. Factory Wiring — 工厂接入

- [x] 4.1 更新 `_ProductionEnginePort.__init__()` 以接受并将守卫钩子传递给 AgentExecutionPort
- [x] 4.2 更新 `create_engine_port()` 工厂以接受 `pre_hook`、`post_hook`、`context_engine` 参数

## 5. Tests — 测试

- [x] 5.1 测试 AgentExecutionPort 加载工具并将其传递给 LLM（模拟 LLMService）
- [x] 5.2 测试 AgentExecutionPort 查询知识库并注入上下文
- [x] 5.3 测试 AgentExecutionPort 应用 PreLLMHook BLOCK（返回被阻止的响应，不调用 LLM）
- [x] 5.4 测试 AgentDefinition.resolve_tools() 过滤 — 白名单缩小工具范围，黑名单移除工具
- [x] 5.5 测试 AgentWorker 在 invocation_mode="tool" 时路由到 AgentTool
- [x] 5.6 测试 AgentWorker 在 invocation_mode 缺失时默认为图模式

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 错误
- [x] 6.2 运行 `mypy src/` — 0 错误
- [x] 6.3 运行 `python -m pytest tests/test_services/test_orchestration/ -q` — 全部通过
