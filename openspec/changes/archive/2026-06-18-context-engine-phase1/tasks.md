## 1. PregelRuntime 构造函数 + execution_context

- [x] 1.1 向 PregelRuntime.__init__ 添加 `context_engine: ContextEngine | None = None` 参数
- [x] 1.2 存储为 `self._context_engine`
- [x] 1.3 在 `_execution_context()` 中，当不为 None 时注入 `ctx["context_engine"] = self._context_engine`
- [x] 1.4 验证使用 context_engine=None 的现有测试通过（向后兼容）

## 2. 工具结果截断辅助函数

- [x] 2.1 在 llm_worker.py 中创建 `_truncate_tool_results(messages: list[dict], tool_result_limit: int) -> list[dict]` 辅助函数
- [x] 2.2 截断具有超过限制的估计 token 的 tool/assistant 消息；保留前 N 个 token；附加 "[截断]" 指示符
- [x] 2.3 返回新列表（非破坏性）；保持原始消息不变
- [x] 2.4 单元测试：过大的工具结果被截断，小的工具结果不变，单条消息中的多个工具结果，无工具结果时透传

## 3. 预算解析辅助函数

- [x] 3.1 在 llm_worker.py 中创建 `_resolve_budget(node_config: dict, execution_context: dict | None) -> int` 辅助函数
- [x] 3.2 优先级：node_config["max_tokens"] → execution_context["context_budget"] → 8000
- [x] 3.3 单元测试：每节点优先级，运行时回退，默认回退，三者都缺失

## 4. LLMWorker.execute() 中的上下文管道

- [x] 4.1 从快照提取消息后，检查 execution_context 中的 "context_engine"
- [x] 4.2 当存在时：解析 tool_result_limit（node_config 或默认 2000），调用 _truncate_tool_results
- [x] 4.3 通过 _resolve_budget 解析预算
- [x] 4.4 调用 context_engine.estimate_tokens(truncated_messages)
- [x] 4.5 如果超出预算：调用 context_engine.select_messages(truncated_messages, budget)
- [x] 4.6 如果仍然超出预算：调用 context_engine.compress(selected)
- [x] 4.7 使用过滤后的消息进行 context_assemble 和 llm_invoke；不修改 snapshot 或 channel_updates
- [x] 4.8 当 context_engine 不存在时：通过未更改的消息（现有行为）

## 5. LLMWorker.execute_stream() 中的上下文管道

- [x] 5.1 在 execute_stream() 中应用与 execute() 相同的管道（步骤 4.1–4.8）
- [x] 5.2 确保流式 token 对应于过滤后的消息
- [x] 5.3 WorkerResult channel_updates 必须只包含新的 assistant 消息（不是过滤后的历史）

## 6. Service 层接线

- [x] 6.1 在 services/workflow/execution_service.py 中，构造 InMemoryContextEngine 并传递给 PregelRuntime 构造函数
- [x] 6.2 在 services/workflow/test_runner.py 中，相同的接线
- [x] 6.3 在 engine/subgraph.py 中，将父运行时的 context_engine 传递给子运行时构造函数

## 7. 集成测试

- [x] 7.1 测试：带 ContextEngine 的 PregelRuntime 通过 execution_context 将其传递给 Worker
- [x] 7.2 测试：不带 ContextEngine 的 PregelRuntime——execution_context 没有 "context_engine" 键
- [x] 7.3 测试：带 ContextEngine 的 LLMWorker 在超出预算时过滤消息（非流式）
- [x] 7.4 测试：带 ContextEngine 的 LLMWorker 在超出预算时过滤消息（流式）
- [x] 7.5 测试：不带 ContextEngine 的 LLMWorker 传递完整消息（向后兼容）
- [x] 7.6 测试：上下文管道后通道消息不变（非破坏性）
- [x] 7.7 测试：上下文管道后检查点包含完整消息历史
- [x] 7.8 测试：工具结果截断限制过大输出
- [x] 7.9 测试：预算解析优先级（每节点 > 运行时 > 默认）

## 8. 文档

- [x] 8.1 更新 AGENTS.md ContextEngine 行："🔴 仅定义" → "🟡 LLMWorker 管道"
- [x] 8.2 如果关于 ContextEngine 集成有相关说明，更新 docs/design/engine-design.md
