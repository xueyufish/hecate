## ADDED Requirements — 新增需求

### Requirement: Structured security audit event integration — 需求：结构化安全审计事件集成
The existing AuditLog system SHALL be extended to consume SecurityAuditEvent records from the structured security audit pipeline. Security audit events SHALL be queryable via a dedicated REST endpoint separate from the general audit log API.

现有的 AuditLog 系统应扩展为消费来自结构化安全审计管线的 SecurityAuditEvent 记录。安全审计事件应可通过与通用审计日志 API 分离的专用 REST 端点查询。

#### Scenario: Security audit events queryable via dedicated endpoint — 场景：通过专用端点查询安全审计事件
- **WHEN** a client requests `GET /api/security/audit?agent_id={id}`
- **THEN** the system returns SecurityAuditEvent records (not general AuditLog records)
- **AND** the response includes decision, reason, policy_version, and per-layer breakdown

- **当**客户端请求 `GET /api/security/audit?agent_id={id}`
- **则**系统返回 SecurityAuditEvent 记录（不是通用 AuditLog 记录）
- **且**响应包含决策、原因、策略版本和逐层分解

#### Scenario: Security audit events available for SIEM export — 场景：安全审计事件可用于 SIEM 导出
- **WHEN** the SIEM export pipeline is implemented
- **THEN** it SHALL consume SecurityAuditEvent records as its primary data source
- **AND** convert them to CEF/LEEF/JSON format for external SIEM systems

- **当**实现 SIEM 导出管线时
- **则**应消费 SecurityAuditEvent 记录作为其主要数据源
- **且**将它们转换为 CEF/LEEF/JSON 格式用于外部 SIEM 系统
