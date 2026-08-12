# Webhooks

**Webhooks** are Hecate's way to receive **asynchronous events** from external systems — GitHub, Slack, PagerDuty, or any service that supports HTTP callbacks. They enable event-driven workflows where Hecate reacts to events happening elsewhere.

This document explains the **conceptual model**: what webhooks are, how they differ from SSE and long-polling, and how Hecate implements them. For practical setup, see [How-to: Webhook integration](../how-to/) (when written). For the GitHub webhook example, see [Use Case: Code Review Agent](../use-cases/02-code-review-agent.md).

---

## What is a webhook

A webhook is an **HTTP POST from one service to another** in response to an event. The receiver doesn't poll — the sender pushes.

```
┌────────────┐                  ┌────────────┐
│  GitHub    │   POST /webhook  │  Hecate   │
│            │ ────────────────▶│            │
│  (sender)  │                  │ (receiver) │
└────────────┘                  └────────────┘
       │                               │
       │   Event: PR opened             ▼
       │                          Process event
       │                          Update workflow
       │                          Trigger agent
```

Key properties:

- **Asynchronous** — sender doesn't wait for response
- **Push-based** — receiver doesn't poll
- **HTTP** — universal transport
- **One-way** — receiver can't respond with rich data (just HTTP 200)

---

## Webhooks vs SSE vs long-polling

Three ways to receive async events:

| | Webhook | SSE | Long-polling |
|---|---|---|---|
| **Direction** | Push (sender → receiver) | Push (server → client) | Pull (client → server) |
| **Transport** | HTTP POST | HTTP streaming | HTTP polling |
| **Connection** | Short-lived per event | Long-lived stream | Short-lived per request |
| **Browser-friendly** | ❌ (needs server) | ✅ | ✅ |
| **Server-to-server** | ✅ | ⚠️ (often blocked) | ✅ |
| **Reliability** | ⚠️ (needs retry) | ⚠️ (can drop) | ✅ (client retries) |
| **Throughput** | High | High | Low |
| **Use case** | External integration | Live UI updates | Browser fallback |

**When to use which**:

| Scenario | Use |
|---|---|
| GitHub PR opened → run code review | **Webhook** |
| Slack message → show in canvas (live) | **SSE** |
| Browser polling for new emails | **Long-polling** |
| Internal service-to-service async | **Webhook** |
| Real-time dashboard updates | **SSE** |
| Fallback when SSE blocked | **Long-polling** |

---

## Hecate's webhook protocol

Hecate accepts webhooks via a standardized protocol, regardless of source:

### Request format

```http
POST /webhooks/{source}
Content-Type: application/json
X-Hecate-Signature: sha256=abc123...
X-Hecate-Timestamp: 2026-08-11T14:23:00Z
User-Agent: GitHub-Hookshot/abc123

{
  "event_type": "pull_request.opened",
  "delivery_id": "abc-123-xyz",
  "payload": {
    "action": "opened",
    "pull_request": {"number": 42, "title": "..."},
    "repository": {"full_name": "xueyufish/hecate"}
  }
}
```

### Signature verification

To prevent forgery, Hecate requires the sender to sign requests:

```python
# Sender side (e.g., GitHub)
import hmac
import hashlib

signature = hmac.new(
    key=WEBHOOK_SECRET.encode(),
    msg=request.body,
    digestmod=hashlib.sha256,
).hexdigest()
# X-Hecate-Signature: sha256=<signature>
```

```python
# Receiver side (Hecate)
import hmac
import hashlib

expected = hmac.new(
    key=stored_secret.encode(),
    msg=request.body,
    digestmod=hashlib.sha256,
).hexdigest()

if not hmac.compare_digest(expected, signature_from_header):
    raise HTTPException(401, "Invalid signature")
```

**Shared secret** is configured per webhook source. Rotation is supported (two-secret overlap).

### Timestamp validation

`X-Hecate-Timestamp` prevents replay attacks. Requests older than 5 minutes are rejected.

---

## Webhook sources

Hecate supports webhooks from any source via a **plugin model**:

| Source | Path | Notes |
|---|---|---|
| **GitHub** | `/webhooks/github` | PR, push, issue, release events |
| **Slack** | `/webhooks/slack` | message, app_mention, reaction events |
| **PagerDuty** | `/webhooks/pagerduty` | incident lifecycle events |
| **Jira** | `/webhooks/jira` | issue, sprint events |
| **Custom** | `/webhooks/{name}` | Self-registered via plugin |

Each source has a **specific event parser** that converts the source's event format into Hecate's normalized event format:

```python
# Normalized event (Hecate's internal format)
{
  "event_type": "github.pull_request.opened",
  "source": "github",
  "delivery_id": "abc-123",
  "timestamp": "2026-08-11T14:23:00Z",
  "payload": {
    "pr_number": 42,
    "repo": "xueyufish/hecate",
    "title": "...",
    "author": "alice",
    "files_changed": [...],
  },
  "metadata": {
    "signature_verified": true,
    "retry_count": 0,
  }
}
```

Once normalized, the event can trigger any workflow.

---

## Webhook-to-workflow binding

A webhook source is bound to a workflow via configuration:

```yaml
# .env or webhooks.yaml
webhooks:
  github:
    secret: ${GITHUB_WEBHOOK_SECRET}
    events:
      - pull_request.opened
      - pull_request.synchronize
    workflow: code-review    # Hecate workflow to trigger
    workspace_filter: ["xueyufish/*"]
    
  slack:
    secret: ${SLACK_WEBHOOK_SECRET}
    events:
      - message
      - app_mention
    workflow: slack-router
```

When a webhook arrives, Hecate:

1. Verifies the signature
2. Parses the event via the source-specific parser
3. Filters by configured events
4. Filters by workspace (if specified)
5. Triggers the configured workflow with the normalized payload as input

---

## Reliability: retries and dead-letter queues

Webhooks can fail. Hecate handles this with:

### Retry policy

| Attempt | Delay | Backoff |
|---|---|---|
| 1 | Immediate | — |
| 2 | 30s | Constant |
| 3 | 2min | Constant |
| 4 | 10min | Constant |
| 5 | 1hr | Constant |
| 6 → dead-letter | — | (no more retries) |

Maximum 5 retries. Total time: ~1.5 hours. After exhaustion, event goes to dead-letter queue.

### Dead-letter queue

Failed events are stored in the `dead_letter_events` table:

```sql
SELECT id, source, event_type, last_error, last_attempt_at
FROM dead_letter_events
WHERE workspace_id = ws_eng
ORDER BY last_attempt_at DESC;
```

Replay manually:

```bash
hecate webhook replay <dead-letter-id>
```

Or via the API:

```bash
POST /api/webhooks/dead-letter/{id}/replay
```

---

## Idempotency

Webhook senders retry on failure. Receivers must be **idempotent** — handling the same event twice should not cause double effects.

Hecate uses the `X-Hecate-Delivery-Id` (or equivalent) header for dedup:

```sql
-- Before processing, check if this delivery_id was already seen
SELECT 1 FROM processed_webhooks
WHERE delivery_id = ?
  AND processed_at > NOW() - INTERVAL '24 hours';
```

If found, return 200 without re-processing. If not, process and record.

---

## Security considerations

Webhook endpoints are **publicly accessible** (otherwise senders can't call them). This makes them a potential attack surface.

| Threat | Mitigation |
|---|---|
| **Forged events** | Signature verification (HMAC-SHA256) |
| **Replay attacks** | Timestamp validation (5-min window) |
| **DoS** | Rate limiting per source IP |
| **Secret leakage** | Webhook secrets stored in env vars / secrets manager, not DB |
| **Payload injection** | All fields validated against source-specific schema |

See [Threat Model](../design/threat-model.md) for the full security analysis.

---

## Webhook for triggering Hecate workflows

The most common use case: external event triggers a Hecate workflow.

```
GitHub PR opened
       ↓
POST /webhooks/github
       ↓
Hecate webhook receiver
       ↓
Verify signature → Parse → Filter → Trigger workflow
       ↓
Workflow: code-review
       ↓
Generator: Researcher agent (PR context)
Reviewer: Code style checker
Reviewer: Security scanner
Aggregator: PR comment
       ↓
POST /repos/.../issues/{pr}/comments
```

See [Use Case: Code Review Agent](../use-cases/02-code-review-agent.md) for the complete implementation.

---

## Webhook from Hecate to external systems

Hecate can also **send** webhooks to other systems (not just receive). Use cases:

- **Notify Slack** when a long-running agent completes
- **Trigger PagerDuty** when SLO violated
- **Update CRM** when a customer event happens in Hecate

Configure via **Outbound webhook** in the workflow:

```yaml
# In workflow definition
nodes:
  - ...
  - type: webhook
    config:
      method: POST
      url: https://hooks.slack.com/services/...
      body:
        text: "Agent ${agent.name} completed task ${task.id}"
      retry_policy: 3x-exponential
```

---

## Webhook observability

Every webhook event is:

- **Logged** (structured log with delivery_id, source, event_type)
- **Traced** (OTel span linked to the workflow execution)
- **Audited** (workspace, source, success/failure)
- **Metered** (`hecate_webhooks_received_total{source,event_type,status}`)

```bash
# Recent webhook events
hecate webhook list --last-1h

# Failed events
hecate webhook list --status failed --last-24h

# Metrics
curl http://localhost:8000/metrics | grep hecate_webhooks
```

---

## What's NOT in Hecate webhooks

| Feature | Status |
|---|---|
| **Bidirectional webhook negotiation** | Receiver only; consider MCP for bidirectional |
| **Webhook marketplace** | Not planned; use OAuth provider instead |
| **Public webhook discovery** | Endpoints are not-listed; sender configures manually |
| **Webhook replay UI** | CLI only; UI in P3 |

---

## Related documents

- [Use Case: Code Review Agent](../use-cases/02-code-review-agent.md) — full implementation example
- [Tools, MCP, and A2A](tools-and-mcp.md) — when to use webhooks vs MCP events
- [Observability](observability.md) — webhook metrics and tracing
- [Threat Model](../design/threat-model.md) — webhook security analysis
-  — webhook replay UI timeline