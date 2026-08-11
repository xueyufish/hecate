# How to Version and Roll Back an Agent or Workflow

Versioning lets you iterate on an agent or workflow safely: every change creates an immutable version, one version is marked `production`, and you can roll back to any prior version by changing the label.

This guide covers the workflow version lifecycle end-to-end: edit, version, publish, roll back, audit.

---

## Prerequisites

- A workflow or agent you want to version (or create one with `hecate workflows create` / `hecate agents create`)
- `editor` or `admin` role on the target Workspace

---

## The version lifecycle

Every workflow and agent follows the same lifecycle:

```
Draft (working copy)
    ↓ save  ──→ creates Version N+1 (immutable)
    ↓ test   ──→ run sessions against any version
    ↓ publish──→ marks Version N+1 as "production"
                demotes previous "production" to no label
    ↓ rollback─→ marks any prior version as "production"
```

Key properties:

| Property | Behavior |
|----------|----------|
| **Immutability** | Once a version is saved, its configuration cannot be changed — only superseded by a new version |
| **Single production** | At most one version of a workflow/agent has the `production` label at any time |
| **Sessions always run** | Sessions reference the version they started with, even if `production` moves — no mid-session drift |
| **Audit trail** | Every save, publish, and rollback is logged with operator identity and timestamp |

---

## Step 1 — Save your first version

When you create a workflow, it starts as an unpublished draft. The first edit-and-save produces Version 1:

```bash
hecate workflows create customer-triage --from-file triage-v1.json
# Working copy is now an unpublished draft.

# Edit it...
hecate workflows edit customer-triage --patch '{"state": {...}, "nodes": {...}}'

# Save as an immutable version
hecate workflows save customer-triage \
  --change-summary "Initial triage flow with guard + escalation"
# → Created Version 1.
```

Each save creates a new version number, monotonically increasing. The change summary appears in the version history for review.

---

## Step 2 — Iterate without affecting production

Make changes freely on the working copy. Sessions started against `production` continue running against Version 1 — your edits do not touch live traffic until you publish.

```bash
hecate workflows edit customer-triage --patch '{"nodes": {...}}'
hecate workflows save customer-triage \
  --change-summary "Add sentiment analysis before escalation"
# → Created Version 2. Version 1 is still production.

hecate workflows edit customer-triage --patch '{"nodes": {...}}'
hecate workflows save customer-triage \
  --change-summary "Tighten escalation threshold"
# → Created Version 3. Versions 1, 2, 3 all preserved.
```

You can run sessions against any version for testing:

```bash
hecate workflows run customer-triage \
  --version 2 \
  --input "Test message for sentiment path"
```

This is how you test a candidate version before publishing it.

---

## Step 3 — Compare versions

Before publishing, look at exactly what changed between two versions:

```bash
hecate workflows diff customer-triage 1 3
```

Output shows the JSON-level diff of `state`, `nodes`, and `edges` between the two versions. Useful for review meetings and change documentation.

For a structured summary of all changes, list the version history:

```bash
hecate workflows versions customer-triage
```

Output:

```json
{
  "versions": [
    { "version": 3, "labels": [], "change_summary": "Tighten escalation threshold", "saved_at": "2026-08-10T14:30:00Z", "saved_by": "alice@example.com" },
    { "version": 2, "labels": [], "change_summary": "Add sentiment analysis before escalation", "saved_at": "2026-08-09T11:00:00Z", "saved_by": "alice@example.com" },
    { "version": 1, "labels": ["production"], "change_summary": "Initial triage flow", "saved_at": "2026-08-08T09:15:00Z", "saved_by": "alice@example.com" }
  ],
  "published_version": 1
}
```

---

## Step 4 — Publish a version

When you are ready to make a version live, publish it:

```bash
hecate workflows publish customer-triage --version 3
```

This atomically:

1. Removes the `production` label from Version 1.
2. Adds the `production` label to Version 3.
3. Updates `published_version` on the workflow to 3.
4. Logs the action to the audit trail with the operator's identity.

New sessions started after this command use Version 3. Sessions already running continue with their original version.

---

## Step 5 — Roll back

If Version 3 misbehaves, roll back to any prior version:

```bash
hecate workflows publish customer-triage --version 1
```

The rollback is identical to publishing — the same command, just with a different version number. The system does not distinguish "publish" from "rollback"; both move the `production` label. This is intentional: there is no special "revert" state, only the current `production` pointer and the history of all versions.

For a faster rollback when you need to halt traffic immediately:

```bash
hecate workflows unpublish customer-triage
```

This removes the `production` label entirely. New sessions will fail with "no published version" until you publish one — useful as a kill switch.

---

## Step 6 — Audit a rollback

After a rollback, review who did what and when:

```bash
hecate workflows audit customer-triage --since "2 hours ago"
```

Output:

```json
{
  "events": [
    { "timestamp": "2026-08-10T16:45:12Z", "actor": "alice@example.com", "action": "publish_version", "version": 1, "previous_production": 3 },
    { "timestamp": "2026-08-10T14:30:00Z", "actor": "alice@example.com", "action": "save_version", "version": 3 },
    { "timestamp": "2026-08-10T11:15:00Z", "actor": "bob@example.com", "action": "publish_version", "version": 3, "previous_production": 1 }
  ]
}
```

Every save, publish, and rollback is here with operator identity and timestamp. This is the audit trail for compliance and post-incident review.

---

## Step 7 — Clean up old versions (optional)

Versions are immutable and persist forever by default. If your workspace accumulates hundreds of versions, you can archive (soft-delete) old ones:

```bash
hecate workflows archive customer-triage --version 2
```

Archived versions:

- Remain in version history (audit-trail-friendly)
- Cannot be run or published
- Can be restored by an admin if needed

This is purely a hygiene operation; it does not affect production traffic.

---

## Edge cases

| Scenario | Behavior |
|----------|----------|
| Publish a version that does not exist | Returns 404 — version numbers are explicit |
| Publish while sessions are running against the prior production | Old sessions continue with the old version; new sessions use the new version |
| Two operators publish different versions concurrently | Last writer wins; the audit trail shows both attempts and their order |
| Restore a deleted workflow | Versions are preserved; restoring brings all of them back |
| Roll back to a version with broken dependencies | The engine validates the version before publishing; if a referenced tool or KB no longer exists, publish fails |

---

## API equivalents

Every CLI command above has a REST equivalent under `/api/workflows/{id}/versions`:

| CLI | REST |
|-----|------|
| `hecate workflows versions <id>` | `GET /api/workflows/{id}/versions` |
| `hecate workflows save <id>` | `POST /api/workflows/{id}/versions` |
| `hecate workflows publish <id> --version N` | `POST /api/workflows/{id}/publish` with `{"version": N}` |
| `hecate workflows unpublish <id>` | `POST /api/workflows/{id}/unpublish` |
| `hecate workflows diff <id> A B` | `GET /api/workflows/{id}/diff?from=A&to=B` |
| `hecate workflows audit <id>` | `GET /api/workflows/{id}/audit` |

The same lifecycle applies to **Agents** via `/api/agents/{id}/versions` — agent versioning covers persona, model config, tools, and knowledge base bindings.

---

## Further reading

- [Core Concepts: ResourceVersion](../design/concepts.md) — the version entity definition
- [Deploy to Production](deploy-production.md#deploy-a-new-version) — how versioning fits blue-green deployment
- [Audit trail](../concepts/guardrails.md) — where the version audit events are written
- [Engine Design: Versioning](../design/concepts.md#versionable-resources) — the unified versioning mechanism