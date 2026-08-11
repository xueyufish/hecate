# Data Model Reference

Every Hecate entity persists to PostgreSQL via SQLAlchemy 2.0 async ORM models. This page is a map of all 67 tables, grouped by domain, with the key relationships that govern how data flows between them.

> For entity-level definitions (fields, types, constraints), see the [Core Concepts design doc](../design/concepts.md) or the interactive Swagger UI at `/docs`. This page intentionally stays at the table-group level.

---

## Common base: `BaseModel`

Every concrete model inherits from `BaseModel` (`models/base.py`), which provides five common columns:

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Auto-generated `uuid4` |
| `created_at` | timestamptz | Server-set creation timestamp |
| `updated_at` | timestamptz | Auto-refreshed on every UPDATE |
| `deleted` | boolean | Soft-delete flag (default `false`) |
| `deleted_at` | timestamptz | When the row was soft-deleted |

Queries apply `WHERE deleted = false` — soft-deleted rows stay in the database but are excluded from results. Composite indexes include the `deleted` column for efficient filtering.

> **Exception**: `CheckpointModel` does **not** inherit `BaseModel` — it has its own `id`, `created_at`, and no `updated_at`/`deleted_at` (checkpoints are immutable).

---

## Domain 1: Identity and Tenancy

| Table | Purpose | Key FK |
|-------|---------|--------|
| `organizations` | Top-level tenant boundary | — |
| `workspaces` | Unit of data isolation | `org_id` → organizations |
| `users` | Authenticated actors (email, password hash, SSO ID) | — |
| `workspace_members` | User ↔ Workspace membership with role | `user_id`, `workspace_id` |
| `api_keys` | Scoped API keys (`hcat_*`, SHA-256 hashed) | `org_id`, `workspace_id`, `created_by` |
| `agent_card_keys` | A2A AgentCard signing keys (ES256) | — |

See [Multi-Tenancy](../concepts/multi-tenancy.md) and [Authentication and Identity](../concepts/auth-identity.md).

---

## Domain 2: Agents and Execution

| Table | Purpose | Key FK |
|-------|---------|--------|
| `agents` | Agent configurations (persona, model, tools, KB bindings, risk level) | `workspace_id` |
| `sessions` | Conversation sessions (`active`/`interrupted`/`completed`) | `agent_id`, `workspace_id` |
| `conversations` | Conversation threads within sessions | `session_id` |
| `messages` | Individual messages (role, content, tool calls) | `session_id`, `conversation_id` |
| `checkpoints` | Immutable execution state snapshots (per superstep) | `session_id` |
| `evidences` | Evidence tracking for context engineering | `session_id` |
| `approval_records` | Human-in-the-loop approval decisions | `session_id` |

All execution tables carry `workspace_id` for tenant isolation. See [The Execution Engine](../concepts/engine.md).

---

## Domain 3: Workflows

| Table | Purpose | Key FK |
|-------|---------|--------|
| `workflows` | Graph DSL definitions (JSON) | `workspace_id` |
| `workflow_versions` | Immutable version snapshots | `workflow_id` |
| `workflow_runs` | Execution records for workflow runs | `workflow_id`, `session_id` |

See [Workflows](../concepts/workflows.md).

---

## Domain 4: Tools and Skills

| Table | Purpose | Key FK |
|-------|---------|--------|
| `tools` | Tool definitions (`builtin`/`custom`/`mcp`) | `workspace_id` |
| `skills` | SKILL.md-format skill definitions | `workspace_id` |
| `plugins` | Discovered and registered plugins | — |
| `tool_policies` | Workspace-level deny/ask/allow rules (glob patterns) | `workspace_id` |
| `tool_policy_rules` | Per-agent policy overrides | `workspace_id`, `agent_id` |
| `agent_policy_configs` | Per-agent declarative policy mode + allow/deny lists | `agent_id` |
| `tool_decisions` | Security decision log (allow/deny/require_approval) | `workspace_id` |

See [Tools, MCP, and A2A](../concepts/tools-and-mcp.md) and [Guardrails and Hooks](../concepts/guardrails.md).

---

## Domain 5: Knowledge and Memory

| Table | Purpose | Key FK |
|-------|---------|--------|
| `knowledge_bases` | KB configurations (chunking, embedding) | `workspace_id` |
| `documents` | Uploaded documents (status, chunk count) | `kb_id`, `workspace_id` |
| `datasets` | Dataset metadata for evaluation | `workspace_id` |
| `memory_blocks` | L1 working-memory blocks (named regions in context) | `agent_id` |
| `memories` | L3 user memories (embeddings + metadata) | `user_id`, `agent_id`, `workspace_id` |
| `knowledge_memories` | L4 knowledge memories (per-agent knowledge store) | `agent_id` |

Vector embeddings themselves live in Qdrant (not PostgreSQL). These tables hold the metadata and configuration. See [Knowledge and Retrieval](../concepts/knowledge-rag.md) and [Memory System](../concepts/memory.md).

---

## Domain 6: Models and Inference

| Table | Purpose | Key FK |
|-------|---------|--------|
| `model_providers` | Provider connections (encrypted keys, base URL, status) | — |
| `model_registry` | Registered models (type, capabilities, version) | `provider_id` |
| `model_pricings` | Per-model pricing data for cost tracking | — |
| `model_cost_budgets` | Per-model budget policies (hard/soft caps) | `workspace_id` |
| `model_deployments` | Self-hosted model deployment records | — |
| `inference_endpoints` | Inference endpoint configurations | — |
| `fine_tuning_jobs` | Fine-tuning job tracking | — |

See [Model Hub](../concepts/model-hub.md).

---

## Domain 7: Security and Guardrails

| Table | Purpose | Key FK |
|-------|---------|--------|
| `hook_configs` | Guardrail hook configurations (Pre/Post LLM/Tool) | `workspace_id`, `agent_id` |
| `security_findings` | Long-lived policy-violation findings | `workspace_id` |
| `audit_logs` | Every HTTP request (async write) | `workspace_id`, `user_id` |
| `pii_mappings` | PII anonymization mapping table (mask ↔ original) | `session_id` |
| `dlp_policies` | DLP entity policies (entity → action) | `workspace_id` |
| `dlp_custom_regex` | Custom regex patterns for DLP recognizers | `workspace_id` |
| `dlp_dictionaries` | Dictionary-based DLP recognizers | `workspace_id` |

See [Guardrails and Hooks](../concepts/guardrails.md), [DLP](../concepts/dlp.md), and [Security Hardening](../how-to/security-hardening.md).

---

## Domain 8: Evaluation

| Table | Purpose | Key FK |
|-------|---------|--------|
| `evaluation_datasets` | Test case collections | `workspace_id` |
| `evaluation_items` | Individual test cases (input, expected output) | `dataset_id` |
| `evaluation_runs` | Execution records (evaluator selection, status) | `dataset_id`, `agent_id` |
| `evaluation_scores` | Per-item, per-metric scores with reasoning | `run_id`, `item_id` |
| `conversation_turn_scores` | Per-turn quality scores from conversation analytics | `session_id` |
| `conversation_clusters` | Auto-grouped conversations by topic | `workspace_id` |

See [Agent Evaluation](../concepts/evaluation.md).

---

## Domain 9: Operations

| Table | Purpose | Key FK |
|-------|---------|--------|
| `traces` | OpenTelemetry span trees (per request/workflow) | `session_id`, `agent_id` |
| `metrics` | Aggregated operational metrics | — |
| `alert_rules` | Alert rule definitions (threshold/anomaly/SLA) | `workspace_id` |
| `alert_events` | Fired alert instances | `rule_id` |
| `alert_silences` | Silenced alert windows | `rule_id` |
| `escalation_policies` | Alert escalation rules (on-call rotation) | — |
| `notification_channels` | Alert delivery channels (Slack/email/webhook) | — |
| `quotas` | Per-tenant quota limits | `workspace_id` |
| `quota_usage` | Current quota consumption | `quota_id` |
| `budget_snapshots` | Budget tracking snapshots | `workspace_id` |
| `budget_forecasts` | ML-based spend forecasts | `workspace_id` |

See [Observability](../concepts/observability.md).

---

## Domain 10: Platform

| Table | Purpose | Key FK |
|-------|---------|--------|
| `feature_flags` | Toggle feature availability (used for rollback) | — |
| `backup_records` | Backup metadata (scope, status, size) | — |
| `scheduled_tasks` | Cron-based scheduled agent runs | `agent_id`, `workspace_id` |
| `scheduled_task_executions` | Execution records for scheduled tasks | `task_id` |
| `prompts` | Prompt templates | `workspace_id` |
| `prompt_versions` | Immutable prompt version snapshots | `prompt_id` |
| `a2a_tasks` | A2A protocol task records | `session_id` |

---

## Cross-cutting patterns

### `workspace_id` isolation

Every table in domains 2–9 that holds tenant-scoped business content carries a `workspace_id` foreign key. Queries are constructed with `workspace_id` filters derived from the authenticated request, enforcing [data-level isolation](../concepts/multi-tenancy.md) at the database layer.

### Soft delete

All `BaseModel` tables use soft delete (`deleted` + `deleted_at`). Composite indexes include `deleted` so the `WHERE deleted = false` filter is efficient. The `CheckpointModel` exception is intentional — checkpoints are append-only and never soft-deleted.

### Immutable version tables

Several tables have companion `_versions` tables that store immutable snapshots:
- `workflows` → `workflow_versions`
- `prompts` → `prompt_versions`

These support [version-and-rollback](../how-to/version-and-rollback-agent.md) workflows.

---

## Further reading

- [Core Concepts](../design/concepts.md) — full entity definitions, field types, and relationships
- [Multi-Tenancy](../concepts/multi-tenancy.md) — the `workspace_id` isolation model
- [Extension Points](extension-points.md) — the `EnginePort` interface and SPI surface
- [Migrations](../migrations/) — Alembic migration guides and expand-contract patterns
- Interactive schema: `/docs` (Swagger) when the server is running
