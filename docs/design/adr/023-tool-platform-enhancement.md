# ADR-023: Tool Platform Enhancement Architecture

> **Status**: Proposed
> **Date**: 2026-07-02
> **Update (5.13a)**: TP1's content-scanning half was split out as feature **5.13a (Plugin Content Scanning, P3)** — a deterministic rule engine (injection/invisible-Unicode/secrets/allowed-tools rules, fail-closed block/warn/allow verdicts, enable-time rescan) enforced inside the 5.5c ingest pipeline; see `openspec/specs/plugin-content-scanning/`. TP1 (5.13, P5) retains only cryptographic signing, digest verification, and the security score, bound to the 12.0 marketplace.

## Context

Hecate's Tool Platform delivers MCP-first tool integration (bidirectional client + server), a tool registry with Docker sandbox execution, 5 built-in tools, skill loading, tool result validation, and 4-level risk authorization. Competitive analysis against OpenClaw, Claude Code, Dify, AgentScope, Salesforce Agentforce, AgentArts, and Palantir AIP revealed 6 gaps in the plugin ecosystem, tool operations, and observability layers:

| Gap | Description | Type | Priority |
|-----|-------------|------|----------|
| TP1 | **Plugin Security & Signing** — cryptographic signing, security scanning, digest verification, security score | New Feature | P5 (5.13) |
| TP2 | **Tool Execution Analytics Dashboard** — per-tool latency/success/failure metrics with drill-down traces | New Feature | P4 (8.9c) |
| TP3 | **Composable Tool Policy Pipeline** — multi-layer policy chain replacing single-condition gating | 5.6 Enhancement | P3 |
| TP4 | **Session Events + Tool Matchers** — session-level hooks and regex-based tool name filtering | 1.3.5i Enhancement | P4 |
| TP5 | **Plugin Type Taxonomy + Developer SDK** — 6 plugin types, Python SDK, template generator, compat validation | 5.5 Enhancement | P3 |
| TP6 | **Per-Tool Auth Scope** — per-tool credential vault, OAuth token management, isolated auth scope | 5.8 Enhancement | P3 |

These gaps span three architectural layers:
1. **Plugin ecosystem layer** — Type taxonomy, SDK, security lifecycle for marketplace distribution
2. **Tool operations layer** — Composable policy pipeline, per-tool auth, session-level hooks
3. **Observability layer** — Tool-specific execution analytics integrated with Ops Center

## Decision

### 1. Plugin Security & Signing (TP1/5.13) — Cryptographic Supply Chain

Build plugin security as a **supply chain integrity layer** on top of the existing Plugin SPI Core (5.5a) and Plugin Packaging (5.5b):

```
Plugin Developer
    │
    ▼
┌─────────────────────────────────────────────────┐
│              Security Scanning                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Static   │  │ CVE      │  │ Secret   │      │
│  │ Analysis │  │ Check    │  │ Detection│      │
│  └──────────┘  └──────────┘  └──────────┘      │
│  ┌──────────┐                                   │
│  │ Permission│  → Security Score (0-100)        │
│  │ Audit    │                                   │
│  └──────────┘                                   │
└─────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│              Cryptographic Signing               │
│  Ed25519 private key → signed plugin.yaml        │
│  Publisher key registered in KeyRegistry         │
└─────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│              Marketplace Publication             │
│  Asset Marketplace (12.0) stores:                │
│  - signed manifest + SHA-256 digest              │
│  - security scan results                         │
│  - publisher identity                            │
└─────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│              Install Verification                │
│  1. Verify Ed25519 signature against KeyRegistry │
│  2. Verify SHA-256 digest matches manifest       │
│  3. Check security score ≥ org threshold         │
│  4. Fail → block install + report                │
└─────────────────────────────────────────────────┘
```

**Design principle**: Security is enforced at the boundary (install time), not at runtime. Runtime execution uses existing sandbox (9.4c) and policy pipeline (TP3).

**Alternatives considered**:
- *GPG signing*: Rejected — Ed25519 is faster, keys are smaller, and it's the modern standard (npm, Cargo, PyPI all moving to Ed25519)
- *Runtime sandbox scanning*: Rejected — too expensive per-invocation; static + install-time scanning covers the threat model

### 2. Tool Execution Analytics Dashboard (TP2/8.9c) — Metrics Aggregation

Build the analytics dashboard as an **Ops Center extension** following the same composition pattern as Agent Health Monitoring (8.9a):

```
ToolRegistry.execute()
    │
    ▼
┌──────────────────────────────────┐
│       Tool Execution Span         │
│  (OpenTelemetry trace span)       │
│  - tool_name                      │
│  - agent_id                       │
│  - workspace_id                   │
│  - latency_ms                     │
│  - status (success/failure)       │
│  - error_type                     │
│  - token_cost                     │
└──────────────────────────────────┘
    │
    ▼ (async export via OTel collector)
┌──────────────────────────────────┐
│    TimescaleDB (hypertable)       │
│  tool_executions                  │
│  - time, tool_name, agent_id,     │
│    workspace_id, latency, status, │
│    error_type, token_cost         │
│  + continuous aggregates:         │
│    - tool_metrics_1min            │
│    - tool_metrics_1hour           │
│    - tool_metrics_1day            │
└──────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────┐
│    Analytics Dashboard API        │
│  GET /api/v1/tool-analytics       │
│  - p50/p95/p99 latency per tool   │
│  - success rate over time         │
│  - error distribution             │
│  - usage heatmap (tool × agent)   │
│  - cost breakdown per tool        │
└──────────────────────────────────┘
```

**Design principle**: Reuse existing OpenTelemetry infrastructure (8.1 Tracing). No new instrumentation — tool execution already emits trace spans. Analytics adds aggregation queries and dashboard views.

### 3. Composable Tool Policy Pipeline (TP3/5.6 Enhancement) — Filter Chain

Evolve the single-condition `available_when` gating into a **composable policy pipeline** inspired by OpenClaw's 6-layer filtering:

```python
# Policy DSL (YAML in agent config)
tool_policy:
  layers:
    - type: profile          # Base tool visibility profile
      profile: "enterprise-safe"
    - type: allow_deny       # Explicit allow/deny lists
      allow: ["web_search", "execute_code"]
      deny: ["file_delete"]
    - type: risk_level       # Risk-based gating
      max_risk: HIGH
      require_approval: CRITICAL
    - type: sandbox          # Sandbox-required tools
      sandbox_required: true
      tools: ["execute_code", "shell_exec"]
    - type: channel          # Channel-specific restrictions
      channel: "web-widget"
      deny: ["admin_*"]
    - type: plugin           # Plugin-availability gating
      require_plugin: "salesforce-connector"
      tools: ["crm_*"]
```

Each layer is a `ToolPolicyLayer` ABC with `filter(tools, context) → ToolSet`. Layers execute in declared order; each layer can remove tools from the visible set but cannot add tools. The final set is what the LLM sees.

**Design principle**: The pipeline is **subtractive** — each layer can only narrow the tool set, never expand it. This ensures security invariants are preserved regardless of layer ordering.

**Alternatives considered**:
- *Single monolithic policy engine*: Rejected — not composable, hard to extend
- *Attribute-based access control (ABAC) only*: Rejected — too complex for common cases; pipeline covers 90% of use cases with simpler mental model

### 4. Session Events + Tool Matchers (TP4/1.3.5i Enhancement) — Hook Expansion

Extend the deterministic hooks system with two capabilities:

**4a. Session-level events**:
```
Session lifecycle:
  SessionStart → [UserPromptSubmit → ReAct loop → Stop]* → SessionEnd
                                         ↓
                                   PreCompact (on context window pressure)
```

Each event triggers configured shell commands with JSON stdin (session context, prompt text, etc.). Shell commands run deterministically — no LLM involvement.

**4b. Tool name matchers**:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__github__.*",
        "command": "echo 'GitHub tool invoked' >> /var/log/tool-audit.log"
      },
      {
        "matcher": "execute_code",
        "command": "python3 /opt/hooks/code-scan.py"
      },
      {
        "matcher": ".*",
        "command": "python3 /opt/hooks/generic-tool-log.py"
      }
    ]
  }
}
```

Matchers use POSIX extended regex. Multiple hooks can match a single tool invocation; they execute in declaration order. A hook can block execution (exit code 2) or modify the tool call (stdout JSON override).

**Design principle**: Hooks are **deterministic and side-effectful** — they run shell commands, not LLM prompts. This complements the AI-based Guardrail Hooks (PreToolHook/PostToolHook) which provide semantic interception.

### 5. Plugin Type Taxonomy + Developer SDK (TP5/5.5 Enhancement) — Structured Ecosystem

Define **6 plugin types** aligned with Dify's proven taxonomy:

| Type | Purpose | Manifest Key | Example |
|------|---------|-------------|---------|
| **Tool Plugin** | Callable function exposed to agents | `type: tool` | `web_search`, `crm_lookup` |
| **Trigger Plugin** | Event-driven workflow invocation | `type: trigger` | Webhook, schedule, file-watch |
| **Extension Plugin** | Hook/middleware injection | `type: extension` | PreToolUse hook, rate limiter |
| **Model Plugin** | Custom LLM provider | `type: model` | Local vLLM, custom fine-tune |
| **Datasource Plugin** | External data connector | `type: datasource` | Salesforce, SAP, Snowflake |
| **Agent Strategy Plugin** | Custom reasoning loop | `type: agent_strategy` | Tree-of-Thought, ReAct variant |

Each plugin type has a typed Python base class in `hecate.plugin`:

```python
from hecate.plugin import ToolPlugin, PluginManifest

class MyTool(ToolPlugin):
    @classmethod
    def manifest(cls) -> PluginManifest:
        return PluginManifest(
            type="tool",
            name="my-search",
            version="1.0.0",
            api_version="1.0",
            min_platform_version="0.5.0",
            permissions=["network:https"],
        )

    async def execute(self, query: str, max_results: int = 5) -> list[dict]:
        ...
```

**Plugin template generator**:
```bash
hecate plugin init --type tool --name my-search
# Creates: my-search/
#   ├── plugin.yaml      (manifest)
#   ├── main.py          (entry point)
#   ├── pyproject.toml   (dependencies)
#   └── tests/           (test scaffold)
```

**Compatibility validation** (on install):
- `api_version` must match installed platform's plugin API version
- `min_platform_version` must be ≤ installed platform version
- Declared permissions must be allowed by organizational policy

### 6. Per-Tool Auth Scope (TP6/5.8 Enhancement) — Credential Isolation

Extend Enterprise Integration Framework with a **per-tool credential vault**:

```python
# Tool credential model
class ToolCredentialModel(Base):
    __tablename__ = "tool_credentials"
    id: UUID
    tool_name: str              # e.g., "salesforce_connector"
    workspace_id: UUID          # Scope to workspace
    credential_type: str        # "oauth2" | "api_key" | "basic" | "bearer"
    credential_data_encrypted: str  # Fernet-encrypted JSON
    oauth_token_url: str | None
    oauth_refresh_token_encrypted: str | None
    oauth_expires_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime
```

**Isolation guarantee**: Tools can only access their own credentials via `ToolCredentialVault.get(tool_name)`. Cross-tool credential access is blocked at the service layer. OAuth token refresh is handled automatically by a background task.

**Design principle**: Credentials are **tool-scoped, not agent-scoped**. Multiple agents using the same Salesforce connector share the same credentials, but the CRM tool cannot access the HR tool's credentials.

## Architecture Diagram

```
                         ┌─────────────────────────────────────┐
                         │          Agent Engine                │
                         │    (PregelRuntime + Workers)         │
                         └──────────────┬──────────────────────┘
                                        │
                         ┌──────────────▼──────────────────────┐
                         │          EnginePort                  │
                         │     tool_execute(name, args)         │
                         └──────────────┬──────────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │       Composable Policy Pipeline       │
                    │     (TP3 — 6-layer filter chain)       │
                    │  profile → allow/deny → risk →         │
                    │  sandbox → channel → plugin            │
                    └───────────────────┬───────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │          Tool Registry                  │
                    │    (routes to builtin/custom/MCP)      │
                    └──────┬──────────┬──────────┬──────────┘
                           │          │          │
                    ┌──────▼──┐ ┌────▼───┐ ┌───▼────┐
                    │ Builtin │ │ Custom │ │  MCP   │
                    │ Tools   │ │ Tools  │ │ Client │
                    └─────────┘ └────────┘ └────────┘
                           │          │          │
                    ┌──────▼──────────▼──────────▼──────────┐
                    │    Per-Tool Credential Vault (TP6)      │
                    │  ToolCredentialModel (isolated scope)   │
                    └─────────────────────────────────────────┘
                                        │
                    ┌───────────────────▼───────────────────┐
                    │     Tool Execution Analytics (TP2)      │
                    │  OTel spans → TimescaleDB → Dashboard   │
                    └─────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────┐
    │              Plugin Ecosystem (TP1 + TP5)                │
    │                                                         │
    │  Plugin SDK (TP5)          Security Pipeline (TP1)      │
    │  ┌─────────────────┐       ┌───────────────────┐        │
    │  │ 6 Plugin Types  │       │ Signing (Ed25519)  │        │
    │  │ Python SDK      │──────▶│ Scanning (static)  │        │
    │  │ Template Gen    │       │ Digest (SHA-256)   │        │
    │  │ Compat Check    │       │ Score (0-100)      │        │
    │  └─────────────────┘       └───────────────────┘        │
    └─────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────┐
    │         Hook System (TP4 — 1.3.5i Enhancement)          │
    │                                                         │
    │  Session Events: SessionStart, SessionEnd,               │
    │    UserPromptSubmit, PreCompact                         │
    │  Tool Matchers: regex-based PreToolUse/PostToolUse       │
    │    e.g., "mcp__github__.*" → targeted hook execution    │
    └─────────────────────────────────────────────────────────┘
```

## Consequences

### Positive

- **Enterprise-ready plugin ecosystem**: Security scanning + signing + digest verification matches OpenClaw and npm supply chain standards
- **Granular tool governance**: Composable policy pipeline enables per-org, per-channel, per-risk-level tool access control
- **Observability parity**: Tool-level analytics closes the gap with Salesforce Session Trace and Palantir end-to-end observability
- **Developer experience**: 6-type taxonomy + SDK + template generator reduces plugin development friction (Dify-proven model)
- **Per-tool security isolation**: Credential vault prevents cross-tool credential leakage, enabling safe multi-connector enterprise deployments

### Negative

- **Complexity**: 6-type taxonomy adds surface area; each type needs documentation, validation, and SDK support
- **Pipeline overhead**: Composable policy pipeline adds per-invocation filter overhead (mitigated by short-circuit evaluation and caching)
- **TimescaleDB dependency**: Tool analytics requires continuous aggregates (mitigated by making analytics optional — degrades gracefully to OTel-only tracing)

## Related Documents

- [Tool Platform Design](../tool-platform-design.md) — Detailed design for TP1-TP6 with personas, API endpoints, and data models
- [ADR-016: Platform SPI Architecture](016-platform-spi-architecture.md) — Plugin SPI Core foundation
- [ADR-008: Security via Hooks](008-security-via-hooks.md) — Guardrail Hooks architecture
- [ADR-021: Ops Center Architecture](021-ops-center-architecture.md) — Composition pattern for Tool Execution Analytics (TP2)
- [ADR-022: Model Hub Enhancement](022-model-hub-enhancement.md) — Parallel enhancement pattern for gap closure
