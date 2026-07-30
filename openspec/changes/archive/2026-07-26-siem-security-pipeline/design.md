## Context — 背景

Hecate 有三个独立的安全审计来源，它们在不同时期演变而来：

1. **AuditLog**（P2，`services/audit/`）— 通过 `AuditMiddleware` 进行的 API 级审计。捕获每个 HTTP 请求。分区 PostgreSQL 表。具有检测异常（批量删除、非工作时间、异常 IP）的 `PolicyEngine`，但仅 `log.warning()` — 违规信息丢失。

2. **SecurityAudit**（9.14，`models/security_audit.py` + `engine/audit_sink.py` + `services/security/audit_service.py`）— 工具策略决策审计。每次 `ToolPolicyPipeline` 和 `ToolAccessPolicy` 评估都会发出结构化事件。异步批量写入器。刚刚合并，没有外部消费者。

3. **TraceModel**（OTel 桥接）— 操作跨度，不直接与安全相关，但提供延迟和工具执行遥测。

这些都无法导出到外部 SIEM 系统（Splunk、Datadog、Elastic、QRadar）。企业 SOC 团队对 Hecate 安全事件没有可见性。命名也令人困惑 — "SecurityAudit" 与 "AuditLog" 在名称上无法区分，"Policy" 被过度使用（ToolPolicyPipeline、ToolAccessPolicy、PolicyEngine 分别表示三种不同的含义）。

**行业命名模式：**
- AWS：CloudTrail（活动）/ GuardDuty Finding（异常）/ Security Hub（聚合）
- OCSF：Activity（class 4001）/ Authorization（class 2201）/ Security Finding（class 2001）
- Kubernetes：Audit Log / Admission Decision / Policy Violation

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 将 SecurityAudit 重命名为 ToolDecision，PolicyEngine 重命名为 FindingEngine，消除混淆
- 持久化 PolicyEngine 发现结果（目前通过 `log.warning()` 丢失）
- 构建统一的 SIEM 导出管道，支持 Webhook + Syslog + OCSF
- 可配置的事件过滤（按类型和严重级别阈值）
- 向后兼容的默认值（SIEM 导出默认禁用，无破坏性运行时行为）

**非目标：**
- 基于 SIEM 反馈的实时阻断（SIEM 仅用于观察）
- Kafka/消息队列导出（推迟到 P1 — webhook + syslog 覆盖 90% 的部署）
- OTel Logs OTLP 导出（推迟到 P2 — 需要 OTel collector 设置）
- Hecate 内部的自定义 SIEM 关联规则（外部 SIEM 进行关联）
- 将 AuditLog 和 ToolDecision 合并为单个表（模式不同，查询模式不同）

## Decisions — 决策

### D1：命名 — 三层对齐行业标准

| 层 | 旧名称 | 新名称 | 行业类比 |
|-------|----------|----------|-----------------|
| API 操作 | AuditLog | AuditLog（不变） | AWS CloudTrail、OCSF Activity |
| 工具决策 | SecurityAudit | **ToolDecision** | OCSF Authorization、K8s Admission Decision |
| 异常检测 | PolicyEngine + PolicyViolation | **FindingEngine + SecurityFinding** | AWS GuardDuty Finding、OCSF Finding |
| 导出 | （无） | **SIEM Export Pipeline** | AWS Security Hub |

**理由：** "SecurityAudit" 具有误导性 — 它仅捕获工具策略决策，而非通用安全事件。"ToolDecision" 更精确。"PolicyEngine" 与 ToolPolicyPipeline/ToolAccessPolicy 过度重叠。"FindingEngine" 与 GuardDuty Finding、Defender Alert、OCSF Finding class 对齐。

**考虑的替代方案：** 保留名称，记录差异。拒绝 — 文档无法修复出现在代码、API 路径、配置键和数据库表中的命名混淆。

### D2：统一 SecurityEvent — 在导出层而非存储层归一化

```
AuditLog（PG 表）──┐
ToolDecision（PG 表）──→ SecurityEventCollector ──→ SIEMExporter(s)
SecurityFinding（PG 表）──┘    （归一化 + 过滤）
```

每个源保留自己的表和模式。收集器从所有三个源读取并归一化为 `SecurityEvent` 数据类，仅用于导出。

**理由：** 不同的源具有不同的字段（API 日志有 HTTP 方法/路径；工具决策有 layer_results/policy_version；发现结果有 severity/metadata）。强制将它们放入一个表会失去类型特定的查询能力，或需要宽稀疏表。

**考虑的替代方案：** 带 `event_type` 鉴别器的单个 `security_events` 表。拒绝 — AuditLog 已分区且在生成中；迁移风险大于收益。

### D3：推送架构带异步批处理

```
源事件 ──→ SecurityEventCollector.emit()（非阻塞，内存缓冲区）
                │
                ▼（每 batch_size 或 flush_interval）
        SIEMExporter.export(events: list[SecurityEvent])
```

收集器缓冲事件并分批刷新 — 与现有 `SecurityAuditService` 异步批量写入器相同的模式。每个导出器接收批次并发送到其目标（webhook HTTP POST、syslog TCP 流等）。

**理由：** 非阻塞发射防止 SIEM 导出影响请求延迟。批处理分摊网络 I/O。与审计批量写入器相同的经过验证的模式。

**考虑的替代方案：** 实时每个事件流式传输。拒绝 — 高流量部署的网络开销高，且 SIEM 系统偏好批量摄入。

### D4：P0 中的三个导出器

| 导出器 | 目标 | 协议 | 用例 |
|----------|--------|----------|----------|
| **WebhookSIEMExporter** | Splunk HEC、Datadog、Elastic、通用 | HTTPS POST 带 JSON 体 | 云 SIEM，最常见 |
| **SyslogSIEMExporter** | QRadar、ArcSight、rsyslog | RFC 5424 通过 TCP/UDP + TLS | 本地部署企业 |
| **OCSFFormatter** | AWS CloudWatch、IBM、下一代 | 带 OCSF v1.5 模式的 JSON | 标准合规导出 |

OCSFFormatter 是一个格式化器，而不是传输器 — 它包装另一个导出器（通常是 webhook）以生成符合 OCSF 的 JSON。

**理由：** Webhook 覆盖云 SIEM（90% 的现代部署）。Syslog 对本地部署企业至关重要。OCSF 映射工作量小（只是 JSON 模式）且为行业标准化提供了未来保障。

**考虑的替代方案：** 仅从 webhook 开始。拒绝 — 用户明确要求全部三个在范围内。

### D5：可配置的过滤 — 事件类型 + 严重级别阈值

```python
SIEM_FILTER_EVENT_TYPES = "api,tool_policy,anomaly"  # 逗号分隔，默认：全部
SIEM_MIN_SEVERITY = "info"  # info | low | medium | high | critical，默认：info（全部）
```

**严重级别映射（内置默认值）：**

| 事件类型 | 默认严重级别 |
|------------|-----------------|
| API 成功（2xx） | INFO |
| API 客户端错误（4xx） | LOW |
| API 服务端错误（5xx） | MEDIUM |
| ToolDecision ALLOW | INFO |
| ToolDecision SANDBOX | MEDIUM |
| ToolDecision DENY | HIGH |
| ToolDecision APPROVAL_REQUIRED | MEDIUM |
| SecurityFinding（低） | LOW |
| SecurityFinding（中） | MEDIUM |
| SecurityFinding（高） | HIGH |
| SecurityFinding（严重） | CRITICAL |

**理由：** 并非每个成功的 API GET 都需要进入 SIEM。可配置的过滤减少噪音和 SIEM 摄入成本。

### D6：SecurityFinding 持久化 — 新表，不复用 AuditLog

```sql
CREATE TABLE security_findings (
    id UUID PRIMARY KEY,
    org_id UUID NOT NULL,
    workspace_id UUID,
    user_id UUID,
    rule_name VARCHAR(100) NOT NULL,    -- 例如 "bulk_delete_rule"
    severity VARCHAR(20) NOT NULL,      -- low | medium | high | critical
    message TEXT NOT NULL,
    source_event JSON,                  -- 触发的 AuditEvent
    metadata JSON DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL
);
```

**理由：** 发现结果具有与 AuditLog 不同的字段（rule_name、severity、source_event）。复用 AuditLog 需要可空列或宽表。单独的表允许专用查询 API 和保留策略。

**考虑的替代方案：** 将发现结果存储为带有 `action="security.finding"` 的 AuditLog 条目。拒绝 — 失去严重级别索引和 rule_name 过滤。

### D7：迁移 — 通过 Alembic 重命名表

两个迁移：
1. 将 `security_audit_events` 重命名为 `tool_decisions`（Alembic `rename_table`）
2. 创建 `security_findings` 表

无需数据迁移 — 列名保持相同（仅表名更改）。配置文件键重命名在 `.env.example` 中带有向后兼容的别名（如果设置了 `AGENT_ENV_AUDIT_ENABLED` 而 `AGENT_ENV_DECISION_ENABLED` 未设置，则回退使用它）。

## Risks / Trade-offs — 风险 / 权衡

- **[命名重构破坏 9.14 代码]** → 9.14 几天前才合并，没有外部消费者，没有引用旧名称的已发布 API 文档。迁移仅限代码内部。
- **[Syslog 可靠性]** → UDP 有损；TCP 增加背压。缓解：TCP 默认，可配置重试，缓冲区溢出时丢弃最旧条目并记录 WARNING。
- **[Webhook 端点宕机]** → 网络故障到 SIEM 不应影响 Hecate。缓解：导出器捕获所有异常，记录错误，继续运行。失败的批次被丢弃（不重试）以防止无界内存增长。重试/队列是 P1 增强。
- **[OCSF 模式正确性]** → OCSF v1.5 复杂；我们的映射可能遗漏必需字段。缓解：仅映射核心字段（timestamp、severity、actor、action、resource），将平台特定数据放在 `metadata` 扩展中。在测试中根据 OCSF JSON 模式验证。
- **[配置重命名混淆]** → 在 `.env` 中有 `AGENT_ENV_AUDIT_ENABLED=true` 的用户可能未注意到重命名。缓解：配置加载器中的向后兼容别名。

## Migration Plan — 迁移计划

1. **阶段 1 — 重命名（无行为变更）：** 在代码中将所有 SecurityAudit 重命名为 ToolDecision。通过 Alembic 重命名表。添加配置别名。所有现有测试使用更新后的名称通过。
2. **阶段 2 — 发现结果持久化：** 添加 SecurityFindingModel + FindingEngine 持久化。FindingEngine 现在写入数据库而非 `log.warning()`。
3. **阶段 3 — SIEM 管道：** 添加 SecurityEvent + Collector + Exporters。默认禁用。对现有行为无影响。

**回滚：** 撤销分支。Alembic 迁移可以降级（`alembic downgrade -1`）。配置别名确保旧的 `.env` 文件仍然有效。

## Open Questions — 开放问题

无 — 所有设计决策在探索阶段已确认。
