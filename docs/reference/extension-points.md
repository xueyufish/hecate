# Extension Points Reference

Hecate's engine layer defines a set of abstract interfaces (ABCs) that let you customize every aspect of graph execution — from LLM invocation and tool execution to scheduling, checkpointing, context management, and safety hooks. Each interface ships with a default implementation suitable for testing or single-process use; production deployments provide concrete adapters in the `services/` layer.

The engine has **zero external dependencies** (except `jsonschema` for Graph DSL validation). All abstract interfaces live in `src/hecate/engine/`.

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
| 10 | [Guardrail Hooks](#10-guardrail-hooks) | `guardrail.py` | `on_pre_llm_call`, `on_post_llm_call`, `on_pre_tool_call`, `on_post_tool_call` | `NoOpPreLLMHook`, `NoOpPostLLMHook`, `NoOpPreToolHook`, `NoOpPostToolHook` |
| 11 | [RetryStrategy](#11-retrystrategy) | `retry.py` | `should_retry`, `get_backoff` | `NoRetryStrategy` |

EnginePort also defines **optional SPI methods** with default implementations — see [EnginePort SPI](#engineport-spi-optional-methods).

Additionally, [ConflictResolver](#conflictresolver-temporal) in `temporal/conflict.py` provides concurrent-update resolution for multi-agent scenarios.

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

Abstract interface for persisting and retrieving execution checkpoints. A checkpoint captures the full channel state, the current superstep counter, the executing node, and optional pending writes.

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
| `save` | Persists a checkpoint. Returns its UUID. |
| `load` | Loads by checkpoint_id, or returns the latest checkpoint for the session if `checkpoint_id` is `None`. Returns `None` if not found. |
| `list_checkpoints` | Lists checkpoints ordered by superstep descending (newest first). |

### Built-in implementation: `InMemoryCheckpointStore`

Uses dual storage: a full chronological history per session (for `list_checkpoints` and ID-based `load`) and a latest-only cache (for O(1) latest-checkpoint lookup). Production uses `PostgresCheckpointStore` (in `models/`).

---

## 5. EventStore

**File**: `src/hecate/engine/eventstore.py`

Abstract interface for append-only event persistence. Records granular execution events (node start/end, tool calls, channel writes, interrupts) as a versioned log, complementing CheckpointStore's snapshot model.

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

**File**: `src/hecate/engine/guardrail.py`

Four independent hook types, each intercepting a single point in the agent execution lifecycle. Each hook returns a `GuardrailResult` with an `ALLOW`, `BLOCK`, or `SANITIZE` decision.

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

### PreLLMHook

Intercepts **before** sending messages to the LLM. Use cases: prompt injection detection, PII redaction, content policy enforcement.

```python
class PreLLMHook(ABC):
    @abstractmethod
    async def on_pre_llm_call(
        self, messages: list[dict], model: str, tools: list[dict] | None,
    ) -> GuardrailResult
```

**Default**: `NoOpPreLLMHook` — allows all calls.

### PostLLMHook

Intercepts **after** receiving the LLM response. Use cases: output toxicity detection, sensitive data masking.

```python
class PostLLMHook(ABC):
    @abstractmethod
    async def on_post_llm_call(
        self, response: dict, messages: list[dict],
    ) -> GuardrailResult
```

**Default**: `NoOpPostLLMHook` — allows all responses.

### PreToolHook

Intercepts **before** executing a tool. Use cases: tool authorization, argument validation, dangerous operation blocking.

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

### PostToolHook

Intercepts **after** a tool has executed. Use cases: result validation, evidence tracking, output sanitization.

```python
class PostToolHook(ABC):
    matcher: str | None = None

    @abstractmethod
    async def on_post_tool_call(
        self, name: str, result: Any, context: dict | None,
    ) -> GuardrailResult
```

**Default**: `NoOpPostToolHook` — allows all tool results.

**Integration status**: GuardrailHooks are Worker-level (configured on individual workers), not PregelRuntime-level (P3).

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
