# engine-tool-loop — Tasks

## 1. EnginePort 接口扩展

- [x] 1.1 `src/hecate/engine/ports.py`: 在 `EnginePort` 新增可选方法 `llm_invoke_structured(messages, config) -> AsyncGenerator[dict[str, Any], None]`，默认实现委托 `llm_invoke` 收集全部 token 后 yield 单个 chunk `{"content": str, "tool_calls": None}`，docstring 说明适用场景与降级语义
- [x] 1.2 `src/hecate/engine/ports.py`: 确认 `llm_invoke_structured` 不是 abstractmethod（可选方法），与 `context_assemble`/`agent_execute` 的既有模式一致

## 2. EnginePortAdapter 结构化实现

- [x] 2.1 `src/hecate/services/orchestration/engine_port_adapter.py`: 覆盖 `llm_invoke_structured`——遍历 `LLMService.chat_stream`，yield `{"content": token}` 增量块，累积 `chunk["tool_calls"]` delta（按 `index` 合并 `function.arguments` 与 `function.name`），流结束时 yield 最终块携带完整 `tool_calls`
- [x] 2.2 `src/hecate/services/orchestration/engine_port_adapter.py`: 无 tool_calls 时最终块 `tool_calls=None`；保持 `_record_cost` 调用（或确认成本记录语义不变）

## 3. Chat 图 tools 注入

- [x] 3.1 `src/hecate/engine/templates.py`: `build_chat_graph()` 新增 `tools: list[dict[str, Any]] | None = None` 参数，注入 `llm` 节点 config 的 `tools` 键（`None` 时省略或置 None）
- [x] 3.2 `src/hecate/services/workflow/execution_service.py`: `execute()` 中调用 `build_chat_graph(...)` 时透传 `tools= tools` 参数

## 4. LLMWorker 工具调用检测

- [x] 4.1 `src/hecate/engine/workers/llm_worker.py`（非流式 `execute`）: `node_config.get("tools")` 非空时改用 `port.llm_invoke_structured`；从结构化响应解析 `tool_calls`；检测到时构造 assistant tool_calls 消息写入 `messages` 通道并置 `updates["_has_tool_call"] = True`；无 tool_calls 时行为与现状一致
- [x] 4.2 `src/hecate/engine/workers/llm_worker.py`（流式 `execute_stream`）: 有 tools 时调用 `llm_invoke_structured`，yield content token 块保持 SSE 兼容；流结束后若检测到 tool_calls，最终结果携带 `_has_tool_call: True` 与 tool_calls（不改变 content chunk 形状）
- [x] 4.3 `src/hecate/engine/workers/llm_worker.py`: 移除 placeholder 注释（`# Check for tool calls in response (placeholder ...)`），替换为真实检测逻辑；无 tools 时维持 `llm_invoke` 调用路径不变

## 5. 测试

- [x] 5.1 `tests/test_engine/test_ports.py`（或对应文件）: 测试 `llm_invoke_structured` 默认实现——未覆盖时委托 `llm_invoke` 返回单 chunk 且 `tool_calls=None`
- [x] 5.2 `tests/test_engine/test_workers/test_llm_worker.py`: 非流式——mock 结构化 port 返回 tool_calls，断言 WorkerResult 的 `_has_tool_call: True` 与 assistant 消息含 tool_calls
- [x] 5.3 `tests/test_engine/test_workers/test_llm_worker.py`: 流式——mock 结构化 port 分块返回，断言 content 块顺序与最终 `_has_tool_call` 信号
- [x] 5.4 `tests/test_engine/test_workers/test_llm_worker.py`: 无 tools 时仍调用 `llm_invoke`（回归，行为不变）
- [x] 5.5 `tests/test_engine/test_templates.py`: `build_chat_graph` 注入 tools 到 LLM 节点 config；无 tools 时省略
- [x] 5.6 集成测试: chat 图端到端——mock EnginePort 返回 tool_calls，断言 PregelRuntime 执行 `check_tools → tool_call → llm` 循环直至无工具调用（复用或扩展既有 chat 图测试）
- [x] 5.7 回归: `tests/test_engine/test_execution_service.py`、`tests/test_engine/test_pregel.py`、`tests/test_services/test_workflow/` 相关测试全部通过

## 6. 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/` 通过
- [x] 6.2 运行 `ruff format --check src/ tests/` 通过
- [x] 6.3 运行 `mypy src/` 通过
- [x] 6.4 运行 `python -m pytest tests/ -q` 通过（或受影响层全量）