## ADDED Requirements — 新增需求

### 需求：AgentState 数据模型

系统应提供一个 `AgentState` Pydantic 模型，表示每会话工作状态。每个 AgentState 实例作用于单个（agent_id、session_id）对。

#### 场景：AgentState 包含必需的字段
- **当** 创建 AgentState 时
- **则** 它包含 `session_id`、`agent_id`、`summary`、`context`、`permission_context`、`tool_context`、`task_context`、`environment_root` 和 `metadata` 字段

#### 场景：AgentState 默认为空状态
- **当** 仅使用 session_id 和 agent_id 创建 AgentState 时
- **则** `summary` 默认为空字符串，`context` 默认为空列表，所有子上下文默认为空字典

#### 场景：AgentState 可序列化为 JSON
- **当** 在 AgentState 实例上调用 `model_dump()` 时
- **则** 返回包含所有字段的可 JSON 序列化字典

#### 场景：AgentState 可从 JSON 反序列化
- **当** 使用有效字典调用 `AgentState.model_validate(data)` 时
- **则** 返回具有匹配字段值的 AgentState 实例

### 需求：AgentStateStore 抽象接口

系统应提供一个 `AgentStateStore` 抽象基类，定义 AgentState 的持久化契约。

#### 场景：保存状态
- **当** 调用 `save(agent_id, session_id, state)` 时
- **则** AgentState 被持久化，可通过相同的（agent_id、session_id）键检索

#### 场景：加载现有状态
- **当** 调用 `load(agent_id, session_id)` 且该键存在状态时
- **则** 返回先前保存的 AgentState

#### 场景：加载不存在的状态
- **当** 调用 `load(agent_id, session_id)` 且该键不存在状态时
- **则** 返回 `None`

#### 场景：删除状态
- **当** 调用 `delete(agent_id, session_id)` 时
- **则** 后续对同一键的 `load()` 返回 `None`

#### 场景：列出 Agent 的会话
- **当** 调用 `list_sessions(agent_id)` 时
- **则** 返回该 Agent 所有会话的会话摘要列表（session_id、updated_at）

### 需求：InMemoryStateStore 实现

系统应提供一个实现 `AgentStateStore` 的 `InMemoryStateStore`，用于单进程使用和测试。

#### 场景：进程内内存持久化
- **当** 状态保存到 InMemoryStateStore 时
- **则** 可在同一进程生命周期内加载

#### 场景：进程重启后状态丢失
- **当** 进程退出时
- **则** InMemoryStateStore 中的所有状态都丢失（MVP 的预期行为）

#### 场景：并发访问安全
- **当** 两个协程同时为相同的（agent_id、session_id）保存状态时
- **则** 不会发生数据损坏（asyncio.Lock 序列化写入）

#### 场景：不同会话相互独立
- **当** 为同一 Agent 的会话 A 和会话 B 保存状态时
- **则** 加载会话 A 返回会话 A 的状态，而不是会话 B 的

### 需求：WorkflowExecutionService 状态生命周期

系统应将 AgentState 加载/保存生命周期集成到 `WorkflowExecutionService.execute()` 中。

#### 场景：在调用入口加载状态
- **当** 使用具有现有状态的 session_id 调用 `execute()` 时
- **则** 从存储加载 AgentState 并注入到 `execution_context["_agent_state"]`

#### 场景：不存在状态时创建新状态
- **当** 使用没有现有状态的 session_id 调用 `execute()` 时
- **则** 使用给定的 session_id 和 agent_id 创建新的空 AgentState

#### 场景：在调用退出时保存状态
- **当** `execute()` 完成时（包括流式和非流式）
- **则** 当前 AgentState 被保存到存储

#### 场景：状态跨调用持久化
- **当** 使用相同的 session_id 调用两次 `execute()` 时
- **则** 第二次调用看到第一次调用的状态（context、summary 等）

#### 场景：环境根路径自动填充
- **当** 使用 agent_id 调用 `execute()` 且配置了 EnvironmentManager 时
- **则** AgentState 的 `environment_root` 字段从 Agent 的环境中填充

### 需求：AgentStateStore 是可选的

系统应在未配置 AgentStateStore 的情况下正常运行（向后兼容）。

#### 场景：未配置状态存储
- **当** 创建 WorkflowExecutionService 时没有 AgentStateStore
- **则** execute() 的行为与之前完全相同（无状态持久化，无错误）
