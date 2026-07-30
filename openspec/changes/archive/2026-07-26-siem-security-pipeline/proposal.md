## Why — 为什么

Hecate 有三个安全审计源（API 级的 AuditLog、工具级的 SecurityAudit、异常检测的 PolicyEngine），但**无法将事件导出到外部 SIEM 系统**。此外，PolicyEngine 违规仅 `log.warning()` — 完全丢失。命名也令人困惑："SecurityAudit" 和 "AuditLog" 都包含 "Audit" 但捕获的是根本不同的内容，"Policy" 在 ToolPolicyPipeline、ToolAccessPolicy 和 PolicyEngine 之间被过度使用，具有三种不同的含义。

此变更将三个源统一为连贯的 SIEM 导出管道，修复了发现结果持久化的缺口，并使命名与行业标准（AWS CloudTrail / GuardDuty / Security Hub、OCSF schema classes）对齐。

## What Changes — 变更内容

### 命名重构（破坏性 — 9.14 刚合并，无外部消费者）

- `SecurityAuditModel` → **`ToolDecisionModel`**（表 `security_audit_events` → `tool_decisions`）
- `SecurityAuditEmitter` / `AuditSink` → **`ToolDecisionEmitter`** / **`DecisionSink`**
- `SecurityAuditService` → **`ToolDecisionService`**
- `SecurityAuditReadSchema` / `SecurityAuditQuerySchema` → **`ToolDecisionReadSchema`** / **`ToolDecisionQuerySchema`**
- API：`GET /api/security/audit` → **`GET /api/security/decisions`**
- 配置：`AGENT_ENV_AUDIT_*` → **`AGENT_ENV_DECISION_*`**
- `PolicyViolation` → **`SecurityFinding`**
- `PolicyEngine` → **`FindingEngine`**
- `AuditSecurityPolicy` ABC → **`DetectionRule`** ABC
- `PolicyContext` → **`DetectionContext`**
- `PolicySeverity` → **`FindingSeverity`**
- `BulkDeleteProtectionPolicy` → **`BulkDeleteRule`**
- `OffHoursSensitiveOpsPolicy` → **`OffHoursRule`**
- `UnusualIPDetectionPolicy` → **`UnusualIPRule`**

### 发现结果持久化（修复丢失的违规）

- 新的 `SecurityFindingModel` 表 — 持久化 FindingEngine 违规，而非通过 `log.warning()` 丢弃
- REST API：`GET /api/security/findings` 用于查询持久化的发现结果
- 发现结果作为高严重级别事件馈送到 SIEM 导出管道

### SIEM 导出管道（新能力）

- **`SecurityEvent`** 统一数据类 — 将 AuditLog + ToolDecision + SecurityFinding 归一化为一个模式
- **`SIEMExporter`** ABC — 可插拔的导出接收器接口
- **`WebhookSIEMExporter`** — HTTPS POST（Splunk HEC、Datadog、Elastic、通用 JSON）
- **`SyslogSIEMExporter`** — 通过 TCP/UDP 带可选 TLS 的 RFC 5424
- **`OCSFFormatter`** — OCSF v1.5 模式映射（Activity class 4001、Authorization class 2201、Finding class 2001）
- **`SecurityEventCollector`** — 订阅所有三个源，归一化，应用可配置过滤（事件类型 + 严重级别阈值），路由到注册的导出器
- 配置：`SIEM_ENABLED`、`SIEM_EXPORTERS`、`SIEM_WEBHOOK_URL`、`SIEM_SYSLOG_HOST/PORT/PROTOCOL`、`SIEM_MIN_SEVERITY`、`SIEM_FILTER_EVENT_TYPES`、`SIEM_BATCH_SIZE`、`SIEM_FLUSH_INTERVAL`

## Capabilities — 能力

### 新能力

- `tool-decision-log`：工具策略决策审计 — 从 structured-security-audit 重命名。捕获来自 ToolPolicyPipeline 和 ToolAccessPolicy 的 ALLOW/DENY/SANDBOX 决策。通过异步批量写入器持久化到 `tool_decisions` 表。
- `security-findings`：异常检测发现结果持久化 — 将 FindingEngine 违规（批量删除、非工作时间操作、异常 IP）存储在 `security_findings` 表中。提供 REST 查询 API。替换当前的 `log.warning()` 丢弃模式。
- `siem-export`：SIEM 导出管道 — 将 AuditLog、ToolDecision 和 SecurityFinding 事件统一为归一化的 SecurityEvent 流。通过 Webhook（JSON）、Syslog（RFC 5424）和 OCSF v1.5 格式化器导出。可配置的事件类型和严重级别过滤。

### 修改的能力

- `audit-logs`：将 PolicyEngine 重命名为 FindingEngine，PolicyViolation 重命名为 SecurityFinding，AuditSecurityPolicy 重命名为 DetectionRule，PolicyContext 重命名为 DetectionContext，PolicySeverity 重命名为 FindingSeverity。内置规则重命名（BulkDeleteProtectionPolicy → BulkDeleteRule 等）。FindingEngine 现在将违规持久化到 SecurityFindingModel 而非记录日志并丢弃。

## Impact — 影响

**创建的文件（约 15 个）：**
- `src/hecate/models/tool_decision.py`（从 security_audit.py 重命名）
- `src/hecate/models/security_finding.py`（新建）
- `src/hecate/engine/decision_sink.py`（从 audit_sink.py 重命名）
- `src/hecate/services/security/decision_service.py`（从 audit_service.py 重命名）
- `src/hecate/services/security/finding_service.py`（新建）
- `src/hecate/services/security/siem/event.py`（新建 — SecurityEvent）
- `src/hecate/services/security/siem/exporter.py`（新建 — SIEMExporter ABC）
- `src/hecate/services/security/siem/webhook.py`（新建）
- `src/hecate/services/security/siem/syslog.py`（新建）
- `src/hecate/services/security/siem/ocsf.py`（新建）
- `src/hecate/services/security/siem/collector.py`（新建）
- `src/hecate/api/tool_decisions.py`（从 security_audit.py 重命名）
- `src/hecate/api/security_findings.py`（新建）
- `alembic/versions/xxx_rename_security_audit_to_tool_decisions.py`（迁移）
- `alembic/versions/yyy_add_security_findings.py`（迁移）

**修改的文件（约 10 个）：**
- `src/hecate/core/config.py` — 重命名 AGENT_ENV_AUDIT_* → AGENT_ENV_DECISION_*，添加 SIEM_* 设置
- `src/hecate/engine/policy_pipeline.py` — 更新 emitter 引用
- `src/hecate/engine/tool_access.py` — 更新 emitter 引用
- `src/hecate/engine/workers/tool_worker.py` — 更新 emitter 引用
- `src/hecate/services/audit/policy.py` — 重命名为 finding.py 或保留使用更新后的名称
- `src/hecate/services/audit/service.py` — 更新 FindingEngine 引用
- `src/hecate/main.py` — 更新重命名服务的 DI 注入 + SIEM collector 启动
- `.env.example` — 重命名配置键，添加 SIEM 设置
- `docs/design/security-architecture.md` — 更新命名

**依赖：** 无新依赖。使用现有的 httpx（webhook）、标准库日志记录（syslog）和 Pydantic（事件模型）。
