# Implementation Tasks — 实现任务

## 1. Naming Refactor: SecurityAudit → ToolDecision — 命名重构

- [x] 1.1 将 `src/hecate/models/security_audit.py` 重命名为 `src/hecate/models/tool_decision.py`；重命名 `SecurityAuditModel` → `ToolDecisionModel`、`SecurityAuditReadSchema` → `ToolDecisionReadSchema`、`SecurityAuditQuerySchema` → `ToolDecisionQuerySchema`
- [x] 1.2 将 `src/hecate/engine/audit_sink.py` 重命名为 `src/hecate/engine/decision_sink.py`；重命名 `AuditSink` → `DecisionSink`、`NullAuditSink` → `NullDecisionSink`、`SecurityAuditEmitter` → `ToolDecisionEmitter`、`audit_emitter` → `decision_emitter`
- [x] 1.3 将 `src/hecate/services/security/audit_service.py` 重命名为 `src/hecate/services/security/decision_service.py`；重命名 `SecurityAuditService` → `ToolDecisionService`；更新类以实现 `DecisionSink` ABC
- [x] 1.4 将 `src/hecate/api/security_audit.py` 重命名为 `src/hecate/api/tool_decisions.py`；更新路由路径 `/api/security/audit` → `/api/security/decisions`
- [x] 1.5 更新 `engine/policy_pipeline.py`、`engine/tool_access.py`、`engine/workers/tool_worker.py` 中的所有导入引用以使用新的 `decision_emitter` 和 `DecisionSink`
- [x] 1.6 更新 `core/config.py`：重命名 `AGENT_ENV_AUDIT_*` 设置 → `AGENT_ENV_DECISION_*`；添加向后兼容的别名解析（新键未设置时回退到旧键）
- [x] 1.7 更新 `.env.example`：重命名配置键，添加注释说明旧别名
- [x] 1.8 创建 Alembic 迁移以将表 `security_audit_events` 重命名为 `tool_decisions`（列名不变）
- [x] 1.9 更新 `main.py` DI 注入以使用 `ToolDecisionService` 和 `decision_emitter`
- [x] 1.10 更新所有现有测试：重命名导入、类引用、API 端点路径、表名
- [x] 1.11 运行 ruff + mypy + pytest 验证重命名完成且零错误

## 2. Naming Refactor: PolicyEngine → FindingEngine — 命名重构

- [x] 2.1 在 `src/hecate/services/audit/policy.py` 中重命名：`PolicyEngine` → `FindingEngine`、`AuditSecurityPolicy` → `DetectionRule`、`PolicyViolation` → `SecurityFinding`、`PolicyContext` → `DetectionContext`、`PolicySeverity` → `FindingSeverity`
- [x] 2.2 重命名内置规则：`BulkDeleteProtectionPolicy` → `BulkDeleteRule`、`OffHoursSensitiveOpsPolicy` → `OffHoursRule`、`UnusualIPDetectionPolicy` → `UnusualIPRule`
- [x] 2.3 更新 `src/hecate/services/audit/service.py` 以使用 `FindingEngine` 和 `SecurityFinding`
- [x] 2.4 更新整个代码库中的所有导入引用
- [x] 2.5 使用新名称更新现有审计策略测试
- [x] 2.6 运行 ruff + mypy + pytest 验证重命名完成

## 3. SecurityFinding Persistence — SecurityFinding 持久化

- [x] 3.1 创建 `src/hecate/models/security_finding.py`：`SecurityFindingModel` ORM（表 `security_findings`），字段：id、org_id、workspace_id、user_id、rule_name、severity、message、source_event（JSON）、metadata（JSON）、created_at；索引：(severity, created_at) 和 (rule_name, created_at)
- [x] 3.2 创建 Pydantic 模式：`SecurityFindingReadSchema`、`SecurityFindingQuerySchema`
- [x] 3.3 为 `security_findings` 表创建 Alembic 迁移
- [x] 3.4 修改 `FindingEngine.evaluate()` 将发现结果持久化到 `SecurityFindingModel` 而非 `log.warning()`；保留 DEBUG 级别日志以提供操作可见性
- [x] 3.5 创建 `src/hecate/services/security/finding_service.py`：`SecurityFindingService`，包含 `query()`、`get_by_id()` 和保留清理方法
- [x] 3.6 创建 `src/hecate/api/security_findings.py`：REST API `GET /api/security/findings`，支持按 org_id、workspace_id、user_id、rule_name、severity、时间范围过滤 + 分页
- [x] 3.7 在 `main.py` DI 中注入 finding 服务和 API
- [x] 3.8 添加 SecurityFinding 的保留清理任务（默认 90 天，可配置 `SECURITY_FINDING_RETENTION_DAYS`）
- [x] 3.9 编写测试：模型测试、服务测试、API 测试、FindingEngine 持久化集成测试
- [x] 3.10 运行 ruff + mypy + pytest

## 4. SIEM Export: SecurityEvent + Collector — SIEM 导出

- [x] 4.1 创建 `src/hecate/services/security/siem/__init__.py`
- [x] 4.2 创建 `src/hecate/services/security/siem/event.py`：`SecurityEvent` 数据类，字段：event_type、severity、source、timestamp、actor_user_id、actor_agent_id、action、decision、resource、metadata；`Severity` 枚举（INFO、LOW、MEDIUM、HIGH、CRITICAL）
- [x] 4.3 创建严重级别映射函数：映射 AuditLog 事件（成功/失败 → severity）、ToolDecision 事件（ALLOW/SANDBOX/DENY → severity）、SecurityFinding 事件（FindingSeverity → SecurityEvent severity）
- [x] 4.4 创建 `src/hecate/services/security/siem/exporter.py`：`SIEMExporter` ABC，带 `async export(events: list[SecurityEvent])` 方法；`NullSIEMExporter` 无操作默认实现
- [x] 4.5 创建 `src/hecate/services/security/siem/collector.py`：`SecurityEventCollector` — 订阅 AuditLog 事件、ToolDecisionEmitter 和 FindingEngine；归一化为 SecurityEvent；应用 event_type + severity 过滤；通过异步批处理缓冲并刷新到导出器
- [x] 4.6 将收集器接入 AuditMiddleware 事件流（审计写入后将事件发送到收集器）
- [x] 4.7 将收集器接入 ToolDecisionEmitter（决策发出后将事件发送到收集器）
- [x] 4.8 将收集器接入 FindingEngine（发现结果持久化后将事件发送到收集器）
- [x] 4.9 向 `core/config.py` 添加 SIEM 配置：`SIEM_ENABLED`（默认 false）、`SIEM_EXPORTERS`、`SIEM_FILTER_EVENT_TYPES`、`SIEM_MIN_SEVERITY`、`SIEM_BATCH_SIZE`（默认 50）、`SIEM_FLUSH_INTERVAL`（默认 5.0）
- [x] 4.10 在 `main.py` 生命周期中接入 SIEM 收集器启动/关闭
- [x] 4.11 编写测试：SecurityEvent 归一化测试、严重级别映射测试、收集器缓冲/刷新测试、过滤测试
- [x] 4.12 运行 ruff + mypy + pytest

## 5. SIEM Export: Webhook Exporter — Webhook 导出器

- [x] 5.1 创建 `src/hecate/services/security/siem/webhook.py`：`WebhookSIEMExporter` — 通过 httpx 异步 HTTP POST；支持 `splunk_hec` 和 `json` 格式；bearer token 认证；可配置标头
- [x] 5.2 实现重试逻辑：3 次重试，指数退避（1s、2s、4s）在 HTTP 5xx 时；在 4xx 时丢弃批并记录错误
- [x] 5.3 添加 webhook 配置：`SIEM_WEBHOOK_URL`、`SIEM_WEBHOOK_TOKEN`、`SIEM_WEBHOOK_FORMAT`（json | splunk_hec）、`SIEM_WEBHOOK_HEADERS`（JSON）
- [x] 5.4 编写测试：webhook 格式测试（json + splunk_hec）、auth 标头测试、重试测试、故障丢弃测试
- [x] 5.5 运行 ruff + mypy + pytest

## 6. SIEM Export: Syslog Exporter — Syslog 导出器

- [x] 6.1 创建 `src/hecate/services/security/siem/syslog.py`：`SyslogSIEMExporter` — RFC 5424 消息格式；TCP 和 UDP 传输；可选 TLS
- [x] 6.2 实现 RFC 5424 消息构造：PRI（facility * 8 + severity）、VERSION=1、TIMESTAMP、HOSTNAME、APPNAME、PROCID、MSGID、STRUCTURED-DATA、MSG
- [x] 6.3 实现带连接池的 TCP 传输；故障时重新连接
- [x] 6.4 实现 UDP 传输（即发即忘数据报）
- [x] 6.5 实现 TCP 模式的 TLS 包装（可配置 CA 包，可选客户端证书）
- [x] 6.6 添加 syslog 配置：`SIEM_SYSLOG_HOST`、`SIEM_SYSLOG_PORT`（默认 514）、`SIEM_SYSLOG_PROTOCOL`（tcp | udp）、`SIEM_SYSLOG_TLS`（默认 false）、`SIEM_SYSLOG_FACILITY`（默认 4 = security/authorization）
- [x] 6.7 编写测试：RFC 5424 格式合规测试、TCP 连接测试、UDP 发送测试、TLS 测试（mock）、连接故障处理测试
- [x] 6.8 运行 ruff + mypy + pytest

## 7. SIEM Export: OCSF Formatter — OCSF 格式化器

- [x] 7.1 创建 `src/hecate/services/security/siem/ocsf.py`：`OCSFFormatter` — 将 SecurityEvent 转换为符合 OCSF v1.5 的 JSON
- [x] 7.2 实现 API 事件的 OCSF Activity class（4001）映射：`activity_name`、`actor.user.uid`、`actor.user.name`、`time`、`severity_id`、`status_id`、`resources`
- [x] 7.3 实现工具决策事件的 OCSF Authorization class（2201）映射：`decision`、`action_id`、`actor.agent`、`resource.tool`、`policy`
- [x] 7.4 实现异常事件的 OCSF Security Finding class（2001）映射：`finding_info.title`、`finding_info.uid`、`severity_id`、`resources`、`time`
- [x] 7.5 实现 OCSF 包装器：格式化器包装另一个导出器（装饰器模式），在委托给包装的导出器的 `export()` 方法之前转换事件
- [x] 7.6 编写测试：所有 3 个类的 OCSF 模式字段存在测试、severity_id 映射测试、actor 字段映射测试
- [x] 7.7 运行 ruff + mypy + pytest

## 8. Integration Tests + Documentation — 集成测试 + 文档

- [x] 8.1 E2E：ToolDecision → 收集器 → 导出器流程
- [x] 8.2 E2E：FindingEngine 发现结果 → SIEM 导出流程
- [x] 8.3 E2E：多个导出器接收相同事件 + 导出器故障隔离
- [x] 8.4 过滤集成测试：严重级别阈值 + 事件类型过滤器
- [x] 8.5 禁用的 SIEM：当收集器为 None 时 emit_to_siem 无操作
- [x] 8.6 优雅关闭：停止时缓冲区被刷新
- [x] 8.7 `.env.example` 使用所有 SIEM_* 和 AGENT_ENV_DECISION_* 设置更新
- [x] 8.8 使用 SIEM Pipeline 部分更新 `docs/design/security-architecture.md`
- [x] 8.9 运行完整测试套件（ruff + mypy + pytest）— 136 通过，4 跳过，零错误
- [x] 8.10 验证 spec delta 与实现行为匹配
