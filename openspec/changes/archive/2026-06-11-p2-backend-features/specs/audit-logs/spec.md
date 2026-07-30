## ADDED Requirements — 新增需求

### 需求：带月度分区的 AuditLog 模型
系统应将审计日志记录持久化到按 `created_at` 月度分区的 PostgreSQL 表中。每条记录应包含：`id`（UUID PK）、`org_id`（UUID，NOT NULL）、`workspace_id`（UUID，可空）、`user_id`（UUID，NOT NULL）、`action`（VARCHAR(100)，NOT NULL）、`resource_type`（VARCHAR(50)）、`resource_id`（UUID，可空）、`request_method`（VARCHAR(10)）、`request_path`（VARCHAR(500)）、`response_status`（INTEGER）、`ip_address`（VARCHAR(255)）、`user_agent`（VARCHAR(500)，可空）、`success`（BOOLEAN，NOT NULL）、`error_code`（VARCHAR(100)，可空）、`error_message`（TEXT，可空）、`metadata`（JSONB，默认 '{}'）、`created_at`（TIMESTAMPTZ，NOT NULL）

#### 场景：创建审计日志条目
- **当** 已认证用户执行 API 操作
- **则** 系统应从请求上下文填充所有字段，创建 AuditLog 记录

#### 场景：月度分区创建
- **当** 新月份开始时
- **则** 系统应通过 pg_partman 自动为该月创建新分区

### 需求：AuditStore 抽象接口
系统应定义 `AuditStore` ABC，带方法：`write(event)`、`query(filters)`、`export(format, filters)`、`archive(before_date)`。默认实现应为写入分区 PostgreSQL 表的 `DatabaseAuditStore`

#### 场景：通过存储写入审计事件
- **当** 产生审计事件
- **则** 系统应通过 AuditStore ABC 写入它，允许替代实现而不改变业务逻辑

#### 场景：使用过滤器查询审计日志
- **当** 发送 GET 请求到 `/api/audit-logs`，携带查询参数 `org_id`、`workspace_id`、`user_id`、`action`、`resource_type`、`resource_id`、`success`、`start_time`、`end_time`
- **则** 系统应返回匹配所有提供过滤器的分页结果

### 需求：用于自动 API 捕获的 AuditMiddleware
系统应注册一个 FastAPI `BaseHTTPMiddleware`，捕获每个 API 请求（排除 `/health`、`/metrics`、OPTIONS 请求和静态资产）。中间件应提取 `AuthContext` 以获取 `user_id`、`org_id`、`workspace_id`，记录请求方法、路径、响应状态，并异步入队审计事件

#### 场景：成功 API 请求已捕获
- **当** 已认证的 POST 请求到 `/api/agents` 返回 201
- **则** 中间件应创建 `action="agent.create"`、`success=true`、`response_status=201` 的审计事件

#### 场景：失败 API 请求已捕获
- **当** 已认证的 DELETE 请求到 `/api/agents/{id}` 返回 404
- **则** 中间件应创建 `success=false`、`response_status=404`、`error_code="NOT_FOUND"` 的审计事件

#### 场景：未认证请求从审计中排除
- **当** 未认证请求到达任何端点
- **则** 中间件应创建 `user_id=NULL`、`action="api.unauthenticated"` 的审计事件

### 需求：审计事件的异步批量写入器
系统应使用基于 `asyncio.Queue` 的批量写入器，从队列中取出审计事件并以批量方式插入数据库（可配置批量大小，默认 100）。写入器不应阻塞请求路径

#### 场景：高吞吐量审计写入
- **当** 1 秒内产生 1000 个审计事件
- **则** 批量写入器应累积并以批处理方式插入，不阻塞任何 API 请求

### 需求：包含 6 个模块的审计操作分类
系统应定义组织为 6 个模块的操作类型：AUTH（登录、登出、密码更改、API 密钥 CRUD、权限更改）、AGENT（CRUD、部署、执行）、WORKFLOW（CRUD、执行、版本管理）、KNOWLEDGE（知识库 CRUD、文档操作、查询）、TOOL（注册、更新、执行）、SYSTEM（用户 CRUD、工作区 CRUD、设置、速率限制触发）。每个操作应使用点号表示法（例如 `"agent.create"`、`"auth.login.success"`）

#### 场景：操作类型验证
- **当** 创建审计事件时操作类型为 `"agent.create"`
- **则** 系统应验证该操作是 AGENT 模块内的已识别操作类型

### 需求：审计日志导出
系统应支持通过 `GET /api/audit-logs/export` 导出审计日志，带 `format` 参数（csv 或 json）和所有查询过滤器。每个请求的导出应限制为 100,000 条记录

#### 场景：以 CSV 格式导出审计日志
- **当** 发送 GET 请求到 `/api/audit-logs/export?format=csv&start_time=2026-06-01&end_time=2026-06-10`
- **则** 系统应返回包含所有匹配审计日志记录的 CSV 文件

### 需求：审计日志冷归档到 MinIO
系统应支持配置保留阈值（默认 365 天）。超过阈值的分区应作为压缩 JSON 文件归档到 MinIO，然后从 PostgreSQL 中删除

#### 场景：归档旧审计日志
- **当** 分区早于配置的保留阈值
- **则** 系统应将分区数据导出到 MinIO 并从 PostgreSQL 中删除分区

### 需求：审计安全策略引擎
系统应实现一个基于规则的安全策略引擎，根据可配置的策略评估审计事件。系统应包含 3 个内置策略：`bulk_delete_protection`（同一用户在 1 分钟内删除 5 个以上资源时告警）、`off_hours_sensitive_ops`（敏感操作在配置的营业时间外发生时告警）、`unusual_ip_detection`（登录 IP 不在用户最近历史记录中时告警）。策略违规应记录为结构化警告

#### 场景：检测到批量删除
- **当** 用户在 1 分钟内执行 5 个或更多删除操作
- **则** 系统应记录安全警告，包含策略名称 `"bulk_delete_protection"` 和用户详情

#### 场景：非工作时间的敏感操作
- **当** 工作区删除操作在周日凌晨 2:00 发生
- **则** 系统应记录安全警告，包含策略名称 `"off_hours_sensitive_ops"`

### 需求：审计日志统计 API
系统应提供 `GET /api/audit-logs/stats`，带 `group_by` 参数（action、user、resource_type）和时间范围过滤器。返回按组的聚合计数

#### 场景：按操作分组统计
- **当** 发送 GET 请求到 `/api/audit-logs/stats?group_by=action&start_time=2026-06-01`
- **则** 系统应返回操作类型列表及其在指定时间范围内的出现次数
