# Release Notes

Human-readable highlights of Hecate's release history. This complements the machine-readable data in [GitHub Releases](https://github.com/xueyufish/hecate/releases) and the per-change audit trail in `openspec/changes/archive/`.

> Hecate is currently in **alpha** (0.1.x). APIs may change between minor versions.

---

## How to read release notes

Hecate's release process:

- **Every commit** landed to `main` is recorded in `openspec/changes/`
- **Every minor version** (e.g., 0.1.0 → 0.1.1) is a GitHub Release with auto-generated notes from merged PRs
- **Every minor version** also gets a CHANGELOG.md entry in this format
- **Highlights** are the 3-5 most notable changes from each release

For the full history, see [GitHub Releases](https://github.com/xueyufish/hecate/releases).

---

## 0.1.x — Alpha (current)

**Status**: Alpha. APIs may change.

### What works today

Hecate alpha delivers the following core capabilities:

- **Pregel/BSP execution engine** with event-sourced execution state (Log-as-Truth event log + materialized checkpoint caches; [ADR-030](../design/adr/030-event-sourced-execution-state.md))
- **11 core + 4 SPI extension points** (EnginePort, Worker, CheckpointStore, ContextEngine, Scheduler, Eviction, Optimizer, ConflictResolver, Retry, Guardrail ×4, Evaluator, Channel, Auth, Notifier)
- **Multi-agent orchestration** with 6 collaboration patterns (Hierarchical, Handoff, Pipeline, Broadcast, Negotiation, Debate)
- **MCP server + client** (bidirectional, Streamable HTTP)
- **A2A Protocol server + client** (Linux Foundation v1.0 GA)
- **OpenAI-compatible API** at `/v1/chat/completions` (drop-in replacement)
- **Multi-tenancy** with Organization → Workspace → RBAC (34 models with `workspace_id`)
- **4-level memory** (L1 working / L2 compressed / L3 user / L4 knowledge)
- **RAG pipeline** with hybrid dense+sparse retrieval (BGE-M3)
- **Visual canvas** at `web/` (React Flow + JSON DSL bidirectional sync)
- **3 CLI entry points**: `hecate`, `hecate-migrate`, `hecate-flag-audit`
- **Observability stack**: OTel traces, Prometheus metrics, structured logs, audit pipeline
- **DLP engine** for outbound content scanning
- **Guardrail hooks** (Pre/Post LLM/Tool) for PII masking, injection defense, audit

### Recent highlights (last 30 days)

- **2026-08-10** — Outbound DLP engine shipped (Content Security)
- **2026-08-09** — Version upgrade workflow
- **2026-08-07** — State store deprecation (AgentStateStore → SessionStateStore)
- **2026-08-05** — EventStore Postgres wiring
- **2026-08-04** — Horizontal scaling validation (3-replica cluster)
- **2026-08-03** — Session state store wired end-to-end
- **2026-08-02** — Redis + Postgres session state backends
- **2026-07-31** — Data backup & recovery (full system)
- **2026-07-29** — Sandbox container pool
- **2026-07-26** — SIEM security pipeline
- **2026-07-22** — Context offloading
- **2026-07-19** — Wire agent handoff

See [GitHub Releases](https://github.com/xueyufish/hecate/releases) for the full list.

### Known limitations (alpha)

- No SLAs; breaking changes can happen between minor versions
- No external security audit (scheduled for Beta exit)
- No multi-region active-active (post-1.0)
- No plugin marketplace (post-1.0)
- No GraphRAG / knowledge graph (1.0)
- Cloud-native deployment patterns are still being battle-tested

---

## 0.2.x — Beta (planned)

**Status**: In development. Target release: 2026 Q4.

The 0.2 release is the **Beta exit** — the point at which Hecate commits to backwards compatibility for the 0.x line.

### Goals

- [ ] Public APIs (REST + CLI + Graph DSL) documented compatibility commitment
- [ ] Every spec in `openspec/specs/` has at least one passing test
- [ ] All `📋 Planned` items removed from L1 architecture for core pillars
- [ ] Load test: 100 concurrent sessions on single node
- [ ] External security audit (OWASP ASI top 10)
- [ ] Documentation: every public feature has a tutorial or how-to
- [ ] Signed releases, SBOM, container images published

See [Migrating from 0.1.x to 0.2.x](../migrations/v0.1-to-v0.2.md) for the upgrade procedure.

### Expected in 0.2

- **API freeze**: OpenAI-compatible `/v1/chat/completions` subset documented stable
- **Graph DSL schema** frozen
- **Plugin manifest `api_version`** required
- **Backward compatibility** commitment for 0.x
- **External security audit** completed
- **Configuration renames** documented (e.g., `AGENT_STATE_STORE_BACKEND` → `SESSION_STATE_STORE_BACKEND`)

---

## 1.0 GA (planned)

**Status**: Planned. Target release: 2027 Q2.

The 1.0 release is the **GA** — first version Hecate guarantees LTS support for.

### Goals

- [ ] Semantic versioning begins
- [ ] Public APIs backwards-compatible across all 1.x
- [ ] LTS branches every 6 months
- [ ] First major-version upgrade story (e.g., 1.0 → 2.0)
- [ ] Production deployment guide validated by 5+ external teams

### Expected in 1.0

- Multi-agent orchestration full maturity (all 6 patterns GA)
- GraphRAG + knowledge graph (if P2 ships)
- Plugin marketplace (if P2 ships)
- Per-tenant encryption keys (if P3 ships)
- Production security certifications (SOC 2, HIPAA — pending audit)



---

## Post-1.0 (planned)

- **1.x**: incremental improvements, new integrations, marketplace
- **2.0**: multi-region active-active (P5), major architectural changes (post-quantum crypto, etc.)



---

## How to stay informed

- **GitHub Releases** — every version tag creates a release with auto-generated notes: [github.com/xueyufish/hecate/releases](https://github.com/xueyufish/hecate/releases)
- **GitHub Watch** — click "Watch" on the repo to get email notifications for new releases
- **GitHub Discussions** — for announcements and community Q&A
- **CHANGELOG.md** (repo root) — machine-readable changelog

---

## Related documents

- [CHANGELOG.md](../../CHANGELOG.md) — machine-readable changelog
-  — what's being worked on
- [Migration Guide 0.1 → 0.2](../migrations/v0.1-to-v0.2.md) — Beta upgrade
- [GitHub Releases](https://github.com/xueyufish/hecate/releases) — auto-generated notes