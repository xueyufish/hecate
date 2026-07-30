## 1. 引擎 — ToolGateEvaluator

- [x] 1.1 创建 `src/hecate/engine/tool_gate.py`，包含 `ToolGateEvaluator` 类
- [x] 1.2 实现 `evaluate(expression: str, context: dict) -> bool` — 使用带受限命名空间（`__builtins__: {}`）的 `eval()`，捕获所有异常并返回 `False`（故障关闭），评估失败时记录 WARNING
- [x] 1.3 实现 `filter_tools(tools: list[dict], context: dict) -> list[dict]` — 迭代工具，评估 `available_when` 字段，返回过滤后的列表；没有 `available_when` 或为 `None` 的工具原样通过
- [x] 1.4 顶部使用 `from __future__ import annotations`，所有方法有类型注解，模块/类/公共方法有完整文档字符串
- [x] 1.5 引擎层约束：不从 services/ 或 models/ 导入

## 2. Models — ToolModel + 模式

- [x] 2.1 向 `models/tool.py` 中的 `ToolModel` 添加 `available_when: Mapped[str | None]` — 可为空的列，默认为 None
- [x] 2.2 向 `ToolCreateSchema` 和 `ToolUpdateSchema` 添加 `available_when: str | None = None`
- [x] 2.3 向 `ToolReadSchema` 添加 `available_when: str | None = None`
- [x] 2.4 添加 Alembic 迁移以向 `tools` 表添加 `available_when` 列（可为空，向后兼容）

## 3. LLMWorker 集成

- [x] 3.1 向 `LLMWorker.__init__` 添加 `ToolGateEvaluator` 实例（或每次调用创建——与现有模式确认）
- [x] 3.2 添加私有 `_filter_tools(tools, execution_context, channel_snapshot) -> list[dict]` 方法——从 execution_context + channel_snapshot 构建扁平上下文字典，委托给 `ToolGateEvaluator.filter_tools()`
- [x] 3.3 在 `LLMWorker.execute()` 中：在第 191 行（`tools = node_config.get("tools")`）之后、PreLLMHook 调用之前调用 `self._filter_tools(tools, execution_context, channel_snapshot)`
- [x] 3.4 在 `LLMWorker.execute_stream()` 中：在第 330 行（`tools = node_config.get("tools")`）之后、PreLLMHook 调用之前调用 `self._filter_tools(tools, execution_context, channel_snapshot)`
- [x] 3.5 上下文字典组装：合并来自 execution_context 的 `session_id`、`superstep`；来自 channel_snapshot 的 `_user_id` → `user_id`、`_agent_id` → `agent_id`、`_turn_index` → `turn_index`

## 4. 测试 — ToolGateEvaluator

- [x] 4.1 测试 `evaluate()` 使用简单相等表达式正确返回 True/False
- [x] 4.2 测试 `evaluate()` 使用复合 `and`/`or` 表达式
- [x] 4.3 测试 `evaluate()` 使用成员 `in` 检查
- [x] 4.4 测试 `evaluate()` 阻止 `__import__` 访问（返回 False，不传播异常）
- [x] 4.5 测试 `evaluate()` 阻止 `__builtins__` 访问（返回 False）
- [x] 4.6 测试 `evaluate()` 使用未定义变量 → 返回 False（故障关闭）+ 记录 WARNING
- [x] 4.7 测试 `evaluate()` 使用语法错误 → 返回 False（故障关闭）+ 记录 WARNING
- [x] 4.8 测试 `filter_tools()` 使用混合工具（有些有 available_when，有些没有）
- [x] 4.9 测试 `filter_tools()` 所有工具被过滤掉 → 空列表
- [x] 4.10 测试 `filter_tools()` 使用空输入 → 空列表
- [x] 4.11 测试 `filter_tools()` 保留工具字典结构（不修改原始字典）

## 5. 测试 — LLMWorker 集成

- [x] 5.1 测试 `execute()` 在传递给 `llm_invoke` 之前过滤带有 `available_when` 的工具
- [x] 5.2 测试 `execute()` 当没有设置 `available_when` 时原样传递工具（向后兼容）
- [x] 5.3 测试 `execute()` PreLLMHook 接收过滤后的工具列表
- [x] 5.4 测试 `execute_stream()` 与 `execute()` 相同地过滤工具
- [x] 5.5 测试从 execution_context 和 channel_snapshot 组装上下文字典
- [x] 5.6 测试缺失的 channel_snapshot 键从上下文中省略（故障关闭行为）

## 6. 测试 — 模型 + 模式

- [x] 6.1 测试 `ToolModel` 接受 `available_when` 字段并持久化到数据库
- [x] 6.2 测试 `ToolCreateSchema` 接受可选的 `available_when` 字符串
- [x] 6.3 测试 `ToolCreateSchema` 没有 `available_when` 时默认为 None
- [x] 6.4 测试 `ToolReadSchema` 在序列化输出中包含 `available_when`

## 7. 文档

- [x] 7.1 更新 AGENTS.md：如果适用，将 `tool_gate.py` 添加到关键文件表
- [x] 7.2 验证引擎层没有新的外部依赖
- [x] 7.3 运行 ruff check + ruff format --check + mypy + pytest — 全部通过
