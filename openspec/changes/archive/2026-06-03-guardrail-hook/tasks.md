## 1. Hook ABCs and NoOp Implementations — Hook ABC 和 NoOp 实现

- [x] 1.1 创建 `src/hecate/engine/guardrail.py`，包含 4 个 hook ABC：`PreLLMHook`（`on_pre_llm_call(self, invocation: LLMInvocation)`）、`PostLLMHook`（`on_post_llm_call(self, result: LLMResult)`）、`PreToolHook`（`on_pre_tool_call(self, call: ToolCall)`）、`PostToolHook`（`on_post_tool_call(self, result: ToolResult)`）
- [x] 1.2 为每个 ABC 和消息类（LLMInvocation、LLMResult、ToolCall、ToolResult）添加数据类和完整的文档字符串
- [x] 1.3 实现 4 个 NoOp 类：`NoOpPreLLMHook`、`NoOpPostLLMHook`、`NoOpPreToolHook`、`NoOpPostToolHook`——每个方法的空实现
- [x] 1.4 确保 LLMInvocation 有 `blocked: bool = False` 字段，pre hooks 可以将其设置为 True 以阻止调用

## 2. GuardrailRegistry — GuardrailRegistry

- [x] 2.1 在 `engine/guardrail.py` 中实现 `GuardrailRegistry`，包含 4 个列表和添加每个类型 hook 的 add 方法
- [x] 2.2 实现 `run_pre_llm(invocation) -> bool`（在所有 pre LLM hooks 上调用 on_pre_llm_call，返回是否应阻止）
- [x] 2.3 实现 `run_post_llm(result)`（在所有 post LLM hooks 上调用 on_post_llm_call）
- [x] 2.4 实现 `run_pre_tool(call) -> bool`（在所有 pre tool hooks 上调用 on_pre_tool_call，返回是否应阻止）
- [x] 2.5 实现 `run_post_tool(result)`（在所有 post tool hooks 上调用 on_post_tool_call）

## 3. LLMWorker Integration — LLMWorker 集成

- [x] 3.1 在 `LLMWorker.__init__` 中添加可选的 `guardrails: GuardrailRegistry | None = None` 参数（默认 None 表示不注册 guardrail）
- [x] 3.2 在 `execute` 的 llm_invoke 调用周围添加 guardrail 调用点：pre-hook 检查 → llm_invoke → post-hook
- [x] 3.3 在 `execute` 的 tool_execute 调用周围添加 guardrail 调用点：pre-hook 检查 → tool_execute → post-hook

## 4. Tests — 测试

- [x] 4.1 创建 `tests/test_engine/test_guardrail.py`
- [x] 4.2 测试 4 个 Hook ABC 不可实例化
- [x] 4.3 测试 4 个 NoOp hook 不修改输入
- [x] 4.4 测试 GuardrailRegistry 管理多个 hook：add 和 run 方法
- [x] 4.5 测试 pre-hook 阻止执行（设置 blocked=True）
- [x] 4.6 测试 post-hook 不阻止执行（即使设置 blocked=True，也被忽略）
- [x] 4.7 测试 4 个调用点在 LLMWorker 中被正确调用
- [x] 4.8 测试为 None 的 guardrails 保留现有行为（不调用 hook）

## 5. Verification — 验证

- [x] 5.1 运行 `ruff check src/hecate/engine/guardrail.py src/hecate/engine/worker.py tests/test_engine/test_guardrail.py`
- [x] 5.2 运行 `ruff format --check src/hecate/engine/guardrail.py src/hecate/engine/worker.py tests/test_engine/test_guardrail.py`
- [x] 5.3 运行 `mypy src/hecate/engine/guardrail.py src/hecate/engine/worker.py`
- [x] 5.4 运行 `python -m pytest tests/test_engine/test_guardrail.py -v`
- [x] 5.5 运行完整测试套件 `python -m pytest tests/ -q`