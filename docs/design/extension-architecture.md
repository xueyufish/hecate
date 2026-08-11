# Extension SPI & Plugin Architecture

Deep-dive design document for Hecate's two-tier extension system: **11 Core extension points** at the engine layer + **4 SPI extension points** at the platform layer. For the API reference, see [Extension Points](../reference/extension-points.md). For the decision rationale, see [ADR-016](adr/016-platform-spi-architecture.md).

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
│   11 Core Extension Points                                          │
│   - Abstract base classes in src/hecate/engine/                     │
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
│   4 SPI Extension Points                                            │
│   - ABCs in src/hecate/plugin/spi/                                   │
│   - Loaded via PluginRegistry (manifest-driven)                     │
│   - Custom code runs OUTSIDE the engine                             │
│   - Affects integration surface                                     │
│                                                                      │
│   Examples: Evaluator, AuthProvider, Channel, Notifier              │
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

## The 11 Core Extension Points

All Core extension points follow the **Ports and Adapters pattern** (a.k.a. Hexagonal Architecture):

```
src/hecate/engine/
├── ports.py            ← EnginePort (the master boundary)
├── worker.py           ← Worker / WorkerPool
├── checkpoint.py       ← CheckpointStore
├── eventstore.py       ← EventStore
├── context.py          ← ContextEngine
├── scheduler.py        ← SchedulerStrategy
├── eviction.py         ← EvictionPolicy
├── optimization.py     ← OptimizationPass
├── guardrail.py        ← GuardrailHooks (×4: PreLLM, PostLLM, PreTool, PostTool)
├── retry.py            ← RetryStrategy
└── temporal/conflict.py ← ConflictResolver
```

Each module exposes an `ABC` (abstract base class) and ships with at least one default implementation. Every Core extension point **must** have a working default — Hecate's zero-config startup depends on this.

### 1. `EnginePort` (master boundary)

The most important Core extension point. **All** engine-to-service communication goes through `EnginePort`. The engine never imports a service module directly.

```python
# src/hecate/engine/ports.py
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

**Default**: `InMemoryEnginePort` (testing only). **Production**: concrete adapter in `src/hecate/services/` that delegates to real services.

### 2. `Worker` / `WorkerPool`

Dispatches node execution. The default uses an asyncio-based pool.

```python
# src/hecate/engine/worker.py
class Worker(ABC):
    @abstractmethod
    async def execute_node(self, node: Node, state: State) -> NodeResult: ...

class WorkerPool(ABC):
    @abstractmethod
    async def dispatch(self, nodes: list[Node], state: State) -> list[NodeResult]: ...
```

**Default**: `DirectWorkerPool` (run sequentially in the same process). **Alternatives**: `ThreadPoolWorkerPool`, `ProcessPoolWorkerPool`, `RayWorkerPool` (P3).

### 3. `CheckpointStore`

Persists agent state for resume / time-travel. Default: in-memory dict (lost on restart).

**Default**: `InMemoryCheckpointStore`. **Production**: `PostgresCheckpointStore` (uses `agent_state` table with JSONB).

### 4. `EventStore`

Append-only log of all engine events. Used for debugging, replay, observability.

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

### 10. `Guardrail Hooks (×4)`

Four interception points for security and policy:

| Hook | When | Default |
|---|---|---|
| `PreLLMHook` | Before sending messages to LLM | `NoOpPreLLMHook` |
| `PostLLMHook` | After LLM response | `NoOpPostLLMHook` |
| `PreToolHook` | Before tool execution | `NoOpPreToolHook` |
| `PostToolHook` | After tool result | `NoOpPostToolHook` |

**Production hooks**: `PIIAnonymizerHook`, `LLMGuardHook`, `InjectionDetectionHook`. See [Security Architecture](security-architecture.md).

### 11. `RetryStrategy`

Retry policies for failed tool calls, LLM calls, and external requests.

**Default**: `NoRetryStrategy`. **Alternatives**: `ExponentialBackoffRetry`, `LinearRetry`, `ConditionalRetry` (retry only on specific exceptions).

---

## The 4 SPI Extension Points

All SPI extension points follow the **Plugin pattern** with a manifest, ABC, registry, and lifecycle.

### PluginManifest (the contract)

Every SPI plugin must declare a `PluginManifest` (from `src/hecate/plugin/manifest.py`):

```python
@dataclass(frozen=True)
class PluginManifest:
    type: str                          # "evaluator", "channel", "auth", "notifier", etc.
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

### 1. `EvaluatorABC`

```python
# src/hecate/plugin/spi/evaluator.py
class EvaluatorABC(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @property
    @abstractmethod
    def description(self) -> str: ...
    
    @abstractmethod
    async def evaluate(self, input: EvalInput) -> EvalOutput: ...
```

**Built-in**: 41 evaluators (faithfulness, relevance, hallucination detection, etc.). **Custom**: implement `EvaluatorABC` and register via `PluginManifest(type="evaluator", ...)`.

### 2. `ChannelABC`

External channel adapter (REST / WebSocket / IM / CLI). 

```python
class ChannelABC(ABC):
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

**Built-in**: `RESTChannel`, `CLIChannel`. **Planned**: `WebSocketChannel`, `FeishuChannel`, `SlackChannel`.

### 3. `AuthProviderABC`

Authentication provider for incoming requests.

```python
class AuthProviderABC(ABC):
    @property
    @abstractmethod
    def scheme(self) -> str: ...       # "bearer", "api_key", "oauth2", "mtls"
    
    @abstractmethod
    async def authenticate(self, request: Request) -> AuthContext: ...
    
    @abstractmethod
    async def authorize(self, context: AuthContext, resource: Resource, action: str) -> bool: ...
```

**Built-in**: `JWTAuthProvider`, `APIKeyAuthProvider`. **Planned**: `OAuth2AuthProvider`, `mTLSAuthProvider`, `SAMLAuthProvider`.

### 4. `NotifierABC`

Notification delivery (audit, alert, etc.).

```python
class NotifierABC(ABC):
    @property
    @abstractmethod
    def channel(self) -> str: ...      # "email", "webhook", "slack", "dingtalk"
    
    @abstractmethod
    async def notify(self, event: NotificationEvent) -> None: ...
```

**Built-in**: `EmailNotifier`, `WebhookNotifier`. **Planned**: `SlackNotifier`, `DingTalkNotifier`, `PagerDutyNotifier`.

### The "5th candidate": `i18n`

`src/hecate/i18n/` exists with 4 files. It provides translations for Hecate's own UI strings and CLI messages. It's **not yet** a fully-fledged SPI extension point — translations are bundled with Hecate, not contributed by third-party plugins. This is on the  for v1.1.

---

## Plugin lifecycle

A plugin goes through five states, managed by `PluginRegistry` (from `src/hecate/plugin/registry.py`):

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

The lifecycle hooks are in `src/hecate/plugin/lifecycle.py` (optional `PluginLifecycle` protocol). Plugins can implement hooks to be notified of transitions:

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
from hecate.plugin import PluginManifest, PluginContext
from hecate.plugin.spi.evaluator import EvaluatorABC
from hecate.services.evaluation.types import EvalInput, EvalOutput


class DomainSpecificEvaluator(EvaluatorABC):
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
hecate plugin install ./my_evaluator.py
hecate plugin enable domain_specific_score
hecate plugin list --type evaluator
```

### Example: a custom extension (guardrail hook)

The "Extension" plugin type is special — it inherits from `ExtensionPluginABC` and implements any of the four guardrail hooks:

```python
# my_pii_filter.py
from hecate.plugin.types.extension import ExtensionPluginABC
from hecate.engine.guardrail import GuardrailResult, GuardrailAction


class MyPIIFilter(ExtensionPluginABC):
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
# src/hecate/plugin/sdk.py
class PluginContext:
    db_session: AsyncSession          # Database access (subject to permissions)
    audit_log: AuditLogger            # Emit audit events
    config: ConfigProvider             # Read plugin config
    metrics: MetricsExporter           # Export custom metrics
    logger: logging.Logger            # Namespaced logger
    cancel_token: CancelToken          # Cooperative cancellation
```

Plugins should **never** import from `src/hecate/engine/` internals — only from published SPI interfaces and `hecate.plugin.sdk`. This boundary keeps third-party code from coupling to engine internals that may change.

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
| **Engine extension** | 11 Core points (ABC swap) | Decorators / custom node types | Plugin marketplace (DAG-level) | Custom nodes |
| **Platform extension** | 4 SPI + Plugin SDK | Tools, retrievers, vector stores | Marketplace plugins (HTTP-based) | Nodes (npm packages) |
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
│   → Implement EvaluatorABC + PluginManifest
│
├── I want to add a new auth method (OAuth2, mTLS, SAML)
│   → Implement AuthProviderABC + PluginManifest
│
├── I want to add a new notification channel (Slack, PagerDuty)
│   → Implement NotifierABC + PluginManifest
│
├── I want to add a new external channel (Feishu, Discord, Telegram)
│   → Implement ChannelABC + PluginManifest
│
├── I want to inject custom logic into the agent execution (PII filter, custom validation)
│   → Implement ExtensionPluginABC + PluginManifest (auto-wired into all 4 hooks)
│
├── I want to add a new tool that agents can call
│   → Implement ToolPluginABC + PluginManifest, OR use MCP server (preferred for external tools)
│
├── I want to add a new LLM provider
│   → Implement ModelPluginABC + PluginManifest, OR add a LiteLLM adapter (preferred)
│
├── I want to react to events (scheduler triggers, webhooks)
│   → Implement TriggerPluginABC + PluginManifest
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

- `src/hecate/engine/ports.py` — EnginePort ABC
- `src/hecate/engine/worker.py` — Worker + WorkerPool ABCs
- `src/hecate/engine/checkpoint.py` — CheckpointStore ABC
- `src/hecate/engine/eventstore.py` — EventStore ABC
- `src/hecate/engine/context.py` — ContextEngine ABC
- `src/hecate/engine/scheduler.py` — SchedulerStrategy ABC
- `src/hecate/engine/eviction.py` — EvictionPolicy ABC
- `src/hecate/engine/optimization.py` — OptimizationPass ABC
- `src/hecate/engine/guardrail.py` — Pre/Post LLM/Tool Hook ABCs
- `src/hecate/engine/retry.py` — RetryStrategy ABC
- `src/hecate/engine/temporal/conflict.py` — ConflictResolver ABC
- `src/hecate/plugin/__init__.py` — Public SPI exports
- `src/hecate/plugin/manifest.py` — PluginManifest dataclass
- `src/hecate/plugin/registry.py` — PluginRegistry
- `src/hecate/plugin/lifecycle.py` — PluginLifecycle protocol
- `src/hecate/plugin/sdk.py` — PluginContext
- `src/hecate/plugin/spi/evaluator.py` — EvaluatorABC
- `src/hecate/plugin/types/extension.py` — ExtensionPluginABC (4 hooks)
- `src/hecate/plugin/types/tool.py` — ToolPluginABC
- `src/hecate/plugin/types/model.py` — ModelPluginABC
- `src/hecate/plugin/types/trigger.py` — TriggerPluginABC
- `src/hecate/plugin/cli.py` — `hecate plugin` CLI commands
- `src/hecate/plugin/hot_reload.py` — plugin hot-reload
- `src/hecate/plugin/permission.py` — permissions enforcement
- `src/hecate/plugin/loader.py` — plugin loader
- `src/hecate/plugin/installer.py` — plugin installer
- `src/hecate/plugin/validation.py` — manifest validation
- `src/hecate/plugin/packaging.py` — plugin packaging

## Related documents

- [ADR-016: Platform SPI Architecture](adr/016-platform-spi-architecture.md) — the "why" behind this design
- [Reference: Extension Points](../reference/extension-points.md) — the API reference
- [How-to: Develop custom extensions](../how-to/develop-extensions.md) — practical recipe
- [Engine Design](engine-design.md) — the engine layer these extensions plug into
- [Security Architecture](security-architecture.md) — Guardrail Hooks in context
- [Tool Platform Design](tool-platform-design.md) — how SPI plugins relate to MCP/A2A
- Extension SPI Architecture — current design
- [Positioning](positioning.md) — why Hecate's extension model is different from competitors