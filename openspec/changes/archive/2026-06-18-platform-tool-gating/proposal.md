## Why — 为什么

拥有许多工具的 Agent 遭受上下文膨胀、推理错误和安全暴露——LLM 每次交互都会看到每个工具，无论是否相关。一个硬平台门控（`available_when`）允许开发人员根据运行时上下文（验证状态、用户角色、任务阶段、预算）有条件地向 LLM 隐藏工具，从而提高推理质量、降低 token 成本并强制执行仅靠提示工程无法保证的业务规则。10 平台研究表明 Salesforce Agentforce 使用了这种确切的模式（带有表达式评估的 `available when` 字段），并且 Dify 有一个开放的功能请求（#27887）要求此功能。

## What Changes — 变更内容

- 向 `ToolModel` 和相应的 Pydantic 模式添加 `available_when: str | None` 字段——每次调用评估的声明性表达式
- 在 `engine/tool_gate.py` 中实现 `ToolGateEvaluator`——表达式评估器，可访问运行时上下文（会话、用户、阶段、预算）
- 将工具过滤接入 `LLMWorker.execute()` 和 `execute_stream()`——从 `node_config` 提取后、`context_assemble` 和 `llm_invoke` 之前过滤工具列表
- 表达式语言：使用 `eval()` 的 Python 安全子集，带受限命名空间（无内置函数，无导入）——支持 `==`、`!=`、`>`、`<`、`>=`、`<=`、`and`、`or`、`not`、`in`、括号
- 表达式可用的上下文变量：`phase`、`budget_remaining`、`user_id`、`user_role`、`session_id`、`turn_index`，以及通道快照值
- 仅软门控——过滤后的工具对 LLM 的工具列表隐藏；无执行层硬阻断（10 平台共识：没有平台实现硬门控）

## Capabilities — 能力

### New Capabilities — 新增能力

- `tool-gating`：基于 `available_when` 表达式的条件工具可见性——包括 ToolGateEvaluator、表达式评估语义、上下文变量模型和 LLMWorker 集成

### Modified Capabilities — 修改的能力

无——tool-registry spec 涵盖执行路由，不是可见性；ToolModel 字段添加是实现细节

## Impact — 影响

- **Models**：`ToolModel` 获得 `available_when` 列（可为空，向后兼容迁移）
- **Engine**：新的 `engine/tool_gate.py`——零外部依赖，遵循现有引擎 ABC 模式
- **Workers**：`LLMWorker.execute()` 和 `execute_stream()` 获得 `_filter_tools()` 调用——每个方法一行新代码
- **API**：工具 CRUD 模式获得可选的 `available_when` 字段
- **Tests**：新的 `tests/test_engine/test_tool_gate.py`——评估器测试、上下文变量测试、LLMWorker 集成测试
- **Dependencies**：无——使用 Python 内置 `eval()` 带受限命名空间
