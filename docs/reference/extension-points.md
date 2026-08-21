# Extension Points Reference

Hecate's engine layer defines a set of abstract interfaces (ABCs) that let you customize every aspect of graph execution — from LLM invocation and tool execution to scheduling, checkpointing, context management, and safety hooks. Each interface ships with a default implementation suitable for testing or single-process use; production deployments provide concrete adapters in the `services/` layer.

The engine has **zero external dependencies** (except `jsonschema` for Graph DSL validation). All abstract interfaces live in `src/hecate/engine/`. In addition to the engine ABCs below, the plugin system defines **8 SPI types** (Tool / Extension / Trigger / Model / Channel / Evaluator / Auth / Secret) — see the [Plugin Manifest Schema](plugin-manifest.md).

---

## Extension point inventory

| # | Extension point | File | Abstract methods | Default implementation |
|---|----------------|------|-----------------|----------------------|
| 1 | [EnginePort](#1-engineport) | `ports.py` | `llm_invoke`, `tool_execute`, `knowledge_query`, `checkpoint_save`, `checkpoint_load`, `conversation_load`, `conversation_save`, `create_span`, `end_span` | Services layer adapter |
| 2 | [Worker](#2-worker) | `worker.py` | `execute` | `AgentWorker` (`workers/`) |
| 3 | [WorkerPool](#3-workerpool) | `worker.py` | `dispatch` | `DirectWorkerPool` |
| 4 | [CheckpointStore](#4-checkpointstore) | `checkpoint.py` | `save`, `load`, `list_checkpoints` | `InMemoryCheckpointStore` |
| 5 | [EventStore](#5-eventstore) | `eventstore.py` | `append`, `get_events`, `replay`, `get_version` | `InMemoryEventStore` |
| 6 | [ContextEngine](#6-contextengine) | `context.py` | `select_messages`, `compress`, `estimate_tokens` | `InMemoryContextEngine` |
| 7 | [SchedulerStrategy](#7-schedulerstrategy) | `scheduler.py` | `select_next`, `set_weights` | `FIFOScheduler` |
| 8 | [EvictionPolicy](#8-evictionpolicy) | `eviction.py` | `should_evict`, `select_victim` | `NoEviction`, `SizeBasedEviction` |
| 9 | [OptimizationPass](#9-optimizationpass) | `optimization.py` | `optimize` | `DeadNodeElimination`, `ParallelBranchDetection` |
| 10 | [Guardrail Hooks](#10-guardrail-hooks) | `guardrail.py` (chain in `middleware.py`) | `on_pre_llm_call`, `on_post_llm_call`, `on_pre_tool_call`, `on_post_tool_call` (each wraps as a single stage of an ordered chain) | `NoOpPreLLMHook`, `NoOpPostLLMHook`, `NoOpPreToolHook`, `NoOpPostToolHook`; chain assembly in `services/security/guardrail_assembly.py` |
| 11 | [RetryStrategy](#11-retrystrategy) | `retry.py` | `should_retry`, `get_backoff` | `NoRetryStrategy` |
| 12 | [ChannelBehavior](#12-channelbehavior) | `channel.py` | `initial_value`, `write` | `LastValueBehavior`, `TopicBehavior`, `AccumulatorBehavior` |
| 13 | [DecisionSink](#13-decisionsink) | `decision_sink.py` | `emit` | `NullDecisionSink` |
| 14 | [EventBus](#14-eventbus) | `eventbus.py` | `publish` | `InMemoryEventBus` |
| 15 | [MetricsStore](#15-metricsstore) | `metrics_store.py` | `record_counter`, `record_gauge`, `record_histogram`, `query_metrics`, `get_snapshot` | `InMemoryMetricsStore` |
| 16 | [PolicyLayer](#16-policylayer) | `policy_pipeline.py` | `name`, `evaluate` | Composable layers in `ToolPolicyPipeline` |
| 17 | [SessionStartHook](#17-session-hooks) | `session_hooks.py` | `on_session_start` | `NoOpSessionStartHook` |
| 18 | [SessionEndHook](#17-session-hooks) | `session_hooks.py` | `on_session_end` | `NoOpSessionEndHook` |
| 19 | [UserPromptSubmitHook](#17-session-hooks) | `session_hooks.py` | `on_user_prompt_submit` | `NoOpUserPromptSubmitHook` |
| 20 | [PreCompactHook](#17-session-hooks) | `session_hooks.py` | `on_pre_compact` | `NoOpPreCompactHook` |
| 21 | [SessionStateStore](#21-sessionstatestore) | `session_state.py` | `save`, `load`, `list_recent` | `InMemorySessionStateStore`; production Redis / PostgreSQL / Tiered (`services/session_state/`) |
| 22 | [TaskAllocator](#22-taskallocator) | `task_allocator.py` | `allocate` | `SemanticTaskAllocator`, `RoundRobinTaskAllocator` |
| 23 | [ApprovalCallback](#23-approvalcallback) | `tool_access.py` (emits events via `services/security/approval.py`) | `request_approval` | Fail-closed default (`NoAnswerApprovalCallback` in `guardrail_assembly.py`); real wired implementation emits `APPROVAL_ASKED`/`APPROVAL_DECIDED` event pair |
| 24 | [MiddlewareChain](#24-middlewarechain) | `middleware.py` | `Chain.run` (ordered stages, BLOCK short-circuit, SANITIZE pass-through) | Chain kernel in `middleware.py`; builders in `middleware_factory.py`; legacy hooks adapt via `middleware_adapters.py` |
| 25 | [MonotonicDenialTracker](#25-monotonicdenialtracker) | `monotonic_denials.py` | `deny`, `is_denied` | Per-session in-memory tracker wired by `services/security/guardrail_assembly.py` |
| 26 | [ShellAnalyzer](#26-shellanalyzer) | `shell_analysis.py` | `decompose_command`, `analyze_command` | Module-level pure functions feeding content-aware gating in `tool_access.py` |

EnginePort also defines **optional SPI methods** with default implementations — see [EnginePort SPI](#engineport-spi-optional-methods).

Additionally, [ConflictResolver](#conflictresolver-temporal) in `temporal/conflict.py` provides concurrent-update resolution for multi-agent scenarios (a concrete class, not an ABC).

---

## 1. EnginePort

**File**: `src/hecate/engine/ports.py`

The boundary interface between the execution engine and external capability services (LLM providers, tool runners, knowledge bases, checkpoint storage, conversation history, observability). The engine calls these methods to perform I/O without importing any service module.

This is the **Ports and Adapters** pattern — the engine depends on the abstract port, and production code supplies an adapter wiring each method to the concrete service layer. Unit tests provide lightweight mocks.

### Abstract methods

```python
class EnginePort(ABC):
    @abstractmethod
    def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]
    
    @abstractmethod
    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any
    
    @abstractmethod
    async def knowledge_query(self, query: str, kb_ids: list[UUID]) -> list[dict]
    
    @abstractmethod
    async def checkpoint_save(self, state: dict) -> UUID
    
    @abstractmethod
    async def checkpoint_load(self, checkpoint_id: UUID) -> dict
    
    @abstractmethod
    async def conversation_load(self, session_id: UUID) -> list[dict]
    
    @abstractmethod
    async def conversation_save(self, session_id: UUID, messages: list[dict]) -> None
    
    @abstractmethod
    async def create_span(
        self, name: str, parent_id: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> SpanContext | None
    
    @abstractmethod
    async def end_span(
        self, span_id: str,
        output_data: dict[str, Any] | None = None,
        usage: dict[str, int] | None = None,
    ) -> None
```

### Method details

| Method | Purpose |
|--------|---------|
| `llm_invoke` | Invoke an LLM, yielding token strings as they are generated. `config` carries model name, temperature, etc. |
| `tool_execute` | Execute a registered tool by name with the given arguments. Returns the tool's value. |
| `knowledge_query` | Query knowledge bases by UUID list. Returns document chunk dicts with content and metadata. |
| `checkpoint_save` | Persist execution state. Returns a checkpoint UUID. |
| `checkpoint_load` | Load a previously saved checkpoint by UUID. Returns the state dict. |
| `conversation_load` | Load message history for a session. Returns message dicts in chronological order. |
| `conversation_save` | Persist conversation messages for a session. |
| `create_span` | Create an observability span for tracing. Returns `SpanContext` or `None` if tracing is inactive. |
| `end_span` | Finalize a span, recording optional output and usage data. |

### EnginePort SPI (optional methods)

These methods have default implementations and are only overridden when the corresponding capability is needed:

| Method | Default | Purpose |
|--------|---------|---------|
| `context_assemble(messages, tools, session_id, model)` | Pass-through (returns `{messages, tools, metadata: {}}`) | **Context Engineering** — dynamically assembles optimized context for an LLM invocation based on task phase, budget, and provider. |
| `evidence_query(session_id, min_importance, limit)` | Returns `[]` | **Context Engineering** — retrieves structured tool execution results with provenance tracking. |
| `agent_execute(agent_id, messages, channel_snapshot, context, agent_definition)` | Raises `NotImplementedError` | **Multi-Agent** — executes a sub-agent by ID with isolated context. Concrete adapters MUST override this. |
| `tool_execute_sandbox(name, args, context)` | Falls back to `tool_execute` | **Security** — executes a tool inside a Docker-isolated sandbox container. Uses the sandbox pool when enabled. |
| `workflow_execute(workflow_id, input_data, context)` | Raises `NotImplementedError` | **Workflow Embedding** — enables agents to invoke workflows as callable tools via SkillRegistry. |
| `llm_invoke_structured(messages, config)` | Delegates to `llm_invoke`, yields a single chunk `{"content": <full text>, "tool_calls": None}` | **Tool Calling** — invokes the LLM yielding structured chunks (dicts with `content` and `tool_calls` keys) so the engine's Pregel chat graph can detect tool calls and route through the `check_tools` / `tool_call` loop. Production adapters override it to stream content and accumulate structured `tool_calls`. Non-overriding ports degrade to plain token-stream semantics. |

### SpanContext

`create_span` returns a `SpanContext` dataclass:

```python
@dataclass(frozen=True)
class SpanContext:
    span_id: str
    trace_id: str
    parent_id: str | None = None
```

---

## 2. Worker

**File**: `src/hecate/engine/worker.py`

Abstract interface for executing a single graph node. A Worker receives a node ID, its configuration, and a read-only snapshot of all channels. It returns a `WorkerResult` with channel updates and an optional `Command`.

### Abstract methods

```python
class Worker(ABC):
    def __init__(self, event_store: Any = None) -> None: ...
    
    @abstractmethod
    async def execute(
        self, node_id: str, node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult
```

### Streaming (optional override)

```python
    async def execute_stream(
        self, node_id: str, node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any] | WorkerResult, None]
```

The default `execute_stream` delegates to `execute()` with no intermediate events. Override it for streaming Workers (e.g., LLM nodes that yield tokens). Each yielded dict is forwarded as a `{"type": "message", ...}` event by PregelRuntime. The final yielded value MUST be a `WorkerResult`.

### Built-in implementation: `AgentWorker`

Located in `src/hecate/engine/workers/`. Dispatches execution based on node type (conversation, tool-call, condition, agent, knowledge-retrieval, etc.).

---

## 3. WorkerPool

**File**: `src/hecate/engine/worker.py`

Abstract interface for dispatching worker execution. Controls how workers are scheduled and awaited — implementations may provide direct async, thread-based, or distributed dispatch.

### Abstract methods

```python
class WorkerPool(ABC):
    @abstractmethod
    async def dispatch(
        self, worker: Worker, node_id: str, node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult
```

### Built-in implementation: `DirectWorkerPool`

Awaits each worker directly in the current event loop without parallelism. This is the default pool — it simplifies debugging and avoids race conditions. For production I/O-bound workloads, a thread or process-based pool can be substituted.

---

## 4. CheckpointStore

**File**: `src/hecate/engine/checkpoint.py`

Abstract interface for persisting and retrieving **materialized caches** of execution state. Per [Log-as-Truth (ADR-030)](../design/adr/030-event-sourced-execution-state.md), the execution event log is the source of truth; a checkpoint is a **discardable cache** — a fold of the log (`channel_state` + `log_version` cursor) that makes recovery fast without full replay. A checkpoint captures the full channel state and the `log_version` cursor at materialization time.

### Abstract methods

```python
class CheckpointStore(ABC):
    @abstractmethod
    async def save(
        self, session_id: UUID, superstep: int,
        node_id: str | None, channel_state: dict,
        pending_writes: list | None = None,
        metadata: dict | None = None,
    ) -> UUID
    
    @abstractmethod
    async def load(
        self, session_id: UUID,
        checkpoint_id: UUID | None = None,
    ) -> dict | None
    
    @abstractmethod
    async def list_checkpoints(
        self, session_id: UUID, limit: int = 10,
    ) -> list[dict]
```

| Method | Behavior |
|--------|----------|
| `save` | Persists a materialized cache. Returns its UUID. |
| `load` | Loads by checkpoint_id, or returns the latest cache for the session if `checkpoint_id` is `None`. Returns `None` if not found. |
| `list_checkpoints` | Lists caches ordered by superstep descending (newest first). |

### Built-in implementation: `InMemoryCheckpointStore`

Uses dual storage: a full chronological history per session (for `list_checkpoints` and ID-based `load`) and a latest-only cache (for O(1) latest-checkpoint lookup). Production materializes through `SessionStateMaterializer` (`services/orchestration/`), which implements this ABC and writes through the existing `SessionStateStore` (Redis / PostgreSQL / Tiered); `PostgresCheckpointStore` (in `models/`) is **soft-deprecated** per ADR-030.

---

## 5. EventStore

**File**: `src/hecate/engine/eventstore.py`

Abstract interface for append-only event persistence — the **execution-state source of truth** (Log-as-Truth, [ADR-030](../design/adr/030-event-sourced-execution-state.md)). Records granular execution events (node start/end, tool calls, channel writes with post-adjudication values, `STEP_END` commit points, interrupts) as a versioned log. Channel state is fully reconstructable by folding the log (`fold_session`, `engine/logfold.py`); checkpoints are just materialized caches of that fold.

### Abstract methods

```python
class EventStore(ABC):
    @abstractmethod
    async def append(self, event: Event) -> UUID
    
    @abstractmethod
    async def get_events(
        self, session_id: UUID, from_version: int = 0,
    ) -> list[Event]
    
    @abstractmethod
    def replay(
        self, session_id: UUID, from_version: int = 0,
    ) -> AsyncGenerator[Event, None]
    
    @abstractmethod
    async def get_version(self, session_id: UUID) -> int
```

| Method | Behavior |
|--------|----------|
| `append` | Persist an event. Returns its UUID. |
| `get_events` | Retrieve events for a session from a given version (inclusive). Version-ascending order. |
| `replay` | Yield events as an async stream for incremental replay. |
| `get_version` | Return the current (highest) version number for a session. Returns 0 if no events exist. |

### Optional override: `acquire_event_lock`

```python
@asynccontextmanager
async def acquire_event_lock(
    self, session_id: UUID, *, timeout_ms: int = 30000,
) -> AsyncGenerator[None, None]
```

Default is a no-op. Distributed implementations (e.g., Redis-backed) should override with proper lock semantics.

### Event types

```
NODE_START, NODE_END, TOOL_CALL, TOOL_RESULT, CHANNEL_WRITE,
LLM_REQUEST, LLM_RESPONSE, INTERRUPT, RESUME, ERROR, PII_DETECTED, CUSTOM
```

### Built-in implementation: `InMemoryEventStore`

For testing and single-process use. Production uses `PostgresEventStore`.

---

## 6. ContextEngine

**File**: `src/hecate/engine/context.py`

Abstract interface for context management — message selection, compression, and token estimation. This is the bottom layer for context operations; higher-level orchestration delegates to it.

### Abstract methods

```python
class ContextEngine(ABC):
    @abstractmethod
    def select_messages(
        self, history: list[dict[str, Any]], budget: int,
    ) -> list[dict[str, Any]]
    
    @abstractmethod
    def compress(
        self, messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]
    
    @abstractmethod
    def estimate_tokens(
        self, messages: list[dict[str, Any]],
    ) -> int
```

| Method | Behavior |
|--------|----------|
| `select_messages` | Select messages that fit within the token budget (keeps most recent). |
| `compress` | Reduce token usage by compressing/removing messages. |
| `estimate_tokens` | Estimate total token count for a message list. |

### Built-in implementation: `InMemoryContextEngine`

Simple heuristics: ~4 characters per token for estimation, keeps most recent messages that fit the budget, removes oldest messages beyond the `max_messages` threshold (default 50).

**Integration status**: Wired into `LLMWorker` via `PregelRuntime.execution_context` (Phase 1).

---

## 7. SchedulerStrategy

**File**: `src/hecate/engine/scheduler.py`

Abstract interface for node scheduling. Determines the order in which ready nodes are dispatched each superstep.

### Abstract methods

```python
class SchedulerStrategy(ABC):
    @abstractmethod
    def select_next(self, nodes: list[str], context: dict) -> list[str]
    
    @abstractmethod
    def set_weights(self, weights: dict[str, float]) -> None
```

| Method | Behavior |
|--------|----------|
| `select_next` | Return node IDs in the order they should be executed. |
| `set_weights` | Set execution weights/priorities (higher = higher priority). |

### Built-in implementation: `FIFOScheduler`

Returns nodes in their original input order (first-in, first-out). Ignores weights. This is the default scheduler that preserves sequential execution behavior.

---

## 8. EvictionPolicy

**File**: `src/hecate/engine/eviction.py`

Abstract interface for channel eviction. Controls when and which items are removed from `topic` channels that can grow unboundedly during long-running sessions.

### Abstract methods

```python
class EvictionPolicy(ABC):
    @abstractmethod
    def should_evict(
        self, channel_name: str, current_size: int, context: dict,
    ) -> bool
    
    @abstractmethod
    def select_victim(self, items: list[Any]) -> list[Any]
```

| Method | Behavior |
|--------|----------|
| `should_evict` | Return `True` if eviction should occur for this channel. Called after each write. |
| `select_victim` | Return the list of items to **keep** (items not returned are evicted). |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `NoEviction` | Never evicts — preserves unbounded growth. Default. |
| `SizeBasedEviction(max_size)` | Evicts oldest items when channel exceeds `max_size`. Keeps the most recent items. |

---

## 9. OptimizationPass

**File**: `src/hecate/engine/optimization.py`

Abstract interface for graph optimization. Each pass takes a `CompiledGraph` and returns a new (potentially optimized) `CompiledGraph`. The original is never modified. Passes are applied in list order during compilation.

### Abstract methods

```python
class OptimizationPass(ABC):
    @abstractmethod
    def optimize(self, graph: CompiledGraph) -> CompiledGraph
```

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `DeadNodeElimination` | Removes nodes not reachable from the entry point via BFS. Eliminates orphaned nodes and their edges. |
| `ParallelBranchDetection` | Identifies nodes with multiple outgoing edges where branches are independent (no shared descendants). Marks parallel groups in graph metadata. |

Passes are configured via `GraphCompiler(passes=[...])`.

---

## 10. Guardrail Hooks

**File**: `src/hecate/engine/guardrail.py`, `src/hecate/engine/middleware.py`

Each of the four hook positions is an **ordered middleware chain**. The chain semantics (stage order, BLOCK short-circuit, SANITIZE pass-through, monotonic tightening) are fixed by the engine kernel; stages cannot re-order or skip other stages. The four legacy hook ABCs (`PreLLMHook`, `PostLLMHook`, `PreToolHook`, `PostToolHook`) remain as backward-compatible single-stage adapters wrapped by `HookStageAdapter` so existing implementations slot into a chain without rewrites.

```python
class GuardrailAction(StrEnum):
    ALLOW = "allow"       # execution continues unchanged
    BLOCK = "block"       # execution halts with a reason
    SANITIZE = "sanitize" # execution continues with modified data

@dataclass
class GuardrailResult:
    action: GuardrailAction = GuardrailAction.ALLOW
    reason: str = ""
    modified_data: dict | None = None
```

A SANITIZE result without `modified_data` is treated as a contract violation by the chain kernel and surfaced as BLOCK with the originating stage's identity — silent fall-through to ALLOW is forbidden (see [Concepts: Guardrails](../concepts/guardrails.md#middleware-chain-and-tool-policy)).

### PreLLMHook (legacy single-stage)

Intercepts **before** sending messages to the LLM. Use cases: prompt injection detection, PII redaction, content policy enforcement. Kept as a single-stage adapter for backward compatibility; the production wiring runs it as the first stage of the `AGENT_REQUEST` chain.

```python
class PreLLMHook(ABC):
    @abstractmethod
    async def on_pre_llm_call(
        self, messages: list[dict], model: str, tools: list[dict] | None,
    ) -> GuardrailResult
```

**Default**: `NoOpPreLLMHook` — allows all calls.

### PostLLMHook (legacy single-stage)

Intercepts **after** receiving the LLM response. Use cases: output toxicity detection, sensitive data masking. Backward-compatible single-stage adapter for the `LLM_RESPONSE` chain.

```python
class PostLLMHook(ABC):
    @abstractmethod
    async def on_post_llm_call(
        self, response: dict, messages: list[dict],
    ) -> GuardrailResult
```

**Default**: `NoOpPostLLMHook` — allows all responses.

### PreToolHook (legacy single-stage)

Intercepts **before** executing a tool. Use cases: tool authorization, argument validation, dangerous operation blocking. Adapter for the `TOOL_PRE_EXECUTE` chain.

```python
class PreToolHook(ABC):
    matcher: str | None = None  # tool name pattern: exact, pipe-separated, or regex

    @abstractmethod
    async def on_pre_tool_call(
        self, name: str, arguments: dict, context: dict | None,
    ) -> GuardrailResult
```

The `matcher` class attribute filters which tools trigger the hook. `None` means all tools.

**Default**: `NoOpPreToolHook` — allows all tool calls.

### PostToolHook (legacy single-stage)

Intercepts **after** a tool has executed. Use cases: result validation, evidence tracking, output sanitization. Adapter for the `TOOL_RESULT` chain.

```python
class PostToolHook(ABC):
    matcher: str | None = None

    @abstractmethod
    async def on_post_tool_call(
        self, name: str, result: Any, context: dict | None,
    ) -> GuardrailResult
```

**Default**: `NoOpPostToolHook` — allows all tool results.

**Integration status**: Both the Pregel path (`ToolWorker` / `LLMWorker` / `AgentExecutionPort`) and the path-A direct tool loop (`api/v1/chat.py`) route through the same chain component, assembled by `services/security/guardrail_assembly.py::assemble_guardrails` from the agent's `guardrail_config` and the workspace's policy rule rows. Per-agent scope filtering happens at assembly time; stages that are not enabled for an agent never enter the chain.

---

## 11. RetryStrategy

**File**: `src/hecate/engine/retry.py`

Abstract interface for retry decisions. Implementations decide whether an error warrants a retry and how long to wait before the next attempt. The strategy is **stateless** — the `RetryExecutor` passes the current attempt number (0-based) to each call.

### Abstract methods

```python
class RetryStrategy(ABC):
    @abstractmethod
    def should_retry(self, error: Exception, attempt: int) -> bool
    
    @abstractmethod
    def get_backoff(self, attempt: int) -> float
```

| Method | Behavior |
|--------|----------|
| `should_retry` | Return `True` if the error warrants a retry at the given attempt number. |
| `get_backoff` | Return the delay in seconds before the next attempt. |

### Optional override: `with_config`

```python
def with_config(self, **overrides: Any) -> RetryStrategy
```

Default returns `self` (no-op). Subclasses with configurable parameters override this to produce a new instance with merged settings.

### Built-in implementation: `NoRetryStrategy`

Never retries — errors propagate immediately. This is the default, preserving the pre-retry behavior. The production implementation is `DefaultRetryStrategy` (in `services/`), which uses `ErrorClassifier` for intelligent retry decisions.

**Integration status**: Integrated into PregelRuntime via `RetryExecutor` (Phase 3). Stream-safe — retry only before the first token is yielded.

---

## 12. ChannelBehavior

**File**: `src/hecate/engine/channel.py`

Abstract interface for channel value semantics. Defines how a channel type initializes its value and how writes are merged into the current value during graph execution.

### Abstract methods

```python
class ChannelBehavior(ABC):
    @abstractmethod
    def initial_value(self, defn: ChannelDef) -> Any
    @abstractmethod
    def write(self, current: Any, value: Any, defn: ChannelDef) -> Any
```

| Method | Behavior |
|--------|----------|
| `initial_value` | Produce the channel's initial value from its definition (e.g. empty list for accumulation). |
| `write` | Merge an incoming write into the current value, returning the new value. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `LastValueBehavior` | Overwrites the current value — last write wins. Default for scalar channels. |
| `TopicBehavior` | Appends values to an unbounded list (subject to eviction policy). |
| `AccumulatorBehavior` | Appends values for partial-result channels used by reducer-style nodes. |

**Integration status**: ChannelManager holds a `ChannelBehavior` per channel type; `ChannelManager.write()` delegates merge semantics to the behavior instance.

---

## 13. DecisionSink

**File**: `src/hecate/engine/decision_sink.py`

Abstract interface for security/audit decision recording. Each tool decision (approve/deny/ask) is emitted as an event for the audit trail.

### Abstract methods

```python
class DecisionSink(ABC):
    @abstractmethod
    def emit(self, event: dict[str, Any]) -> None
```

| Method | Behavior |
|--------|----------|
| `emit` | Accept a security audit event dict (keys include `agent_id`, tool metadata, decision, timestamp). MUST be non-blocking — buffer and return immediately; real DB writes happen in a background flush cycle. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `NullDecisionSink` | Discards events silently. Default when no service is registered. |

The production adapter (in `services/`) persists events to the audit log tables. `ToolDecisionEmitter` is the non-abstract helper that wraps a `DecisionSink`.

---

## 14. EventBus

**File**: `src/hecate/engine/eventbus.py`

Abstract interface for publish/subscribe collaboration events between agents. Used by broadcast, debate, and other multi-agent patterns to exchange `CollaborationEvent`s on named topics.

### Abstract methods

```python
class EventBus(ABC):
    @abstractmethod
    async def publish(self, topic: str, event: CollaborationEvent) -> None
    @abstractmethod
    async def subscribe(self, topic: str) -> AsyncIterator[CollaborationEvent]
```

| Method | Behavior |
|--------|----------|
| `publish` | Deliver an event to all subscribers of the topic. |
| `subscribe` | Async iterator yielding events published to the topic. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `InMemoryEventBus` | In-process pub/sub with per-topic subscriber sets. Default for single-process deployments. |

`CollaborationEventType` (StrEnum) enumerates the supported event kinds (e.g. `MESSAGE`, `TASK_ASSIGNED`, `TASK_COMPLETED`, `VOTE`).

---

## 15. MetricsStore

**File**: `src/hecate/engine/metrics_store.py`

Abstract interface for telemetry recording. The engine records counters, gauges, and histograms at every boundary (LLM calls, tool invocations, checkpoint saves) without depending on a specific backend.

### Abstract methods

```python
class MetricsStore(ABC):
    @abstractmethod
    def record_counter(self, name: str, value: float = 1.0, tags: dict[str, str] | None = None) -> None
    @abstractmethod
    def record_gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None
    @abstractmethod
    def record_histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None
    @abstractmethod
    def query_metrics(self, name: str, tags: dict[str, str] | None = None, window: str = "5m") -> list[MetricEntry]
    @abstractmethod
    def get_snapshot(self, windows: list[str] | None = None) -> MetricsSnapshot
```

| Method | Behavior |
|--------|----------|
| `record_counter` | Increment a monotonically increasing counter (optionally by `value`). |
| `record_gauge` | Set a gauge to `value`. |
| `record_histogram` | Record a sample into a histogram. |
| `query_metrics` | Query raw metric entries matching name/tags within a time window. |
| `get_snapshot` | Return an aggregated `MetricsSnapshot` over the requested windows. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `InMemoryMetricsStore` | Ring-buffer storage with rolling aggregates. Default; bounded by `max_buffer_size` (100 000 entries). |

---

## 16. PolicyLayer

**File**: `src/hecate/engine/policy_pipeline.py`

Abstract interface for a single policy decision stage. Layers are composed into a `ToolPolicyPipeline` that evaluates tool-call visibility and execution permissions.

### Abstract methods

```python
class PolicyLayer(ABC):
    @abstractmethod
    def name(self) -> str
    @abstractmethod
    def evaluate(self, context: PolicyContext, decision: PolicyDecision) -> PolicyDecision
```

| Method | Behavior |
|--------|----------|
| `name` | Stable layer identifier (used in logs and error messages). |
| `evaluate` | Inspect/mutate the `PolicyDecision` flowing through the pipeline (e.g. deny, require approval, allow). |

### Built-in implementations

Composable layers built into `ToolPolicyPipeline`, including rule-based allow/deny lists, risk-based approval requirements, and sandbox-scope enforcement. The pipeline exposes `evaluate_visibility` and `evaluate_execution` entry points. `PermissionMode` (StrEnum) selects between `STRICT` and permissive modes.

---

## 17. Session Hooks

**File**: `src/hecate/engine/session_hooks.py`

Four abstract hook interfaces that fire at session lifecycle boundaries, letting extensions observe or veto lifecycle transitions. All return a `HookResult` and may emit a `HookAction` (e.g. allow/deny/redirect). This section covers inventory rows 17–20 — the four interfaces below share one section rather than each getting its own.

### Abstract methods

```python
class SessionStartHook(ABC):
    @abstractmethod
    async def on_session_start(self, context: SessionContext) -> HookResult

class SessionEndHook(ABC):
    @abstractmethod
    async def on_session_end(self, context: SessionContext) -> HookResult

class UserPromptSubmitHook(ABC):
    @abstractmethod
    async def on_user_prompt_submit(self, context: SessionContext) -> HookResult

class PreCompactHook(ABC):
    @abstractmethod
    async def on_pre_compact(self, context: SessionContext) -> HookResult
```

| Hook | Fires |
|------|-------|
| `SessionStartHook` | When a session is created / resumed, before the first LLM call. |
| `SessionEndHook` | When a session ends, before state finalization. |
| `UserPromptSubmitHook` | When a user prompt enters the session, before context assembly. |
| `PreCompactHook` | Before context compaction/offloading kicks in. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `NoOpSessionStartHook` / `NoOpSessionEndHook` / `NoOpUserPromptSubmitHook` / `NoOpPreCompactHook` | Pass-through defaults. |

---

## 21. SessionStateStore

**File**: `src/hecate/engine/session_state.py`

Abstract interface for durable session state (summary, memory, conversation metadata) independent of the checkpoint store. Used by long-running sessions to persist `SessionState` between invocations.

### Abstract methods

```python
class SessionStateStore(ABC):
    @abstractmethod
    async def save(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None
    @abstractmethod
    async def load(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None
    @abstractmethod
    async def list_recent(self, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]
```

| Method | Behavior |
|--------|----------|
| `save` | Persist a session state snapshot. |
| `load` | Retrieve the latest snapshot, or `None` if absent. |
| `list_recent` | Return recent session summaries for a user. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `InMemorySessionStateStore` | Dict-backed store for tests and single-process use. Default. |

Production stores live in `services/session_state/`: Redis, PostgreSQL, and a tiered store combining both. Optimistic concurrency uses `SessionStateConflictError`; sessions that no longer exist raise `SessionNotFoundError`.

---

## 22. TaskAllocator

**File**: `src/hecate/engine/task_allocator.py`

Abstract interface for task-to-agent assignment in dynamic orchestration. Given a task and a set of candidate agents, selects the best-fit agent (creating one if allowed).

### Abstract methods

```python
class TaskAllocator(ABC):
    @abstractmethod
    async def allocate(self, task: str, candidates: list[Any], create_if_not_found: bool = False) -> Any | None
```

| Method | Behavior |
|--------|----------|
| `allocate` | Return the best-fit agent for `task`, or `None` when no candidate matches and creation is disabled. |

### Built-in implementations

| Implementation | Behavior |
|----------------|---------|
| `SemanticTaskAllocator` | Selects via semantic similarity between task description and agent capabilities. |
| `RoundRobinTaskAllocator` | Rotates through candidates deterministically. |

---

## 23. ApprovalCallback

**File**: `src/hecate/engine/tool_access.py`

Abstract interface for human-in-the-loop approval. When a tool call requires approval (per policy/risk level), the engine invokes the callback and blocks until a decision arrives.

### Abstract methods

```python
class ApprovalCallback(ABC):
    @abstractmethod
    async def request_approval(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        risk_level: str,
        context: dict[str, Any],
    ) -> ApprovalDecision
```

| Method | Behavior |
|--------|----------|
| `request_approval` | Block until an `ApprovalDecision` (approve/deny) is produced for the pending tool call. |

### Built-in implementations

**Fail-closed by default**. When no `ApprovalCallback` is configured, `ToolWorker` denies the call with `Tool requires approval but no callback configured` rather than auto-approving — see [Concepts: Guardrails](../concepts/guardrails.md#middleware-chain-and-tool-policy) and the [guardrail-upgrade-trio](../../openspec/changes/archive/2026-08-21-guardrail-upgrade-trio/) 1.3.4 spec. Production deployments wire the callback to the dashboard's human-in-the-loop approval UI; the wiring goes through `services/security/guardrail_assembly.py::assemble_guardrails`. `RiskLevel`, `AccessDecision`, and `ApprovalScope` enums define the policy vocabulary.

The wired callback is responsible for emitting `APPROVAL_ASKED` and `APPROVAL_DECIDED` events to the `EventStore` (enclosed by a `TURN_START` / `TURN_END` window). See [Event Catalog — Engine event log](../reference/event-catalog.md#engine-event-log-event-sourced-state).

### Once-only consumption

An `ONCE`-scoped approval is consumed on first use: a subsequent call for the same `tool_call_id` does NOT reuse the consumed grant. `SESSION`/`PROJECT`/`GLOBAL` scopes only cache when the grant is backed by a durable `APPROVAL_DECIDED` event — an in-memory-only grant is treated as `ONCE`.

---

## 24. MiddlewareChain

**File**: `src/hecate/engine/middleware.py`

The waterfall chain kernel (1.3.5i E3). Each of the four legacy hook positions (see [10. Guardrail Hooks](#10-guardrail-hooks)) hosts an ordered chain of stages. A stage receives `(ctx, call_next)` and must either call `call_next()` — pass-through, optionally with modified data — or short-circuit with a `StageDecision`. The kernel enforces chain-level semantics that stages cannot bypass: BLOCK short-circuits with the originating stage's identity, a SANITIZE without `modified_data` is a contract violation surfaced as BLOCK, and decisions tighten monotonically downstream (a later stage can never loosen an earlier BLOCK). Builders in `middleware_factory.py` assemble chains per agent with scope filtering; legacy `PreLLMHook`/`PostLLMHook`/`PreToolHook`/`PostToolHook` implementations wrap as single stages via `middleware_adapters.py` without code changes.

## 25. MonotonicDenialTracker

**File**: `src/hecate/engine/monotonic_denials.py`

Per-session tracker enforcing the monotonic denial invariant (9.4 content-aware gating upgrade): once `deny(tool_call_id)` is recorded, `is_denied` reports it for the rest of the session — no guard ordering can resurrect a denied call. The `MONOTONIC.DENIAL` log invariant fail-stops execution if a denied `tool_call_id` executes again.

## 26. ShellAnalyzer

**File**: `src/hecate/engine/shell_analysis.py`

Content-aware shell inspection (9.4 content-aware gating upgrade). `decompose_command` performs a quote-aware operator split of a shell command into pipeline segments; `analyze_command` runs dangerous-pattern analysis per segment (command substitution, redirect targets, chained operators). `ToolAccessPolicy._match_dangerous_patterns` routes shell tool arguments through these functions so gating decisions see command **content**, not just the tool's static `risk_level`.

---

## ConflictResolver (Temporal)

**File**: `src/hecate/engine/temporal/conflict.py`

Resolves conflicts when multiple agents update the same channel simultaneously. Unlike the other extension points, `ConflictResolver` is a **concrete class** (not an ABC) — it provides multiple resolution strategies out of the box.

### Resolution strategies

| Strategy | Description |
|----------|-------------|
| `LAST_WRITE_WINS` | The proposed value replaces the current value (default fallback). |
| `MERGE_LIST` | Concatenates lists with deduplication. |
| `MERGE_MAP` | Shallow-merges dicts (proposed overwrites current). |
| `HUMAN_APPROVAL` | Creates a `PendingApproval` entry; external systems (Temporal Signals) resolve via `resolve_approval()`. |
| `DISTRIBUTED_LOCK` | First agent to acquire the lock wins; others must retry. |
| `NEGOTIATION` | Delegates to P2P negotiator (falls back to last-write-wins). |

### Key methods

```python
class ConflictResolver:
    def resolve(
        self, channel_key: str, current_value: Any, proposed_value: Any,
        behavior: Any | None = None, agent_id: str | None = None,
        require_approval: bool = False,
    ) -> ConflictResult
    
    def resolve_approval(
        self, conflict_id: str, approved: bool, approver: str | None = None,
    ) -> ConflictResult
    
    async def resolve_distributed(
        self, channel_key: str, current_value: Any, proposed_value: Any,
        strategy: ConflictStrategy, agent_id: str | None = None,
        lock_ttl: float = 30.0,
    ) -> ConflictResult
```

---

## See also

- **[Graph DSL](graph-dsl.md)** — the JSON format for defining workflow graphs that these extension points execute.
- **[Engine Design](../design/engine-design.md)** — how the Pregel runtime uses these extension points during superstep execution.
- **[Guardrails and Hooks Tutorial](../tutorials/05-guardrails-hooks.md)** — hands-on guide to writing custom guardrail hooks.
- **[Context Engineering Tutorial](../tutorials/07-context-engineering.md)** — observe the context pipeline in action.
- **[Architecture Decision Records](../design/adr/)** — design rationale for each extension point.
