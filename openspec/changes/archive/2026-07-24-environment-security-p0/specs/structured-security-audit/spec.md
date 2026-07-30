## ADDED Requirements — 新增需求

### 需求：SecurityAuditEvent 数据模型
系统应提供一个 `SecurityAuditModel` ORM 表，存储结构化的安全审计事件。每个事件应捕获：agent_id、workspace_id、session_id（可空）、tool_name、arguments_hash（SHA-256）、decision、reason、policy_version、on_behalf_of_user（可空）、timestamp 和每层决策分解。

#### 场景：策略评估时创建审计事件
- **当** `ToolPolicyPipeline.evaluate_execution()` 为工具 `bash` 返回决策时
- **则** 创建一个 `SecurityAuditModel` 行，包含工具名、最终决策、原因和每层结果

#### 场景：参数以哈希而非原始形式存储
- **当** 使用参数 `{"command": "rm -rf /tmp/data"}` 的工具调用被评估时
- **则** 审计事件存储 `arguments_hash` 作为参数的 SHA-256
- **且** 原始参数不存储在审计表中

#### 场景：记录策略版本
- **当** 发生策略评估时
- **则** 审计事件记录 `policy_version` 作为评估时有效策略配置的哈希

### 需求：审计事件的异步批量写入
系统应在内存中缓冲安全审计事件，并以批处理方式刷新到数据库（每 50 个事件或 5 秒，以先到者为准）。

#### 场景：事件在达到阈值前缓冲
- **当** 5 秒内生成了 30 个审计事件时
- **则** 事件保留在内存缓冲区中（尚未写入数据库）

#### 场景：事件计数阈值触发刷新
- **当** 第 50 个事件添加到缓冲区时
- **则** 所有 50 个事件在单个批量写入中刷新到数据库
- **且** 缓冲区被清除

#### 场景：时间阈值触发刷新
- **当** 距上次刷新已过去 5 秒且缓冲区中有 10 个事件时
- **则** 所有 10 个事件刷新到数据库
- **且** 缓冲区被清除

#### 场景：优雅关闭时刷新
- **当** 应用程序收到关闭信号时
- **则** 所有缓冲的事件在关闭完成前刷新

### 需求：策略评估的审计事件发射
系统应自动从三个发射点发出 `SecurityAuditEvent`：`ToolPolicyPipeline.evaluate_visibility()`、`ToolPolicyPipeline.evaluate_execution()` 和 `ToolAccessPolicy.evaluate()`。

#### 场景：可见性评估为每个工具发出事件
- **当** `evaluate_visibility()` 过滤掉一个工具（HIDE 或 DENY）时
- **则** 发出审计事件，包含工具名、导致隐藏的层和决策

#### 场景：执行评估发出最终决策事件
- **当** `evaluate_execution()` 返回带有每层结果的最终决策时
- **则** 发出审计事件，包含最终决策、原因和所有层结果

#### 场景：ToolAccessPolicy 发出访问决策事件
- **当** `ToolAccessPolicy.evaluate()` 返回 `REQUIRE_APPROVAL` 时
- **则** 发出审计事件，包含访问决策、匹配规则和风险级别

### 需求：审计事件查询的 REST API
系统应公开一个 REST API 端点，用于查询带有过滤功能的安全审计事件。

#### 场景：按 Agent 查询
- **当** 客户端请求 `GET /api/security/audit?agent_id={agent_id}` 时
- **则** 系统返回该 Agent 在默认时间窗口内的所有审计事件

#### 场景：按决策查询
- **当** 客户端请求 `GET /api/security/audit?decision=DENY` 时
- **则** 系统仅返回决策为 DENY 的审计事件

#### 场景：按时间范围查询
- **当** 客户端请求 `GET /api/security/audit?start=2026-07-20T00:00:00&end=2026-07-24T00:00:00` 时
- **则** 系统仅返回指定时间范围内的事件

#### 场景：分页
- **当** 客户端请求 `GET /api/security/audit?limit=50&offset=100` 时
- **则** 系统从偏移量 100 开始返回 50 个事件

### 需求：可配置保留期与自动清理
系统应自动删除超过配置保留期的审计事件。

#### 场景：默认保留期为 30 天
- **当** 未设置 `AGENT_ENV_AUDIT_RETENTION_DAYS` 时
- **则** 超过 30 天的事件符合清理条件

#### 场景：清理任务定期运行
- **当** 清理任务运行时（每日）
- **则** 所有 `timestamp < now() - retention_days` 的事件被删除

#### 场景：审计禁用停止事件发射
- **当** `AGENT_ENV_AUDIT_ENABLED=false` 时
- **则** 不创建 `SecurityAuditEvent` 行
- **且** 策略评估在没有审计开销的情况下进行

### 需求：审计管道在两个环境上都工作
结构化审计管道应在 LocalEnvironment 和 DockerEnvironment 上都正常工作。

#### 场景：LocalEnvironment 发出审计事件
- **当** `AGENT_ENV_BACKEND=local` 且 `AGENT_ENV_AUDIT_ENABLED=true` 时
- **则** 策略评估正常发出审计事件
- **且** 事件可通过 REST API 查询

#### 场景：DockerEnvironment 发出审计事件
- **当** `AGENT_ENV_BACKEND=docker` 且 `AGENT_ENV_AUDIT_ENABLED=true` 时
- **则** 策略评估正常发出审计事件
- **且** 事件可通过 REST API 查询
