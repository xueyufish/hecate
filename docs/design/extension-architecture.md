# Extension SPI & Plugin Architecture

Deep-dive design document for Hecate's two-tier extension system: **many engine extension interfaces (multiple Core + multiple SPI)** at the engine layer per [ADR-016](adr/016-platform-spi-architecture.md) + **multiple plugin SPI types** at the platform layer. For the API reference, see [Extension Points](../reference/extension-points.md). For the decision rationale, see [ADR-016](adr/016-platform-spi-architecture.md).

> The 8 plug-in-type ABCs (`ToolPlugin` / `TriggerPlugin` / `ExtensionPlugin` / `ModelPlugin` / channel/evaluator/auth/secret providers) are the **P-tier deep-integration** extension surface. The ecosystem-facing third-party ingestion path is **Agent Plugins 1.0** (feature 5.5c) — single adapter module `plugin/agent_plugins.py` ingests `plugin.json` + `skills/` + `mcp.json` as one atomic unit, projects skills as `SkillModel` rows with `source="plugin"` and MCP servers under `<plugin>__<server>` names. Component-level trust dispatch per [ADR-029](adr/029-trust-tiered-kernel-plugin-architecture.md): skills (T4) + http/sse MCP (T2) install by workspace admin; stdio MCP (T1) only by platform installer via config allowlist, executed in Docker sandbox via `plugin/stdio_sandbox.py`. Bare-SKILL-md directories (Claude Code ecosystem) install as virtual packages.
>
> 5.5 (enh) T0 Tightening is the structural companion to the component-level trust dispatch above: the T0 tier discipline from [ADR-029](adr/029-trust-tiered-kernel-plugin-architecture.md) is enforced in code — `python:module:Class` entries load in-process only when `module` is first-party (`hecate` / `hecate.*`); SaaS rejects all non-first-party; self-hosted default-denies with explicit `PLUGIN_PYTHON_ENTRY_ALLOWLIST` (segment-boundary prefix match); install-time pre-check rolls back the extracted directory on rejection; SaaS skips runtime `uv pip install` for `requirements.txt`. The P-tier deep-integration surface (the multiple ABCs above) is unaffected — those are in-repo or deployer-bundled first-party code, which the gate permits.

This document is for **implementers** — engineers writing custom extensions, third-party plugin authors, or contributors evolving the extension surfaces.

---

## Why extension points matter

Hecate's engine layer is designed to have **zero external dependencies** (except `jsonschema`). But real-world deployments need extensive customization:

- Different checkpoint backends (Postgres vs Redis vs S3)
- Different LLM providers (100+ via LiteLLM, plus custom models)
- Different scheduling strategies (FIFO vs priority vs deadline-aware)
- Different authentication (JWT today, OAuth2/mTLS tomorrow)
- Different notification channels (Email today, Slack/DingTalk tomorrow)
- Custom evaluation metrics (the 41 built-ins don't cover your domain)

Extension points are the **controlled escape hatches** that let deployments customize Hecate without forking it, without coupling the engine to specific implementations, and without breaking the upgrade path.

The goal of this design is: **every custom need has either a Core or SPI escape hatch, and the choice between them is principled**.

---

## Two-tier model: Core vs SPI

Hecate uses a **two-tier** extension architecture. The boundary is **load-time vs runtime** and **engine-internal vs deployment-external**:

```
┌──────────────────────────────────────────────────────────────────────┐
│                       Engine Layer (load-time)                       │
│                                                                      │
│   26 Engine Extension Interfaces                                  │
│   - Abstract base classes in src/hecate/runtime/                     │
│   - Default InMemory implementations                                 │
│   - Hot-swappable via dependency injection (engine startup)         │
│   - Custom code runs IN the engine process                          │
│   - Affects execution correctness                                   │
│                                                                      │
│   Examples: SchedulerStrategy, EvictionPolicy, CheckpointStore      │
│                                                                      │
└──────────────────────┬───────────────────────────────────────────────┘
                       │
                       │  EnginePort (the only bridge)
                       │
┌──────────────────────┴───────────────────────────────────────────────┐
│                    Platform Layer (runtime)                          │
│                                                                      │
│   8 Plugin SPI Types                                               │
│   - ABCs in src/hecate/core/plugin/types/ + spi/                        │
│   - Loaded via PluginRegistry (manifest-driven)                    │
│   - Custom code runs OUTSIDE the engine                            │
│   - Affects integration surface                                    │
│                                                                    │
│   Examples: Tool, Extension, Trigger, Model, Evaluator, Channel,   │
│             AuthProvider, SecretProvider                           │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### When to use Core vs SPI

| Decision criterion | Use Core | Use SPI |
|---|---|---|
| **Where does the code run?** | In the engine process | Outside the engine process |
| **What's the failure mode?** | Wrong = incorrect execution | Wrong = degraded integration |
| **What's the load timing?** | Engine startup (compile-time-ish) | Application runtime (hot-swap) |
| **What's the distribution model?** | Fork + rebuild | Install plugin package |
| **What's the audit model?** | Part of Hecate codebase | Third-party, sandboxed |

**Rule of thumb**: If you're changing how Hecate *executes*, use Core. If you're changing how Hecate *integrates*, use SPI.

---

## The 26 Engine Extension Interfaces

All engine extension interfaces follow the **Ports and Adapters pattern** (a.k.a. Hexagonal Architecture):

```
src/hecate/runtime/
├── ports.py            ← EnginePort (the master boundary)
├── worker.py           ← Worker / WorkerPool
├── checkpoint.py       ← CheckpointStore
├── eventstore.py       ← EventStore
├── context.py          ← ContextEngine
├── scheduler.py        ← SchedulerStrategy
├── eviction.py         ← EvictionPolicy
├── optimization.py     ← OptimizationPass
├── guardrail.py        ← GuardrailHooks (×4: PreLLM, PostLLM, PreTool, PostTool)
├── middleware.py       ← MiddlewareChain (E3: ordered waterfall chain, 7 phases)
├── middleware_adapters.py ← HookStageAdapter (legacy hooks → chain stages)
├── middleware_factory.py  ← chain builders from legacy single-hook wiring
├── monotonic_denials.py   ← MonotonicDenialTracker (per-session denial set)
├── shell_analysis.py      ← ShellAnalyzer (content-aware command decomposition)
├── loginvariants_t2.py / _t3.py ← APPROVAL.TURN_CLOSURE / MONOTONIC.DENIAL invariants
├── retry.py            ← RetryStrategy
├── channel.py          ← ChannelBehavior
├── decision_sink.py    ← DecisionSink
├── eventbus.py         ← EventBus
├── metrics_store.py    ← MetricsStore
├── policy_pipeline.py  ← PolicyLayer
├── session_hooks.py    ← Session Hooks (×4: start, end, prompt-submit, pre-compact)
├── session_state.py    ← SessionStateStore
├── task_allocator.py   ← TaskAllocator
├── tool_access.py      ← ApprovalCallback (+ shell-aware dangerous-pattern matching)
└── temporal/conflict.py ← ConflictResolver
```

Each module exposes an `ABC` (abstract base class) and ships with at least one default implementation. Every Core extension point **must** have a working default — Hecate's zero-config startup depends on this.

### 1. `EnginePort` (master boundary)

The most important Core extension point. **All** engine-to-service communication goes through `EnginePort`. The engine never imports a service module directly.

```python
# src/hecate/runtime/ports.py
class EnginePort(ABC):
    @abstractmethod
    def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]: ...
    
    @abstractmethod
    async def tool_execute(self, tool_name: str, args: dict) -> dict: ...
    
    @abstractmethod
    async def knowledge_query(self, kb_id: UUID, query: str, top_k: int) -> list[Chunk]: ...
    
    @abstractmethod
    async def checkpoint_save(self, session_id: UUID, state: dict) -> None: ...
    
    @abstractmethod
    async def checkpoint_load(self, session_id: UUID) -> dict | None: ...
```

**Default**: `InMemoryEnginePort` (testing only). **Production**: concrete adapter in `src/hecate/ops/` that delegates to real services.

### 2. `Worker` / `WorkerPool`

Dispatches node execution. The default uses an asyncio-based pool.

```python
# src/hecate/runtime/worker.py
class Worker(ABC):
    @abstractmethod
    async def execute_node(self, node: Node, state: State) -> NodeResult: ...

class WorkerPool(ABC):
    @abstractmethod
    async def dispatch(self, nodes: list[Node], state: State) -> list[NodeResult]: ...
```

**Default**: `DirectWorkerPool` (run sequentially in the same process). **Alternatives**: `ThreadPoolWorkerPool`, `ProcessPoolWorkerPool`, `RayWorkerPool` (P3).

### 3. `CheckpointStore`

Materializes execution-state caches for fast resume (the event log is the source of truth — [ADR-030](adr/030-event-sourced-execution-state.md)). Default: in-memory dict (lost on restart).

**Default**: `InMemoryCheckpointStore`. **Production**: `SessionStateMaterializer` (`services/orchestration/`) — implements this ABC and writes through `SessionStateStore` (Redis / PostgreSQL / Tiered). `PostgresCheckpointStore` was hard-removed in 13.4a-7 (along with the `checkpoints` table).

### 4. `EventStore`

Append-only log of all engine events — the **execution-state source of truth** (replayable, value-carrying; checkpoints are materialized caches of its fold).

**Default**: `InMemoryEventStore`. **Production**: `PostgresEventStore` (write-once table with hash chain for tamper evidence).

### 5. `ContextEngine`

Per-call context pipeline: assembler → evidence tracker → phase detector → token budget → provider shaping → message prioritization → tool filtering → offloader.

**Default**: `InMemoryContextEngine`. **Alternatives**: custom context strategies for domain-specific token budgets.

### 6. `SchedulerStrategy`

Decides which ready nodes to execute next in each superstep.

**Default**: `FIFOScheduler`. **Alternatives**: `PriorityScheduler`, `DeadlineAwareScheduler`, `CostOptimizedScheduler`.

### 7. `EvictionPolicy`

Decides which messages / artifacts to evict from working memory when token budget is exceeded.

**Default**: `LRUEviction`. **Alternatives**: `TimeBasedEviction`, `ImportanceBasedEviction`, `NoEviction`.

### 8. `OptimizationPass`

Runs before execution to optimize the graph: dead-node elimination, parallel-branch detection, common-subexpression elimination.

**Default**: `DeadNodeElimination`, `ParallelBranchDetection`. **Custom**: add domain-specific optimizations.

### 9. `ConflictResolver`

Resolves concurrent writes to the same channel when multiple nodes write in parallel.

**Default**: `NoOpConflictResolver` (last-write-wins). **Alternatives**: `CRDTConflictResolver`, `VersionedConflictResolver`.

### 10. `Guardrail Hooks (×4)` + Middleware Chain (E3)

Four interception points for security and policy — each position hosts an **ordered middleware chain** (`middleware.py`) since guardrail-upgrade-trio; the legacy ABCs adapt as single stages via `middleware_adapters.py`:

| Hook (→ chain phase) | When | Default |
|---|---|---|
| `PreLLMHook` → `AGENT_REQUEST` | Before sending messages to LLM | `NoOpPreLLMHook` |
| `PostLLMHook` → `LLM_RESPONSE` | After LLM response | `NoOpPostLLMHook` |
| `PreToolHook` → `TOOL_PRE_EXECUTE` | Before tool execution | `NoOpPreToolHook` |
| `PostToolHook` → `TOOL_RESULT` | After tool result | `NoOpPostToolHook` |

Chain semantics (ordering, BLOCK short-circuit with stage identity, SANITIZE pass-through, monotonic tightening) live in the kernel — stages are pluggable, the chain mechanism is not (ADR-029/030). Production wiring goes through `services/security/guardrail_assembly.py` (assembly-time per-agent scope filtering + fail-closed approval callback + shell-aware tool gating).

**Production hooks**: `PIIAnonymizerHook`, `LLMGuardHook`, `InjectionDetectionHook`. See [Security Architecture](security-architecture.md).

### 11. `RetryStrategy`

Retry policies for failed tool calls, LLM calls, and external requests.

**Default**: `NoRetryStrategy`. **Alternatives**: `ExponentialBackoffRetry`, `LinearRetry`, `ConditionalRetry` (retry only on specific exceptions).

---

## The 8 Plugin SPI Types

All SPI extension points follow the **Plugin pattern** with a manifest, ABC, registry, and lifecycle.

### PluginManifest (the contract)

Every SPI plugin must declare a `PluginManifest` (from `src/hecate/core/plugin/manifest.py`):

```python
@dataclass(frozen=True)
class PluginManifest:
    type: str                          # "tool", "extension", "trigger", "model", "channel", "evaluator", "auth_provider", "secret_provider"
    name: str                          # Unique within type, e.g., "faithfulness"
    version: str                       # Semantic version, e.g., "1.0.0"
    api_version: str = ""              # API version this plugin targets
    min_platform_version: str = ""     # Minimum Hecate version required
    description: str = ""              # Human-readable
    entry: str = ""                    # Loading strategy: "python:module:Class" or "mcp://..."
    permissions: tuple[str, ...] = () # Required permissions: ["network:https", "db:read", ...]
    translations: tuple[str, ...] = () # i18n message keys the plugin provides
    config_schema: dict | None = None  # JSON Schema for plugin configuration
```

The manifest is **immutable** (frozen dataclass). Plugins cannot mutate their own metadata after registration.

### 1. `EvaluatorBase`

```python
# src/hecate/core/plugin/spi/evaluator.py
class EvaluatorBase(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @property
    @abstractmethod
    def description(self) -> str: ...
    
    @abstractmethod
    async def evaluate(self, input: EvalInput) -> EvalOutput: ...
```

**Built-in**: 41 evaluators (faithfulness, relevance, hallucination detection, etc.). **Custom**: implement `EvaluatorBase` and register via `PluginManifest(type="evaluator", ...)`.

### 2. `ChannelBase`

External channel adapter (REST / WebSocket / IM / CLI). 

```python
class ChannelBase(ABC):
    @property
    @abstractmethod
    def protocol(self) -> str: ...     # "rest", "websocket", "cli", "feishu", ...
    
    @abstractmethod
    async def receive(self) -> AsyncIterator[UserMessage]: ...
    
    @abstractmethod
    async def send(self, message: AgentMessage) -> None: ...
    
    @abstractmethod
    async def stream(self, message: AgentMessage) -> AsyncIterator[Token]: ...
```

**Built-in**: `RESTChannel`, `CLIChannel`, `FeishuChannel` (11.3), `SlackChannel` (11.9). **Planned**: `WebSocketChannel` (P4).

### 3. `AuthProvider`

Authentication provider for incoming requests.

```python
class AuthProvider(ABC):
    @property
    @abstractmethod
    def scheme(self) -> str: ...       # "bearer", "api_key", "oauth2", "mtls"
    
    @abstractmethod
    async def authenticate(self, request: Request) -> AuthContext: ...
    
    @abstractmethod
    async def authorize(self, context: AuthContext, resource: Resource, action: str) -> bool: ...
```

**Built-in**: `JWTAuthProvider`, `APIKeyAuthProvider`. **Planned**: `OAuth2AuthProvider`, `mTLSAuthProvider`, `SAMLAuthProvider`.

### 4. Notifications: merged into `ChannelBase`

Notification delivery (audit, alert, etc.) used to be a standalone `NotifierABC`. It was **merged into `ChannelBase`** as an outbound channel: notification dispatchers are now `NotificationChannelAdapter` implementations extending `ChannelBase` (see `src/hecate/channel/notification.py`).

```python
class NotificationChannelAdapter(ChannelBase):
    @property
    @abstractmethod
    def channel(self) -> str: ...      # "email", "webhook", "slack", "dingtalk"

    @abstractmethod
    async def notify(self, event: NotificationEvent) -> None: ...
```

**Built-in**: `EmailNotificationAdapter`, `WebhookNotificationAdapter`, `WebSocketNotificationAdapter`. **Planned**: `SlackNotificationAdapter`, `DingTalkNotificationAdapter`, `PagerDutyNotificationAdapter`. There is no `notifier` plugin type anymore — use `channel` (see the [plugin manifest](../reference/plugin-manifest.md)).

### 5. `ToolPluginBase` / `ExtensionPluginBase` / `TriggerPluginBase` / `ModelPluginBase` / `SecretProvider`

The remaining five SPI types complete the eight-type taxonomy (registered in `PLUGIN_TYPE_REGISTRY` at `src/hecate/core/plugin/types/__init__.py`):

| ABC | File | Purpose |
|-----|------|---------|
| `ToolPluginBase` | `src/hecate/core/plugin/types/tool.py` | Callable tool that agents can invoke (built-in, custom, or MCP-backed). |
| `ExtensionPluginBase` | `src/hecate/core/plugin/types/extension.py` | Runtime interceptor auto-wired into all four guardrail hook points (Pre/Post LLM/Tool). |
| `TriggerPluginBase` | `src/hecate/core/plugin/types/trigger.py` | Event-driven invocation: webhook, schedule, or message-queue triggered entry points. |
| `ModelPluginBase` | `src/hecate/core/plugin/types/model.py` | Custom LLM provider built on the existing `InferenceBackendABC` surface. |
| `SecretProvider` | `src/hecate/vault/provider.py` | Custom secret storage backend for the vault abstraction. |

All follow the same Plugin pattern (manifest → ABC → `PluginRegistry` → lifecycle). See the [plugin manifest reference](../reference/plugin-manifest.md) for the manifest contract and [Writing a custom SPI plugin](#example-a-custom-evaluator) for worked examples.

### The "5th candidate": `i18n`

`src/hecate/i18n/` exists with 4 files. It provides translations for Hecate's own UI strings and CLI messages. It's **not yet** a fully-fledged SPI extension point — translations are bundled with Hecate, not contributed by third-party plugins. This is on the  for v1.1.

---

## Plugin lifecycle

A plugin goes through five states, managed by `PluginRegistry` (from `src/hecate/core/plugin/registry.py`):

```
                            install
   ┌──────────────────────────────────────────────────┐
   │                                                  ▼
┌──────┐    enable     ┌─────────┐    invoke    ┌──────────┐
│ New  │ ─────────────▶│ Loaded  │ ───────────▶│  Active  │
│      │ ◀─────────────│         │ ◀───────────│          │
└──────┘    disable    └─────────┘    complete  └──────────┘
   ▲                                                  │
   │                  uninstall                       │
   └──────────────────────────────────────────────────┘
```

| State | What it means |
|---|---|
| **New** | Plugin package installed on disk, not loaded |
| **Loaded** | Plugin module imported, manifest validated, instance created |
| **Active** | Plugin is registered with the relevant subsystem, can be invoked |
| **Disabled** | Manifest preserved, instance kept for re-enable, no new invocations |
| **Uninstalled** | Package removed, instance destroyed |

The lifecycle hooks are in `src/hecate/core/plugin/lifecycle.py` (optional `PluginLifecycle` protocol). Plugins can implement hooks to be notified of transitions:

```python
class PluginLifecycle(Protocol):
    async def on_load(self) -> None: ...
    async def on_enable(self) -> None: ...
    async def on_disable(self) -> None: ...
    async def on_uninstall(self) -> None: ...
```

Not all hooks are required — implement only what you need.

---

## Writing a custom SPI plugin

### Example: a custom evaluator

```python
# my_evaluator.py
from hecate.core.plugin import PluginManifest, PluginContext
from hecate.core.plugin.spi.evaluator import EvaluatorBase
from hecate.ops.evaluation.types import EvalInput, EvalOutput


class DomainSpecificEvaluator(EvaluatorBase):
    @property
    def name(self) -> str:
        return "domain_specific_score"

    @property
    def description(self) -> str:
        return "Evaluates whether the agent's response correctly cites our domain glossary"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        # Custom logic: check if the response mentions any glossary terms
        glossary = input.config.get("glossary", [])
        hits = [t for t in glossary if t.lower() in input.response.lower()]
        score = len(hits) / max(len(glossary), 1)
        return EvalOutput(score=score, reasoning=f"Hit {len(hits)}/{len(glossary)} glossary terms")


MANIFEST = PluginManifest(
    type="evaluator",
    name="domain_specific_score",
    version="1.0.0",
    api_version="1.0",
    min_platform_version="0.5.0",
    description="Domain-specific glossary citation scorer",
    entry="python:my_evaluator:DomainSpecificEvaluator",
    permissions=["db:read:glossary"],
    config_schema={
        "type": "object",
        "properties": {
            "glossary": {"type": "array", "items": {"type": "string"}}
        }
    },
)


def register(registry):
    registry.register(MANIFEST, DomainSpecificEvaluator())
```

Then deploy:

```bash
python -m hecate.core.plugin.cli package ./my_evaluator
python -m hecate.core.plugin.cli install ./my_evaluator.hecate-plugin
# enable/disable via the plugin management REST API (/api/plugins)
```

### Example: a custom extension (guardrail hook)

The "Extension" plugin type is special — it inherits from `ExtensionPluginBase` and implements any of the four guardrail hooks:

```python
# my_pii_filter.py
from hecate.core.plugin.types.extension import ExtensionPluginBase
from hecate.runtime.guardrail import GuardrailResult, GuardrailAction


class MyPIIFilter(ExtensionPluginBase):
    """Redact custom PII patterns beyond what the built-in Presidio recognizers catch."""

    def on_pre_llm(self, messages, config):
        # Walk all messages, replace custom PII patterns
        for msg in messages:
            msg["content"] = self._redact_custom_patterns(msg["content"])
        return None  # Continue execution


MANIFEST = PluginManifest(
    type="extension",
    name="my_pii_filter",
    version="1.0.0",
    api_version="1.0",
    description="Custom PII redactor (e.g., internal project codes)",
    entry="python:my_pii_filter:MyPIIFilter",
)
```

Extension plugins are **automatically wired** into all four guardrail hook points by the registry — no manual enable needed per hook.

---

## Permissions model

Plugins declare required permissions in their manifest. Hecate enforces them at runtime:

| Permission | Meaning |
|---|---|
| `network:https://*` | Outbound HTTPS to any host |
| `network:https://api.example.com/*` | Outbound HTTPS to specific host (more restrictive) |
| `db:read:agents` | Read access to the `agents` table |
| `db:write:audit_logs` | Write access to the `audit_logs` table |
| `fs:read:/workspace/*` | Read access to a specific workspace path |
| `mcp:invoke` | Can call MCP servers |
| `a2a:invoke` | Can call A2A agents |

When a plugin tries to do something outside its declared permissions, the request is denied at the boundary (network egress filter, DB permission check, filesystem sandbox) and the violation is logged to the audit trail.

Plugins cannot grant themselves permissions after registration — the manifest is frozen.

---

## PluginContext and SDK

Plugins receive a `PluginContext` at registration time, giving them access to Hecate's services without needing to import internals:

```python
# src/hecate/core/plugin/sdk.py
class PluginContext:
    db_session: AsyncSession          # Database access (subject to permissions)
    audit_log: AuditLogger            # Emit audit events
    config: ConfigProvider             # Read plugin config
    metrics: MetricsExporter           # Export custom metrics
    logger: logging.Logger            # Namespaced logger
    cancel_token: CancelToken          # Cooperative cancellation
```

Plugins should **never** import from `src/hecate/runtime/` internals — only from published SPI interfaces and `hecate.core.plugin.sdk`. This boundary keeps third-party code from coupling to engine internals that may change.

---

## Versioning and compatibility

### Core extension points

Core extension points follow Hecate's **engine versioning** (currently `0.1.x`, alpha). Breaking changes to abstract interfaces are allowed within `0.x`. From `1.0` onwards, breaking changes require a major version bump.

If you implement a Core extension point and Hecate changes its interface, your code breaks. Pin to a specific Hecate version in production.

### SPI plugins

SPI plugins use **API versioning** independent of Hecate:

- `api_version = "1.0"` means the plugin implements SPI v1.0
- `min_platform_version = "0.5.0"` means Hecate 0.5.0 or later is required
- A new SPI version (e.g., `1.1`) adds methods but keeps backward compat — plugins declaring `api_version = "1.0"` still work
- Breaking changes require a new SPI major version

The registry checks compatibility at load time and rejects incompatible plugins with a clear error message.

---

## Comparison to other plugin models

| | Hecate | LangChain | Dify | n8n |
|---|---|---|---|---|
| **Engine extension** | multiple Core + multiple SPI = 15 ([ADR-016](adr/016-platform-spi-architecture.md)) | Decorators / custom node types | Plugin marketplace (DAG-level) | Custom nodes |
| **Platform extension** | 8 Plugin SPI types + Plugin SDK | Tools, retrievers, vector stores | Marketplace plugins (HTTP-based) | Nodes (npm packages) |
| **Load timing** | Engine startup / runtime | Runtime | Runtime (DAG parsing) | Runtime |
| **Distribution** | In-process Python packages | pip packages | Marketplace HTTP calls | npm packages |
| **Permission system** | Yes (declared in manifest) | No | No | No |
| **Hot reload** | Yes (`hot_reload.py`) | No | No | No |
| **Audit log** | All plugin actions logged | No | Partial | No |

Hecate's distinguishing features:

1. **Engine-level extensions** (Core) are not common — most platforms only allow DAG-level extensions
2. **Declared permissions** prevent plugins from leaking beyond their declared scope
3. **Hot reload** lets you iterate on a plugin without restarting Hecate
4. **Audit logging** of all plugin actions for compliance

---

## Decision tree: how to extend Hecate

```
I want to customize Hecate. What do I do?
│
├── I want to change how the engine executes (scheduling, eviction, ...)
│   → Use a Core extension point (rebuild engine)
│
├── I want to add a new evaluation metric
│   → Implement EvaluatorBase + PluginManifest
│
├── I want to add a new auth method (OAuth2, mTLS, SAML)
│   → Implement AuthProvider + PluginManifest
│
├── I want to add a new notification channel (Slack, PagerDuty)
│   → Implement a NotificationChannelAdapter (ChannelBase) + PluginManifest
│
├── I want to add a new external channel (Feishu, Discord, Telegram)
│   → Implement ChannelBase + PluginManifest
│
├── I want to inject custom logic into the agent execution (PII filter, custom validation)
│   → Implement ExtensionPluginBase + PluginManifest (auto-wired into all 4 hooks)
│
├── I want to add a new tool that agents can call
│   → Implement ToolPluginBase + PluginManifest, OR use MCP server (preferred for external tools)
│
├── I want to add a new LLM provider
│   → Implement ModelPluginBase + PluginManifest, OR add a LiteLLM adapter (preferred)
│
├── I want to react to events (scheduler triggers, webhooks)
│   → Implement TriggerPluginBase + PluginManifest
│
└── I'm not sure which path
    → Open an issue with the `extension:` tag and we'll help route it
```

---

## What's NOT exposed (yet)

| Extension point | Why not yet |
|---|---|
| **Custom LLM token cost calculator** | Cost tracking is internal to Model Hub |
| **Custom Pregel optimization passes** | Runtime is stable; no user demand for custom optimizations |
| **Custom channel protocols (IRC, Matrix, XMPP)** | Demand unclear |
| **Custom checkpoint serializers** | Would break resume across versions |
| **Custom event store sinks (Kafka, Pulsar)** | On the  for v1.1 |

If you have a use case for any of these, file an issue — we may be missing the right escape hatch.

---

## Implementation references

For specific implementation details:

- `src/hecate/runtime/ports.py` — EnginePort ABC
- `src/hecate/runtime/worker.py` — Worker + WorkerPool ABCs
- `src/hecate/runtime/checkpoint.py` — CheckpointStore ABC
- `src/hecate/runtime/eventstore.py` — EventStore ABC
- `src/hecate/runtime/context.py` — ContextEngine ABC
- `src/hecate/runtime/scheduler.py` — SchedulerStrategy ABC
- `src/hecate/runtime/eviction.py` — EvictionPolicy ABC
- `src/hecate/runtime/optimization.py` — OptimizationPass ABC
- `src/hecate/runtime/guardrail.py` — Pre/Post LLM/Tool Hook ABCs
- `src/hecate/runtime/retry.py` — RetryStrategy ABC
- `src/hecate/runtime/temporal/conflict.py` — ConflictResolver ABC
- `src/hecate/core/plugin/__init__.py` — Public SPI exports
- `src/hecate/core/plugin/manifest.py` — PluginManifest dataclass
- `src/hecate/core/plugin/registry.py` — PluginRegistry
- `src/hecate/core/plugin/lifecycle.py` — PluginLifecycle protocol
- `src/hecate/core/plugin/sdk.py` — PluginContext
- `src/hecate/core/plugin/spi/evaluator.py` — EvaluatorBase
- `src/hecate/core/plugin/types/extension.py` — ExtensionPluginBase (4 hooks)
- `src/hecate/core/plugin/types/tool.py` — ToolPluginBase
- `src/hecate/core/plugin/types/model.py` — ModelPluginBase
- `src/hecate/core/plugin/types/trigger.py` — TriggerPluginBase
- `src/hecate/core/plugin/cli.py` — standalone plugin CLI (`python -m hecate.core.plugin.cli`)
- `src/hecate/core/plugin/hot_reload.py` — plugin hot-reload
- `src/hecate/core/plugin/permission.py` — permissions enforcement
- `src/hecate/core/plugin/loader.py` — plugin loader
- `src/hecate/core/plugin/installer.py` — plugin installer
- `src/hecate/core/plugin/validation.py` — manifest validation
- `src/hecate/core/plugin/packaging.py` — plugin packaging

## Related documents

- [ADR-016: Platform SPI Architecture](adr/016-platform-spi-architecture.md) — the "why" behind this design
- [Reference: Extension Points](../reference/extension-points.md) — the API reference
- [How-to: Develop custom extensions](../how-to/develop-extensions.md) — practical recipe
- [Engine Design](engine-design.md) — the engine layer these extensions plug into
- [Security Architecture](security-architecture.md) — Guardrail Hooks in context
- [Tool Platform Design](tool-platform-design.md) — how SPI plugins relate to MCP/A2A
- Extension SPI Architecture — current design
- [Positioning](positioning.md) — why Hecate's extension model is different from competitors