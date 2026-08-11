# Set Up Webhooks

Configure Hecate to receive events from external systems (GitHub, Slack, custom services) and trigger workflows.

For the conceptual model, see [Webhooks concept](../concepts/webhooks.md). For the GitHub example, see [Use Case: Code Review Agent](../use-cases/02-code-review-agent.md).

---

## What's currently supported

Hecate's webhook system is **partially implemented**. As of 0.1.x:

- ✅ **Webhook receivers** — `POST /webhooks/{source}` accepts events from any source
- ✅ **Signature verification** — HMAC-SHA256 + 5-minute timestamp window
- ✅ **Normalized event format** — internal representation of any source's events
- ✅ **Webhook-to-workflow binding** — events trigger configured workflows
- ❌ **Public webhook receiver UI** — configuration is via API/CLI only (no canvas UI yet)
- ❌ **Webhook marketplace** — only community-contributed config (post-1.0)

If you need a full webhook UI + marketplace, that's coming in post-1.0.

---

## What you can configure

This guide covers:

- **Receiving webhooks** from external sources
- **Signature verification** setup
- **Binding events to workflows**
- **Testing** webhook delivery
- **Handling failures** (retries, dead-letter queue)

---

## Step 1 — Choose your webhook source

Hecate accepts webhooks from any source but ships with built-in parsers for common ones:

| Source | Path | Events supported |
|---|---|---|
| **GitHub** | `POST /webhooks/github` | `pull_request.*`, `push`, `issues.*`, `release.*` |
| **Slack** | `POST /webhooks/slack` | `message`, `app_mention`, `reaction_added` |
| **Generic JSON** | `POST /webhooks/{name}` | Any event (raw JSON passthrough) |
| **Custom** | `POST /webhooks/{name}` | Self-registered via plugin |

For other sources, configure a **generic** webhook with custom event parsing.

---

## Step 2 — Configure the receiving secret

Hecate verifies signatures using a **shared secret** known to both sender and receiver. Configure per source:

```bash
# Set via env var (recommended for production)
export GITHUB_WEBHOOK_SECRET="abc123_your_secret_here"
export SLACK_WEBHOOK_SECRET="xyz789_another_secret"
```

Or via the configuration file:

```toml
# webhooks.toml
[webhooks.github]
secret = "abc123_your_secret_here"
events = ["pull_request.opened", "pull_request.synchronize"]
workflow = "code-review"
workspace_filter = ["xueyufish/*"]

[webhooks.slack]
secret = "xyz789_another_secret"
events = ["message", "app_mention"]
workflow = "slack-router"
```

Restart Hecate to pick up changes:

```bash
docker compose restart api
```

Or pass via Kubernetes ConfigMap reload (if you're using Hecate's Helm chart).

### Secret generation

Generate a strong secret:

```bash
openssl rand -hex 32
# a4f8e9b2c1d7e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8
```

Use **different secrets per source** so a leaked Slack secret doesn't compromise GitHub auth.

---

## Step 3 — Configure the sender (GitHub example)

In GitHub:

1. Go to **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://hecate.example.com/webhooks/github`
3. **Content type**: `application/json`
4. **Secret**: paste the secret from Step 2
5. **Which events**: select "Let me select individual events" → enable "Pull requests" + "Pushes"
6. **Active**: checked
7. Click **Add webhook**

GitHub will send a `ping` event to verify. Hecate returns 200 if the secret is correct.

### Verify the webhook

```bash
# GitHub sends a ping event on creation
# You should see this in Hecate's audit log:
hecate audit list --event_type webhook.received --last-5m

# Expected:
# {
#   "event_type": "webhook.received",
#   "source": "github",
#   "event_name": "ping",
#   "delivery_id": "abc-123",
#   "signature_verified": true,
#   "timestamp": "2026-08-11T14:23:00Z"
# }
```

If you see `signature_verified: false`, check that the secret matches exactly (whitespace, encoding).

---

## Step 4 — Bind events to a workflow

The webhook source is configured to trigger a specific workflow. Set this in `webhooks.toml`:

```toml
[webhooks.github]
secret = "abc123_your_secret_here"
events = ["pull_request.opened", "pull_request.synchronize"]
workflow = "code-review"      # ← Hecate workflow to trigger
workspace_filter = ["xueyufish/*"]  # Optional: only process events from these repos
```

When the event arrives, Hecate:

1. Verifies signature
2. Checks timestamp (reject if > 5 min old)
3. Parses the event via the source-specific parser
4. Filters by configured event types
5. Filters by workspace/repo (if configured)
6. Triggers the workflow with the normalized payload as input

---

## Step 5 — Test the webhook delivery

### Using curl (manual test)

Simulate a webhook locally:

```bash
# Compute the signature
SECRET="abc123_your_secret_here"
PAYLOAD='{"event_type":"pull_request.opened","action":"opened","pull_request":{"number":42}}'
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
SIGNATURE=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$SECRET" -hex | awk '{print $2}')

# Send to Hecate
curl -X POST http://localhost:8000/webhooks/github \
  -H "Content-Type: application/json" \
  -H "X-Hecate-Signature: sha256=$SIGNATURE" \
  -H "X-Hecate-Timestamp: $TIMESTAMP" \
  -H "X-Hecate-Delivery-Id: test-001" \
  -d "$PAYLOAD"
```

Expected response:
```json
{
  "status": "accepted",
  "delivery_id": "test-001",
  "workflow": "code-review",
  "execution_id": "exec_..." 
}
```

### Using the CLI

```bash
# Trigger a test webhook
hecate webhook test github \
  --event pull_request.opened \
  --payload ./fixtures/pr-opened.json

# List recent webhook deliveries
hecate webhook list --last-1h

# Show delivery details
hecate webhook show <delivery-id>
```

---

## Step 6 — Verify the workflow ran

After delivering a webhook, the workflow runs asynchronously:

```bash
# Watch workflow execution
hecate workflow execution list --workflow code-review --last-5m

# Get specific execution details
hecate workflow execution get <execution-id>

# Check status
# Expected: status=completed, duration=~30s, artifacts=2
```

The execution is logged in the audit trail and visible in the Ops Center dashboard.

---

## Step 7 — Handle failures

### Retry policy

Hecate retries failed webhook deliveries 5 times:

| Attempt | Delay | Backoff |
|---|---|---|
| 1 | Immediate | — |
| 2 | 30s | Constant |
| 3 | 2min | Constant |
| 4 | 10min | Constant |
| 5 | 1hr | Constant |

After 5 failures, the event goes to the **dead-letter queue**.

### Dead-letter queue

```bash
# List failed events
hecate webhook list --status failed --last-24h

# Show failure details
hecate webhook show <delivery-id>

# Replay manually
hecate webhook replay <delivery-id>
```

### Dead-letter schema

Each failed event is stored in `dead_letter_events` table:

```sql
SELECT id, source, event_type, last_error, last_attempt_at, retry_count
FROM dead_letter_events
WHERE workspace_id = ws_eng
ORDER BY last_attempt_at DESC;
```

---

## Source-specific setup

### GitHub

Follow [Step 3](#step-3--configure-the-sender-github-example) above. Hecate auto-detects GitHub events and parses them into the normalized format.

**Required headers** (GitHub sends):
- `X-GitHub-Event` — event type (e.g., `pull_request`)
- `X-GitHub-Delivery` — unique delivery ID
- `X-Hub-Signature-256` — GitHub-style signature (Hecate accepts this format)

Events supported:
- `push` → `git.push`
- `pull_request.opened` → `git.pull_request.opened`
- `pull_request.synchronize` → `git.pull_request.synchronize`
- `issues.opened` → `git.issue.opened`
- `release.published` → `git.release.published`

### Slack

In Slack:

1. Go to **api.slack.com/apps → Your App → Event Subscriptions**
2. **Enable Events**: ON
3. **Request URL**: `https://hecate.example.com/webhooks/slack` (Slack will verify)
4. **Subscribe to bot events**: `message.channels`, `app_mention`
5. Save

Hecate parses Slack events into a normalized format with `text`, `user`, `channel`, `thread_ts`.

### Custom source

For any custom service:

```bash
# Register the webhook source
hecate webhook source create custom \
  --path "/webhooks/my-service" \
  --secret "$MY_SERVICE_SECRET" \
  --events raw \
  --workflow my-workflow

# Your service sends raw JSON
curl -X POST https://hecate.example.com/webhooks/my-service \
  -H "Content-Type: application/json" \
  -H "X-Hecate-Signature: sha256=$SIGNATURE" \
  -H "X-Hecate-Timestamp: $TIMESTAMP" \
  -H "X-Hecate-Delivery-Id: $UNIQUE_ID" \
  -d '{"my_custom_field": "value"}'
```

The workflow receives the raw JSON as `payload`.

---

## Workspace filtering

If you have multiple repos in a workspace, filter by repository:

```toml
[webhooks.github]
secret = "..."
events = ["pull_request.*"]
workflow = "code-review"
workspace_filter = ["xueyufish/hecate", "xueyufish/hecate-cli"]
# Only events from these repos trigger the workflow
```

Multiple repos = list of patterns. Glob patterns supported (`*`).

---

## Security considerations

### Signature rotation

Rotate secrets periodically without downtime using **two-secret overlap**:

```bash
# Step 1: Add new secret alongside old
hecate webhook source update github \
  --secret "old_secret" \
  --rotation-secret "new_secret"

# Step 2: Update GitHub to use new secret

# Step 3: Remove old secret
hecate webhook source update github \
  --secret "new_secret"
```

During the overlap, both signatures are accepted.

### IP allowlist

Optionally restrict which IPs can send webhooks:

```toml
[webhooks.github]
secret = "..."
ip_allowlist = ["140.82.112.0/20", "185.199.108.0/22"]  # GitHub's IP ranges
```

GitHub publishes their webhook IP ranges at <https://api.github.com/meta>.

### Timestamp validation

Hecate rejects requests with timestamps older than 5 minutes. This prevents replay attacks. Configure tighter if needed:

```bash
hecate webhook source update github \
  --max-timestamp-age-seconds 60
```

---

## Monitoring

```bash
# Recent webhook deliveries
hecate webhook list --last-1h

# Failed deliveries
hecate webhook list --status failed --last-24h

# Metrics
curl http://localhost:8000/metrics | grep hecate_webhooks

# Per-source breakdown
hecate webhook stats --source github --last-7d
```

Each delivery is also tracked as an OTel span:

```
[trace] webhook.received (source=github, event=pull_request.opened)
  └── [trace] workflow.execute (workflow=code-review)
       └── [trace] agent.invoke (agent=reviewer-1)
            └── [trace] llm.call (model=gpt-4o)
```

---

## Troubleshooting

### Webhook not received

If your webhook source shows 0 deliveries:

```bash
# 1. Check the receiving endpoint is reachable
curl -X POST https://hecate.example.com/webhooks/github \
  -H "Content-Type: application/json" \
  -d '{}'

# Expected: 401 (no signature) or 400 (bad payload)

# 2. Check the audit log
hecate audit list --event_type webhook.received --last-1h

# 3. Check network connectivity from sender
# From a server with GitHub IPs:
curl -I https://hecate.example.com/webhooks/github
```

### Signature verification fails

```bash
# 1. Check the secret matches exactly
hecate webhook source show github | jq .secret_fingerprint
# Compare with sender's configured secret (not the secret itself, just the fingerprint)

# 2. Check timestamp validation
hecate webhook source update github --max-timestamp-age-seconds 600

# 3. Check for clock skew
# Sender and Hecate must be within 5 minutes of UTC
date -u
```

### Workflow doesn't trigger

```bash
# 1. Verify the binding
hecate webhook source show github | jq .workflow

# 2. Check the event type matches
# In webhooks.toml: events = ["pull_request.opened"]
# GitHub sends: pull_request.opened  ✓
# GitHub sends: pull_request.synchronize  ✗ (not in list)

# 3. Check the workflow exists
hecate workflow get code-review

# 4. Test the workflow directly
hecate workflow run code-review --input '{"pr_number": 42}'
```

### Rate-limited by Hecate

Each source has a default rate limit (1000 events/minute). If exceeded:

```bash
# Check if you're hitting limits
hecate webhook stats --source github --last-1h | grep rate_limit

# Increase the limit
hecate webhook source update github \
  --rate-limit-per-minute 5000
```

---

## What's NOT implemented

| Feature | Status |
|---|---|
| **Webhook UI in canvas** | [1.x] |
| **Webhook marketplace** | [post-1.0] |
| **Bidirectional webhook negotiation** | Not planned |
| **Public webhook discovery endpoint** | Not planned |
| **Replay UI for dead-letter events** | CLI only; UI in P3 |

---

## Related documents

- [Webhooks concept](../concepts/webhooks.md) — conceptual model
- [Use Case: Code Review Agent](../use-cases/02-code-review-agent.md) — full GitHub example
- [Threat Model](../design/threat-model.md) — webhook security analysis
- [Troubleshooting](troubleshoot.md) — common failure modes
-  — webhook UI and marketplace timeline