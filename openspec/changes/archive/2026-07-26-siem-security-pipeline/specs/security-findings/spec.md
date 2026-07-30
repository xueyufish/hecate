## ADDED Requirements — 新增需求

### 需求：SecurityFindingModel 数据模型
系统应提供一个 `SecurityFindingModel` ORM 表（`security_findings`），存储由 FindingEngine 产生的异常检测发现结果。每个发现结果应捕获：org_id、workspace_id、user_id（可空）、rule_name、severity、message、source_event（触发事件的 JSON）、metadata（JSON）和 created_at 时间戳。

#### 场景：规则匹配时持久化发现结果
- **当** FindingEngine 检测到批量删除违规时
- **则** 创建 `SecurityFindingModel` 行，包含 `rule_name="bulk_delete_rule"`、`severity="medium"`、`source_event` 中的触发事件和 `metadata` 中的违规上下文

#### 场景：发现结果严重级别索引用于过滤
- **当** 创建 `severity="critical"` 的发现结果时
- **则** severity 字段被索引以支持高效的 `WHERE severity >= 'high'` 查询

### 需求：FindingEngine 替换 PolicyEngine
系统应将 `PolicyEngine` 重命名为 `FindingEngine`，`AuditSecurityPolicy` ABC 重命名为 `DetectionRule` ABC，`PolicyViolation` 重命名为 `SecurityFinding`，`PolicyContext` 重命名为 `DetectionContext`，`PolicySeverity` 重命名为 `FindingSeverity`。所有现有行为应保持不变，除了发现结果被持久化而非丢弃。

#### 场景：FindingEngine 评估事件
- **当** FindingEngine 收到审计事件时
- **则** 所有注册的 DetectionRule 根据 DetectionContext 评估事件
- **且** 任何由此产生的 SecurityFinding 持久化到数据库

#### 场景：内置规则重命名
- **当** 系统使用默认规则初始化时
- **则** `BulkDeleteProtectionPolicy` 重命名为 `BulkDeleteRule`，`OffHoursSensitiveOpsPolicy` 重命名为 `OffHoursRule`，`UnusualIPDetectionPolicy` 重命名为 `UnusualIPRule`

### 需求：发现结果持久化替换 log.warning
系统应将所有 SecurityFinding 持久化到 `security_findings` 表，而不是调用 `log.warning()` 并丢弃它们。日志记录应在 DEBUG 级别继续以提供操作可见性，但持久化是主要记录方式。

#### 场景：发现结果不再丢失
- **当** OffHoursRule 检测到周末敏感操作时
- **则** 在数据库中创建 SecurityFindingModel 行
- **且** 发现结果可通过 REST API 查询

#### 场景：FindingEngine 故障不阻塞审计管道
- **当** 在发现结果持久化期间数据库不可用时
- **则** FindingEngine 记录错误并继续处理后续事件
- **且** 没有异常传播到调用者

### 需求：发现结果查询的 REST API
系统应公开 `GET /api/security/findings`，支持按 org_id、workspace_id、user_id、rule_name、severity 和时间范围过滤。

#### 场景：按严重级别查询
- **当** 客户端请求 `GET /api/security/findings?severity=high` 时
- **则** 系统仅返回严重级别为 HIGH 或 CRITICAL 的发现结果

#### 场景：按规则名称查询
- **当** 客户端请求 `GET /api/security/findings?rule_name=bulk_delete_rule` 时
- **则** 系统仅返回来自批量删除检测规则的发现结果

#### 场景：按时间范围查询带分页
- **当** 客户端请求 `GET /api/security/findings?start=...&end=...&limit=50&offset=0` 时
- **则** 系统返回时间范围内最多 50 个发现结果，按 created_at 降序排列

### 需求：发现结果保留与自动清理
系统应自动删除超过配置保留期的发现结果（`SECURITY_FINDING_RETENTION_DAYS`，默认 90 天 — 比 ToolDecision 保留期更长，因为发现结果量更少且调查价值更高）。

#### 场景：默认保留期为 90 天
- **当** 未设置 `SECURITY_FINDING_RETENTION_DAYS` 时
- **则** 超过 90 天的发现结果符合清理条件

#### 场景：清理任务每日运行
- **当** 清理任务运行时
- **则** 所有 `created_at < now() - retention_days` 的发现结果被删除
