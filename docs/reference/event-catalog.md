# Event Catalog

Hecate emits events from many subsystems — audit, observability, webhooks, lifecycle, system. This document is a **partial catalog** of event types, their payloads, and consumers.

> **Status**: This catalog is maintained manually and may not be 100% complete. For the **authoritative** catalog, search the source code (`grep -r "event_type" src/hecate/`). For real-time event subscription, see [Observability Architecture](../design/observability-architecture.md).

---

## How to consume events

Events can be consumed four ways:

| Mechanism | Use case | Latency |
|---|---|---|
| **Audit log query** | Compliance review, forensics | Historical |
| **Metrics counter** | Aggregated event counts | Real-time |
| **OTel span** | Per-event trace with attributes | Real-time |
| **Webhook** | External integration (GitHub, Slack) | Real-time |

```bash
# Audit events
hecate audit list --event-type <name> --last-1h

# Metrics for events
curl http://localhost:8000/metrics | grep hecate_<event>

# Trace a specific event
hecate trace list --name <span-name> --last-1h
```

---

## Event categories

Hecate events fall into 7 categories:

| Category | Count | Persisted to |
|---|---|---|
| **Lifecycle** | ~10 | `audit_logs` |
| **Chat** | ~6 | `audit_logs` + span |
| **Workflow** | ~8 | `audit_logs` + span |
| **Tool** | ~5 | `audit_logs` + span |
| **Knowledge** | ~5 | `audit_logs` |
| **Auth** | ~10 | `audit_logs` |
| **System** | ~8 | `audit_logs` + metrics |

This is an **approximate count** — exact numbers depend on the version.

---

## Lifecycle events

| Event name | When | Payload |
|---|---|---|
| `agent.created` | New agent created | `{agent_id, name, mode, workspace_id}` |
| `agent.updated` | Agent config updated | `{agent_id, diff, actor_id}` |
| `agent.deleted` | Agent soft-deleted | `{agent_id, deleted_by}` |
| `session.created` | New session started | `{session_id, agent_id, workspace_id}` |
| `session.interrupted` | Session paused (HITL) | `{session_id, agent_id, interrupt_node}` |
| `session.resumed` | Session resumed after approval | `{session_id, decision, approver_id}` |
| `session.completed` | Session reached terminal state | `{session_id, duration_ms, message_count}` |
| `session.failed` | Session terminated due to error | `{session_id, error_code, error_message}` |
| `workflow.created` | New workflow created | `{workflow_id, name, dsl_hash}` |
| `workflow.versioned` | New workflow version saved | `{workflow_id, version}` |

> **Engine execution events** (the Log-as-Truth event log — `CHANNEL_WRITE`, `STEP_END`, `INTERRUPT`, `EVICTION`, `CHANNEL_WRITE_REJECTED`, `SUBGRAPH_START`/`SUBGRAPH_END`, etc.) are a separate namespace from the business/audit events above. They are the **source of truth for execution state** (replayable, value-carrying) and are documented in [ADR-030](../design/adr/030-event-sourced-execution-state.md) and [Engine Design](../design/engine-design.md#event-store).

---

## Chat events

| Event name | When | Payload |
|---|---|---|
| `chat.requested` | User sends a message | `{session_id, agent_id, message_count, tokens_input}` |
| `chat.started` | Engine begins processing | `{session_id, agent_id}` |
| `chat.llm_called` | LLM API called | `{session_id, agent_id, model, tokens_input, tokens_output, cost_usd, latency_ms}` |
| `chat.tool_called` | Agent invokes a tool | `{session_id, tool_name, args_hash, latency_ms}` |
| `chat.completed` | Agent returns final response | `{session_id, duration_ms, total_tokens, total_cost_usd}` |
| `chat.failed` | Chat failed (LLM error, tool error, etc.) | `{session_id, error_code, error_message, retry_count}` |

---

## Workflow events

| Event name | When | Payload |
|---|---|---|
| `workflow.execution.started` | Run begins | `{execution_id, workflow_id, version, input_hash}` |
| `workflow.execution.superstep` | Each superstep completes | `{execution_id, superstep_n, duration_ms, node_count}` |
| `workflow.execution.completed` | Successful completion | `{execution_id, duration_ms, artifact_count}` |
| `workflow.execution.failed` | Execution failed | `{execution_id, error_node, error_message}` |
| `workflow.execution.canceled` | Canceled by user | `{execution_id, canceled_by, reason}` |
| `workflow.execution.interrupted` | Paused for HITL | `{execution_id, interrupt_node, prompt}` |
| `workflow.execution.paused` | Paused (broker unavailable) | `{execution_id, reason}` |
| `workflow.execution.resumed` | Resumed after pause | `{execution_id}` |

---

## Tool events

| Event name | When | Payload |
|---|---|---|
| `tool.invoked` | Tool called | `{tool_name, source, args_hash, actor_id, latency_ms}` |
| `tool.completed` | Tool returned | `{tool_name, duration_ms, result_size}` |
| `tool.failed` | Tool error | `{tool_name, error_code, error_message}` |
| `tool.policy_blocked` | Tool call blocked by guardrail | `{tool_name, actor_id, guardrail_type, reason}` |
| `tool.timeout` | Tool exceeded timeout | `{tool_name, timeout_ms, attempted_ms}` |

---

## Knowledge events

| Event name | When | Payload |
|---|---|---|
| `kb.created` | Knowledge base created | `{kb_id, name, source, workspace_id}` |
| `kb.document.uploaded` | Document added to KB | `{kb_id, document_id, size_bytes, chunk_count}` |
| `kb.document.deleted` | Document removed | `{kb_id, document_id, deleted_by}` |
| `kb.search_performed` | RAG retrieval executed | `{kb_id, query, top_k, latency_ms, hit_count}` |
| `kb.ingestion_failed` | Document processing failed | `{kb_id, document_id, error_message}` |

---

## Auth events

| Event name | When | Payload |
|---|---|---|
| `auth.login` | User logged in | `{user_id, auth_method, ip, user_agent}` |
| `auth.login_failed` | Login attempt failed | `{attempted_user, ip, reason}` |
| `auth.logout` | User logged out | `{user_id, session_duration_ms}` |
| `auth.token_refreshed` | JWT refreshed | `{user_id, session_id}` |
| `auth.api_key_created` | New API key | `{key_id, name, workspace_id, created_by}` |
| `auth.api_key_revoked` | API key revoked | `{key_id, revoked_by, reason}` |
| `auth.sso_login` | SSO login succeeded | `{user_id, idp, ip}` |
| `auth.sso_failed` | SSO login failed | `{ip, reason}` |
| `auth.rbac_denied` | Authorization failed | `{user_id, resource, action, role}` |
| `auth.password_reset` | Password reset requested | `{user_id, reset_method}` |

---

## System events

| Event name | When | Payload |
|---|---|---|
| `system.started` | Hecate started | `{version, commit, started_at}` |
| `system.stopped` | Hecate stopped cleanly | `{uptime_seconds, last_request_at}` |
| `system.upgraded` | Hecate version changed | `{from_version, to_version, actor_id}` |
| `system.config_changed` | Runtime config changed | `{key, old_value, new_value, actor_id}` |
| `system.feature_flag_toggled` | Feature flag changed | `{flag, old_value, new_value, actor_id}` |
| `system.health_check_failed` | Health check failed | `{check_name, error_message}` |
| `system.budget_triggered` | Budget threshold crossed | `{workspace_id, threshold, current_spend, limit}` |
| `system.webhook.received` | External webhook arrived | `{source, event_type, delivery_id, signature_verified}` |

---

## Security events

| Event name | When | Payload |
|---|---|---|
| `security.guardrail.blocked` | Guardrail blocked an action | `{guardrail_type, actor_id, action_type, reason}` |
| `security.guardrail.alerted` | Guardrail triggered warning | `{guardrail_type, severity, message}` |
| `security.pii.anonymized` | PII detected and masked | `{entity_count, pii_types, context}` |
| `security.injection.detected` | Prompt injection detected | `{llm_id, source, pattern}` |
| `security.dlp.detected` | Outbound DLP detected sensitive data | `{destination, pii_types, action}` |
| `security.signature.failed` | Webhook signature verification failed | `{source, ip, reason}` |
| `security.signature.replayed` | Replay attack detected | `{source, delivery_id, timestamp}` |
| `security.policy.violated` | Policy violated | `{policy_name, actor_id, action}` |

---

## Common event payload fields

Most events include these metadata fields:

| Field | Type | Description |
|---|---|---|
| `event_id` | UUID | Unique event identifier |
| `event_type` | string | Dotted event name (e.g., `chat.llm_called`) |
| `timestamp` | ISO 8601 | When the event occurred (UTC) |
| `workspace_id` | UUID | Workspace context (if applicable) |
| `user_id` | UUID | User who triggered (if applicable) |
| `actor_id` | UUID | System or user actor |
| `session_id` | UUID | Session context |
| `request_id` | UUID | HTTP request that triggered |
| `trace_id` | string | OpenTelemetry trace ID |
| `span_id` | string | OpenTelemetry span ID |

Use these to correlate events across subsystems.

---

## Event subscriptions (real-time)

Subscribe to events in real time via the management API:

```bash
# WebSocket subscription (when available)
wscat -c "ws://localhost:8000/api/events/subscribe?type=chat.llm_called"
```

Or via Server-Sent Events (SSE):

```bash
curl -N http://localhost:8000/api/events/sse?type=chat.llm_called
```

(These endpoints are planned for [1.x].)

For now, use the audit log query API for historical events:

```bash
# All events for a session
curl "http://localhost:8000/api/audit/events?session_id=$SESSION_ID&limit=100" \
  -H "Authorization: Bearer $ADMIN_KEY"

# Specific event type
curl "http://localhost:8000/api/audit/events?event_type=chat.llm_called&last=1h" \
  -H "Authorization: Bearer $ADMIN_KEY"
```

---

## What's NOT in the catalog

This catalog is **not exhaustive**. To find all events:

```bash
# Search source for all event emissions
grep -rn "event_type=" src/hecate/ | grep -v "test_" | head -50

# Or for the canonical list
grep -rn "AuditEventType\|EventType\." src/hecate/models/ | head -30
```

If you discover an event not listed here, please open a PR to update this document.

---

## Related documents

- [Observability Architecture](../design/observability-architecture.md) — the audit pipeline
- [Webhooks concept](../concepts/webhooks.md) — webhook event flow
- [Security Architecture](../design/security-architecture.md) — security events in depth
- [Threat Model](../design/threat-model.md) — events relevant to security monitoring
-  — real-time event streaming (P3)