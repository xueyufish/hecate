## MODIFIED Requirements — 修改的需求

### 需求：审计安全策略引擎
系统应实现一个基于规则的 `FindingEngine`（从 `PolicyEngine` 重命名），根据可配置的 `DetectionRule`（从 `AuditSecurityPolicy` 重命名）评估审计事件。系统应包括 3 个内置规则：`BulkDeleteRule`（从 `BulkDeleteProtectionPolicy` 重命名），当同一用户在 1 分钟内删除 5+ 个资源时发出警报；`OffHoursRule`（从 `OffHoursSensitiveOpsPolicy` 重命名），当敏感操作发生在配置的业务时间之外时发出警报；`UnusualIPRule`（从 `UnusualIPDetectionPolicy` 重命名），当登录 IP 不在用户近期历史中时发出警报。规则违反应作为 `SecurityFinding` 记录（从 `PolicyViolation` 重命名）持久化到 `security_findings` 表中，而不是通过 `log.warning()` 丢弃。`FindingSeverity` 枚举（从 `PolicySeverity` 重命名）定义级别：LOW、MEDIUM、HIGH、CRITICAL。

#### 场景：检测到批量删除并持久化
- **当** 用户在 1 分钟内执行 5 次或更多删除操作时
- **则** FindingEngine 创建一个带有 `rule_name="bulk_delete_rule"` 和 `severity="medium"` 的 SecurityFinding
- **且** 发现结果持久化到 `security_findings` 表
- **且** 发现结果可通过 `GET /api/security/findings` 查询

#### 场景：检测到非工作时间敏感操作
- **当** 周日凌晨 2:00 发生工作空间删除操作时
- **则** FindingEngine 创建一个带有 `rule_name="off_hours_rule"` 和 `severity="low"` 的 SecurityFinding
- **且** 发现结果持久化到 `security_findings` 表

#### 场景：检测到异常 IP
- **当** 用户从不在其已知 IP 集中的 IP 地址执行操作时
- **则** FindingEngine 创建一个带有 `rule_name="unusual_ip_rule"` 和 `severity="low"` 的 SecurityFinding
- **且** 发现结果持久化到 `security_findings` 表

#### 场景：FindingEngine 故障不阻塞审计
- **当** FindingEngine 在持久化期间遇到数据库错误时
- **则** 记录错误，审计管道继续处理
- **且** 没有异常传播到 AuditMiddleware

#### 场景：自定义 DetectionRule 注册
- **当** 用户实现自定义 `DetectionRule` 子类时
- **则** 规则可通过 `FindingEngine.register(rule)` 注册并与内置规则一起评估
