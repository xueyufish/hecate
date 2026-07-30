## 新增的需求

### 需求：用于每次调用配置的 AgentDefinition 数据类
引擎应在 `engine/agent_tool.py` 中定义 `AgentDefinition` 数据类，包含字段：`agent_id`（UUID）、`description`（str，对 LLM 可见）、`prompt_override`（str | None，默认为 None）、`tools`（list[str] | None，默认为 None）、`disallowed_tools`（list[str]，默认为 `["agent_execute"]`）、`skills`（list[str] | None，默认为 None）、`model_override`（str | None，默认为 None）、`context_mode`（Literal["inherited", "isolated"]，默认为 "inherited"）、`max_turns`（int | None，默认为 None）、`timeout_seconds`（float | None，默认为 None）。

#### 场景：最小 AgentDefinition
- **当** 创建 `AgentDefinition(agent_id=UUID("..."), description="Research assistant")`
- **则** `tools` 应为 None（继承全部），`disallowed_tools` 应为 `["agent_execute"]`，`context_mode` 应为 `"inherited"`，所有可选字段应为 None

#### 场景：带白名单的完整 AgentDefinition
- **当** 创建 `AgentDefinition(agent_id=UUID("..."), description="Researcher", tools=["web_search", "read"], disallowed_tools=["agent_execute"], context_mode="isolated", max_turns=5, timeout_seconds=60.0)`
- **则** 所有字段应按指定值设置

### 需求：AgentTool 将智能体包装为可调用工具
引擎应定义 `AgentTool` 类，实现工具接口（name、description、execute），并通过 `EnginePort.agent_execute()` 包装 `AgentDefinition` 进行调用。

#### 场景：AgentTool 作为工具定义
- **当** 创建 `AgentTool(definition=AgentDefinition(agent_id=agent_uuid, description="Searches the web for information"))`
- **则** 工具的 name 应派生自智能体定义（例如，`"agent_{name}"`），description 应与 AgentDefinition 的描述匹配

#### 场景：AgentTool 执行
- **当** 调用 `agent_tool.execute({"task": "Find latest AI papers"})`
- **则** 它应调用 `port.agent_execute(agent_id, messages=[{"role": "user", "content": "Find latest AI papers"}], channel_snapshot=..., context=...)` 并返回智能体的响应作为工具结果

#### 场景：带上下文隔离的 AgentTool
- **当** 执行一个 `context_mode="isolated"` 的 AgentTool
- **则** 子智能体应仅接收任务消息，而非父智能体的完整对话历史

#### 场景：带上下文继承的 AgentTool
- **当** 执行一个 `context_mode="inherited"` 的 AgentTool
- **则** 子智能体应接收父智能体的消息通道内容作为其对话历史

### 需求：工具白名单/黑名单解析
在解析 AgentTool 调用的有效工具列表时，系统应应用：如果 `tools` 不为 None → 使用白名单减去 disallowed_tools；如果 `tools` 为 None → 继承父智能体的工具减去 disallowed_tools。

#### 场景：白名单减黑名单
- **当** `tools=["web_search", "read", "agent_execute"]` 且 `disallowed_tools=["agent_execute"]`
- **则** 有效工具列表应为 `["web_search", "read"]`

#### 场景：继承减黑名单
- **当** `tools=None` 且父智能体有工具 `["web_search", "read", "write", "agent_execute"]` 且 `disallowed_tools=["agent_execute", "write"]`
- **则** 有效工具列表应为 `["web_search", "read"]`

#### 场景：空白名单
- **当** `tools=[]`（空列表）
- **则** 子智能体应没有可用工具

### 需求：AgentTool 超时强制执行
当 AgentDefinition 设置了 `timeout_seconds`，AgentTool 执行应使用 `asyncio.wait_for` 强制执行超时。

#### 场景：在超时内完成执行
- **当** 智能体执行在 10 秒内完成且 `timeout_seconds=60.0`
- **则** 结果应正常返回

#### 场景：执行超过超时
- **当** 智能体执行超过 `timeout_seconds=5.0`
- **则** 工具应抛出 `asyncio.TimeoutError`，包装在工具执行错误中

### 需求：AgentTool max_turns 强制执行
当 AgentDefinition 设置了 `max_turns`，系统应将其传递给 `agent_execute()` 上下文以限制子智能体的执行循环。

#### 场景：Max turns 被遵守
- **当** 设置 `max_turns=3` 且子智能体通常会循环 10 次
- **则** 子智能体应在 3 轮后停止并返回当前响应
