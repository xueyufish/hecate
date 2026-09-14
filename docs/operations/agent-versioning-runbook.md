# Agent Versioning & Channel Publishing — Ops Runbook (1.3.20)

## Upgrade prerequisites

### IM deployments MUST create channel rows before upgrading

Before rolling out this version, every running IM instance (Feishu / Slack
adapter) needs an `im`-type publishing channel row; otherwise inbound IM
messages are **rejected** (logged, never executed) after the upgrade.

For each IM app instance, create the channel (as a workspace admin):

```bash
curl -X POST https://<host>/api/channels \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "feishu-prod",
    "type": "im",
    "agent_id": "<agent-uuid>",
    "bind_mode": "published",
    "config": {"provider": "feishu"}
  }'
```

- `config.provider` MUST equal the adapter name serving the webhook
  (`feishu`, `slack`, ...).
- The target agent must have at least one **published** version — commit
  and publish first (`POST /api/agents/{id}/versions/commit`, then
  `POST /api/agents/{id}/publish/{version}`).
- Use `bind_mode: "pinned"` with `pinned_version` if the instance should
  NOT follow new publishes automatically.

## Behavior changes (BREAKING)

1. **Workflow runtime serves the published version.** Non-test execution
   paths now load `published_version` instead of the highest version.
   Workflows that were never published keep running their latest version
   (unchanged). Workflows that were published but kept evolving: the
   editor test-run still exercises the draft, everything else runs the
   published graph.
2. **IM messages without a configured channel are rejected.** Previously
   they fell through to a zero-UUID placeholder agent (execution failed
   downstream). They are now dropped with an explicit error log entry
   naming the provider — see the prerequisite above.

## Rollback

Backend revert + `alembic downgrade -2` (drops `channels`,
`agent_versions`, and the `agents.published_version` / `evaluation_gate`
columns). Version and channel rows are rebuildable; no other data
depends on them.

## Known notes

- `IMSessionRouter` (`channel/gateway/im_session_router.py`) still carries
  a legacy zero-UUID `default_agent_id` fallback but has no production
  caller — the live path is webhook → `ChannelPublishingService` → bus.
  Do not wire it without replacing the fallback.
- Version immutability is **configuration-level**. Skills/tools resolve
  live; drift is detectable per version via
  `GET /api/agents/{id}/versions/{v}/drift`.
