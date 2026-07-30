## ADDED Requirements — 新增需求

### 需求：ToolDecisionModel 数据模型
系统应提供一个 `ToolDecisionModel` ORM 表（`tool_decisions`），存储结构化的工具策略决策事件。每个事件应捕获：agent_id、workspace_id、session_id（可空）、tool_name、arguments_hash（SHA-256）、decision、reason、policy_version、on_behalf_of_user（可空）、timestamp 和每层决策分解。

#### 场景：策略评估时创建决策事件
- **当** `ToolPolicyPipeline.evaluate_execution()` 为工具 `bash` 返回决策时
- **则** 创建 `ToolDecisionModel` 行，包含工具名、最终决策、原因和每层结果

#### 场景：参数以哈希而非原始形式存储
- **当** 使用参数 `{"command": "rm -rf /tmp/data"}` 的工具调用被评估时
- **则** 决策事件存储 `arguments_hash` 作为参数的 SHA-256
- **且** 原始参数不存储在决策表中

#### 场景：记录策略版本
- **当** 发生策略评估时
- **则** 决策事件记录 `policy_version` 作为评估时有效策略配置的哈希

### 需求：决策事件的异步批量写入
系统应在内存中缓冲工具决策事件，并以批处理方式刷新到数据库（每 `AGENT_ENV_DECISION_BATCH_SIZE` 个事件或 `AGENT_ENV_DECISION_FLUSH_INTERVAL` 秒，以先到者为准）。

#### 场景：事件在达到阈值前缓冲
- **当** 在刷新间隔内生成了 30 个决策事件时
- **则** 事件保留在内存缓冲区中（尚未写入数据库）

#### 场景：事件计数阈值触发刷新
- **当** 达到批大小阈值时
- **则** 所有缓冲事件在单个批量写入中刷新到数据库
- **且** 缓冲区被清除

#### 场景：时间阈值触发刷新
- **当** 刷新间隔过去且缓冲区有待处理事件时
- **则** 所有事件刷新到数据库
- **且** 缓冲区被清除

#### 场景：优雅关闭时刷新
- **当** 应用程序收到关闭信号时
- **则** 所有缓冲的事件在关闭完成前刷新

### 需求：策略评估的决策事件发射
系统应从三个发射点自动发出工具决策事件：`ToolPolicyPipeline.evaluate_visibility()`、`ToolPolicyPipeline.evaluate_execution()` 和 `ToolAccessPolicy.evaluate()`。

#### 场景：可见性评估为每个工具发出事件
- **当** `evaluate_visibility()` 过滤掉一个工具（HIDE 或 DENY）时
- **则** 发出决策事件，包含工具名、导致隐藏的层和决策

#### 场景：执行评估发出最终决策事件
- **当** `evaluate_execution()` 返回带有每层结果的最终决策时
- **则** 发出决策事件，包含最终决策、原因和所有层结果

#### 场景：ToolAccessPolicy 发出访问决策事件
- **当** `ToolAccessPolicy.evaluate()` 返回 `REQUIRE_APPROVAL` 时
- **则** 发出决策事件，包含访问决策、匹配规则和风险级别

### 需求：决策事件查询的 REST API
系统应公开一个 REST API 端点 `GET /api/security/decisions`，用于查询工具决策事件，支持按 agent_id、workspace_id、session_id、decision、tool_name 和时间范围过滤。

#### 场景：按 Agent 查询
- **当** 客户端请求 `GET /api/security/decisions?agent_id={agent_id}` 时
- **则** 系统返回该 Agent 在默认时间窗口内的所有决策事件

#### 场景：按决策查询
- **当** 客户端请求 `GET /api/security/decisions?decision=DENY` 时
- **则** 系统仅返回决策为 DENY 的决策事件

#### 场景：按时间范围查询
- **当** 客户端请求 `GET /api/security/decisions?start=...&end=...` 时
- **则** 系统仅返回指定时间范围内的事件

#### 场景：分页
- **当** 客户端请求 `GET /api/security/decisions?limit=50&offset=100` 时
- **则** 系统从偏移量 100 开始返回 50 个事件

### 需求：可配置保留期与自动清理
系统应自动删除超过配置保留期的决策事件（`AGENT_ENV_DECISION_RETENTION_DAYS`，默认 30 天）。

#### 场景：默认保留期为 30 天
- **当** 未设置 `AGENT_ENV_DECISION_RETENTION_DAYS` 时
- **则** 超过 30 天的事件符合清理条件

#### 场景：决策日志禁用
- **当** `AGENT_ENV_DECISION_ENABLED=false` 时
- **则** 不创建 `ToolDecisionModel` 行
- **且** 策略评估在没有决策日志开销的情况下进行

### 需求：向后兼容的配置别名
系统应接受旧配置键（`AGENT_ENV_AUDIT_*`）作为新键（`AGENT_ENV_DECISION_*`）的别名。当两者都设置时，新键优先。

#### 场景：旧配置键有效
- **当** `.env` 包含 `AGENT_ENV_AUDIT_ENABLED=true` 但不包含 `AGENT_ENV_DECISION_ENABLED` 时
- **则** 系统启用决策日志，如同设置了 `AGENT_ENV_DECISION_ENABLED=true`

#### 场景：新键优先
- **当** `.env` 同时包含 `AGENT_ENV_AUDIT_ENABLED=false` 和 `AGENT_ENV_DECISION_ENABLED=true` 时
- **则** 系统启用决策日志（新键获胜）
