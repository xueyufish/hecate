## ADDED Requirements — 新增需求

### Requirement: ToolModel supports available_when field — ToolModel 支持 available_when 字段

`ToolModel` SHALL 接受一个可选的 `available_when: str | None` 字段。当为 `None`（默认）时，工具 SHALL 始终可用。当为非空字符串时，该字符串 SHALL 是一个针对运行时上下文评估的 Python 表达式，用于确定工具可见性。

#### Scenario: Tool without available_when is always visible — 没有 available_when 的工具始终可见

- **WHEN** 工具的 `available_when=None`
- **THEN** 该工具 SHALL 出现在 LLM 的工具列表中，无论运行时上下文如何

#### Scenario: Tool with available_when expression — 带 available_when 表达式的工具

- **WHEN** 工具具有 `available_when="user_role == 'admin'"`
- **AND** 运行时上下文有 `user_role='admin'`
- **THEN** 该工具 SHALL 出现在 LLM 的工具列表中

#### Scenario: Tool with available_when expression evaluates to False — 带 available_when 表达式的工具评估为 False

- **WHEN** 工具具有 `available_when="user_role == 'admin'"`
- **AND** 运行时上下文有 `user_role='guest'`
- **THEN** 该工具 SHALL NOT 出现在 LLM 的工具列表中

### Requirement: ToolGateEvaluator evaluates expressions in restricted namespace — ToolGateEvaluator 在受限命名空间中评估表达式

系统 SHALL 在 `engine/tool_gate.py` 中提供一个 `ToolGateEvaluator` 类，使用 Python 的 `eval()` 在受限命名空间（`__builtins__: {}`，无 `__import__`，无内置函数）中评估 `available_when` 表达式。评估器 SHALL 只能访问运行时上下文字典中显式提供的变量。

#### Scenario: Simple equality expression — 简单相等表达式

- **WHEN** 调用 `evaluator.evaluate("user_role == 'admin'", {"user_role": "admin"})`
- **THEN** 它 SHALL 返回 `True`

#### Scenario: Compound expression with and/or — 带 and/or 的复合表达式

- **WHEN** 调用 `evaluator.evaluate("phase == 'EXECUTE' and budget > 1000", {"phase": "EXECUTE", "budget": 5000})`
- **THEN** 它 SHALL 返回 `True`

#### Scenario: Membership check expression — 成员检查表达式

- **WHEN** 调用 `evaluator.evaluate("'delete' in permissions", {"permissions": ["read", "write", "delete"]})`
- **THEN** 它 SHALL 返回 `True`

#### Scenario: Expression cannot access builtins — 表达式无法访问内置函数

- **WHEN** 调用 `evaluator.evaluate("__import__('os').system('rm -rf /')", {})`
- **THEN** 表达式 SHALL 抛出 `NameError`，评估器 SHALL 返回 `False`（故障关闭）

#### Scenario: Expression referencing undefined variable fails closed — 引用未定义变量的表达式故障关闭

- **WHEN** 调用 `evaluator.evaluate("user_role == 'admin'", {})`（上下文中没有 `user_role`）
- **THEN** 表达式 SHALL 抛出 `NameError`，评估器 SHALL 返回 `False`

#### Scenario: Syntax error in expression fails closed — 表达式中的语法错误故障关闭

- **WHEN** 调用 `evaluator.evaluate("user_role == ", {"user_role": "admin"})`
- **THEN** 表达式 SHALL 抛出 `SyntaxError`，评估器 SHALL 返回 `False`

### Requirement: ToolGateEvaluator filters tool list — ToolGateEvaluator 过滤工具列表

`ToolGateEvaluator` SHALL 提供一个 `filter_tools(tools, context)` 方法，接受工具字典列表和运行时上下文字典，并返回一个新列表，仅包含其 `available_when` 评估为 `True`（或没有 `available_when`）的工具。

#### Scenario: Mixed tools with and without available_when — 混合工具（有和没有 available_when）

- **WHEN** `filter_tools` 接收 3 个工具：一个 `available_when=None`，一个 `available_when="user_role == 'admin'"`（True），一个 `available_when="user_role == 'admin'"`（False）
- **THEN** 结果 SHALL 恰好包含 2 个工具（始终可用的那个和评估为 True 的那个）

#### Scenario: All tools filtered out — 所有工具被过滤掉

- **WHEN** `filter_tools` 接收 3 个工具，所有工具的 `available_when` 都评估为 `False`
- **THEN** 结果 SHALL 是一个空列表

#### Scenario: Empty tool list passthrough — 空工具列表透传

- **WHEN** `filter_tools` 接收一个空列表
- **THEN** 结果 SHALL 是一个空列表

### Requirement: LLMWorker filters tools before LLM invocation — LLMWorker 在 LLM 调用前过滤工具

`LLMWorker.execute()` 和 `execute_stream()` SHALL 在从 `node_config` 提取工具后、将工具传递给 `PreLLMHook`、`context_assemble` 或 `llm_invoke` 之前，使用 `ToolGateEvaluator` 过滤工具列表。

#### Scenario: Filtered tools reach the LLM — 过滤后的工具到达 LLM

- **WHEN** 一个节点有 5 个工具，其中 2 个的 `available_when` 评估为 `False`
- **THEN** `llm_invoke` 调用 SHALL 在其配置中只接收 3 个工具

#### Scenario: PreLLMHook sees filtered tools — PreLLMHook 看到过滤后的工具

- **WHEN** 工具被 `available_when` 过滤
- **THEN** `PreLLMHook.on_pre_llm_call()` SHALL 接收过滤后的工具列表，而不是原始列表

#### Scenario: No available_when on any tools — behavior unchanged — 任何工具都没有 available_when——行为不变

- **WHEN** 所有工具都没有设置 `available_when`
- **THEN** 工具列表 SHALL 原样通过（向后兼容）

### Requirement: Runtime context is assembled from execution_context and channel_snapshot — 运行时上下文从 execution_context 和 channel_snapshot 组装

用于 `available_when` 评估的运行时上下文 SHALL 通过将 `execution_context` 和 `channel_snapshot` 中的相关键合并到扁平字典中来组装。当源数据中存在以下变量时，以下变量 SHALL 可用：

- `session_id`（来自 execution_context）
- `superstep`（来自 execution_context）
- `user_id`（来自 channel_snapshot `_user_id`）
- `agent_id`（来自 channel_snapshot `_agent_id`）
- `turn_index`（来自 channel_snapshot `_turn_index`）

当相应功能（任务阶段检测 4.9、Token 预算 4.10、RBAC）激活时，MAY 添加其他派生变量（`phase`、`budget_remaining`、`user_role`）。

#### Scenario: Basic context variables available — 基本上下文变量可用

- **WHEN** `LLMWorker.execute()` 以 `execution_context={"session_id": "abc", "superstep": 3}` 和 `channel_snapshot={"_user_id": "user123", "_turn_index": 5}` 运行
- **THEN** 用于 `available_when` 的运行时上下文 SHALL 包含 `session_id="abc"`、`superstep=3`、`user_id="user123"`、`turn_index=5`

#### Scenario: Missing channel_snapshot keys are omitted — 缺失的 channel_snapshot 键被省略

- **WHEN** `channel_snapshot` 不包含 `_user_id`
- **THEN** 运行时上下文 SHALL NOT 包含 `user_id` 键（引用它的表达式将故障关闭）

### Requirement: Tool CRUD schemas support available_when field — 工具 CRUD 模式支持 available_when 字段

`ToolCreateSchema` 和 `ToolUpdateSchema` SHALL 接受可选的 `available_when: str | None` 字段。`ToolReadSchema` SHALL 在其输出中包含 `available_when` 字段。

#### Scenario: Create tool with available_when — 使用 available_when 创建工具

- **WHEN** 使用 `{"name": "admin_delete", "available_when": "user_role == 'admin'", ...}` 调用 `POST /api/tools`
- **THEN** 工具 SHALL 被创建，`available_when` 字段存储在数据库中

#### Scenario: Create tool without available_when — 不包含 available_when 创建工具

- **WHEN** 调用 `POST /api/tools` 而请求体中不包含 `available_when`
- **THEN** 工具 SHALL 以 `available_when=None` 创建（始终可用）

#### Scenario: Read tool includes available_when — 读取工具包含 available_when

- **WHEN** 对 `available_when="user_role == 'admin'"` 的工具调用 `GET /api/tools/{id}`
- **THEN** 响应 SHALL 包含 `"available_when": "user_role == 'admin'"`
