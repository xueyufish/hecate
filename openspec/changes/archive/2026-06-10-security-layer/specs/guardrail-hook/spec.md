## MODIFIED Requirements — 修改的需求

### 需求：GuardrailAction 枚举
系统应定义 `GuardrailAction` 为 `StrEnum`，具有三个成员：`ALLOW`、`BLOCK` 和 `SANITIZE`

#### 场景：字符串比较
- **当** `result.action == GuardrailAction.ALLOW`
- **则** 计算结果为 `True`

#### 场景：字面字符串比较
- **当** `result.action == "allow"`
- **则** 计算结果为 `True`（StrEnum 兼容性）

#### 场景：清洗动作
- **当** `result.action == GuardrailAction.SANITIZE`
- **则** 计算结果为 `True`

#### 场景：三个成员
- **当** 计算 `len(GuardrailAction)`
- **则** 结果为 `3`

#### 场景：清洗字符串值
- **当** `GuardrailAction.SANITIZE` 转换为字符串
- **则** 值为 `"sanitize"`

### 需求：GuardrailResult 数据类
系统应在 `engine/guardrail.py` 中定义 `GuardrailResult` 数据类，具有三个字段：`action`（GuardrailAction，默认 ALLOW）、`reason`（str，默认 ""）和 `modified_data`（dict | None，默认 None）

#### 场景：带默认值的允许动作
- **当** 构造 `GuardrailResult()`
- **则** `action` 为 `GuardrailAction.ALLOW`，`reason` 为 `""`，`modified_data` 为 `None`

#### 场景：带原因的阻止动作
- **当** 构造 `GuardrailResult(action=GuardrailAction.BLOCK, reason="Prompt injection")`
- **则** `action` 为 `GuardrailAction.BLOCK`，`reason` 为 `"Prompt injection"`，`modified_data` 为 `None`

#### 场景：带修改数据的清洗动作
- **当** 构造 `GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"messages": [...]})`
- **则** `action` 为 `GuardrailAction.SANITIZE`，`modified_data` 为 `{"messages": [...]}`

## ADDED Requirements — 新增需求

### 需求：NoOp 钩子支持 modified_data
NoOp 钩子实现应返回 `modified_data=None` 的 `GuardrailResult`

#### 场景：NoOpPreLLMHook 返回不带 modified_data 的 allow
- **当** 调用 `NoOpPreLLMHook().on_pre_llm_call(messages, model, tools)`
- **则** 返回 `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

#### 场景：NoOpPostLLMHook 返回不带 modified_data 的 allow
- **当** 调用 `NoOpPostLLMHook().on_post_llm_call(response, messages)`
- **则** 返回 `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

#### 场景：NoOpPreToolHook 返回不带 modified_data 的 allow
- **当** 调用 `NoOpPreToolHook().on_pre_tool_call(name, arguments, context)`
- **则** 返回 `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

#### 场景：NoOpPostToolHook 返回不带 modified_data 的 allow
- **当** 调用 `NoOpPostToolHook().on_post_tool_call(name, result, context)`
- **则** 返回 `GuardrailResult(action=GuardrailAction.ALLOW, modified_data=None)`

### 需求：Worker 处理 SANITIZE 动作
LLMWorker 和 ToolWorker 应通过将相关数据替换为 `modified_data` 内容来处理 SANITIZE 动作

#### 场景：LLMWorker 从 PreLLMHook 接收 SANITIZE
- **当** `PreLLMHook` 返回 `GuardrailResult(action=SANITIZE, modified_data={"messages": <anonymized>})`
- **则** LLMWorker 应使用匿名化消息进行 LLM 调用，而不是原始消息

#### 场景：LLMWorker 从 PostLLMHook 接收 SANITIZE
- **当** `PostLLMHook` 返回 `GuardrailResult(action=SANITIZE, modified_data={"response": <sanitized>})`
- **则** LLMWorker 应在通道更新中使用清洗后响应

#### 场景：ToolWorker 从 PostToolHook 接收 SANITIZE
- **当** `PostToolHook` 返回 `GuardrailResult(action=SANITIZE, modified_data={"result": <masked>})`
- **则** ToolWorker 应在工具结果消息中使用掩码后结果

#### 场景：SANITIZE 带空 modified_data
- **当** 返回 `GuardrailResult(action=SANITIZE, modified_data=None)`
- **则** worker 应将其视为 ALLOW（原样通过）并记录警告
