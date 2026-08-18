# Hecate Execution Engine Design

> Deep dive into the Pregel runtime, compiler, channel system, and event-sourced execution state (Log-as-Truth; checkpoints as materialized caches). For a system overview, see [Architecture](architecture.md). For entity definitions, see [Core Concepts](concepts.md).

---

## Design Philosophy

The execution engine is Hecate's heart. It receives compiled Graphs, executes them following the Pregel model, manages state, handles interrupts, and outputs streaming results.

The core design decision is to build a self-contained engine that borrows five design patterns from LangGraph — Channel, Checkpoint, Pregel superstep loop, interrupt/Command, and subgraph composition — without depending on any LangChain code. The engine layer has zero external dependencies except `jsonschema` for DSL validation. This keeps the engine portable, testable, and free from framework coupling.

The engine defines 26 extension interfaces that provide pluggable behavior for every aspect of execution: how nodes are scheduled, how memory is managed, how conflicts are resolved, how events are logged, how requests are guarded, how sessions are stored, and how decisions are audited. Default in-memory implementations are provided for each extension point.

---

## Graph DSL

Graphs are defined as JSON documents conforming to a JSON Schema (bundled in the engine package via `importlib.resources`). The DSL supports ten node types (`conversation`, `tool-call`, `condition`, `agent`, `knowledge-retrieval`, `variable-set`, `fan-out`, `merge`, `suggestion`, `coordinator`) and four channel types.

### Example: Three-Layer Agent Template

```json
{
    "version": "1.0",
    "name": "three-layer-agent",
    "state": {
        "messages": { "type": "topic", "reduce": "append" },
        "current_plan": { "type": "last_value" },
        "iterations": { "type": "accumulator", "initial": 0 }
    },
    "nodes": {
        "guard": {
            "type": "llm",
            "config": {
                "model": "auto",
                "system_prompt": "You are a security guard...",
                "tools": ["content_filter", "risk_assessor"]
            }
        },
        "plan": {
            "type": "llm",
            "config": {
                "model": "auto",
                "system_prompt": "You are a task planner...",
                "tools": ["skill_selector", "task_decomposer"]
            }
        },
        "execute": {
            "type": "agent",
            "config": {
                "skill_ref": "{{ current_plan.selected_skill }}",
                "allowed_tools": "{{ current_plan.allowed_tools }}"
            }
        },
        "should_continue": {
            "type": "condition",
            "config": {
                "expression": "state.iterations < state.max_iterations AND state.current_plan.status != 'done'"
            }
        }
    },
    "edges": [
        { "source": "__start__", "target": "guard" },
        { "source": "guard", "target": "plan" },
        { "source": "plan", "target": "execute" },
        { "source": "execute", "target": "should_continue" },
        {
            "source": "should_continue",
            "targets": {
                "true": "plan",
                "false": "__end__"
            }
        }
    ]
}
```

### Channel Types

Channels are the state management primitive. Each channel type defines write semantics and reduction behavior:

| Type | Semantics | Write Behavior | Typical Use |
|------|-----------|---------------|-------------|
| `last_value` | Keeps the last value | New value overwrites old value | Current plan, current state |
| `topic` | Message stream | Append (supports reducer) | Conversation messages, tool call records, audit logs |
| `accumulator` | Accumulator | Aggregate via specified function | Iteration counter, token usage statistics |
| `accumulator` | Accumulator | Aggregate via specified function | Iteration counter, token usage statistics |

The channel system is managed by a `ChannelManager` with a pluggable `ChannelTypeRegistry`. Writes to unregistered channels are silently skipped; reads from unregistered channels raise `KeyError`. State restoration (from Checkpoint) bypasses write semantics and directly sets the underlying value.

---

## Compiler Pipeline

The compiler transforms Graph DSL (JSON) into a runtime-executable `CompiledGraph` through five stages:

```
JSON DSL
  │
  ├── 1. Schema Validation
  │     └── Validate node types, edge connections, channel definitions
  │
  ├── 2. Dependency Analysis
  │     └── Build node dependency graph, detect cycles
  │
  ├── 3. Channel Binding
  │     └── Analyze each node's read/write channels, verify type compatibility
  │
  ├── 4. Compilation Optimization
  │     └── Dead node elimination (BFS from entry point), parallel branch detection
  │
  └── 5. Output CompiledGraph
        ├── nodes: Map[NodeId, CompiledNode]
        ├── edges: Map[NodeId, List[CompiledEdge]]
        ├── channels: Map[ChannelName, ChannelInstance]
        └── entry_point: NodeId
```

Unreachable nodes are detected via BFS from the entry point. The compiler logs a warning for each unreachable node but does not raise an error — this allows work-in-progress graphs to compile.

---

## Pregel Runtime

The execution engine adopts the Pregel/BSP (Bulk Synchronous Parallel) model. The scheduler runs in a single process and orchestrates superstep loops:

```
┌──────────────────────────────────────────────────────┐
│                  Pregel Scheduler (single-process)    │
│                                                       │
│  1. READ: Each node reads current Channel values      │
│  2. DISPATCH: Dispatch ready nodes to Worker Pool     │
│  3. AWAIT: Wait for all Workers to return results     │
│  4. WRITE: Each node writes new Channel values        │
│  5. CHECKPOINT: Persist current state                 │
│  6. ROUTE: Determine next step based on conditional   │
│     edges                                             │
│  7. CHECK: Are there still ready nodes?               │
│     ├── YES → Go back to Step 1                      │
│     └── NO → Execution complete                      │
│                                                       │
│  Can be paused at any time via interrupt()            │
└──────────────────────────────────────────────────────┘
```

### Worker Pool

Node execution (LLM calls, tool execution, code execution) is dispatched to a Worker Pool. The scheduler owns all Channel and Checkpoint state — workers are stateless and receive only read-only Channel snapshots.

**Design constraints**:

- Workers only receive Channel snapshots (read-only). They never directly modify Channels.
- Checkpoint persistence is controlled exclusively by the scheduler. Workers are unaware of checkpoints.
- When a worker calls `interrupt()`, it notifies the scheduler via `WorkerResult.status = "interrupted"`. The scheduler pauses the loop.
- Workers are stateless. After a restart, they can be rescheduled by the scheduler based on Checkpoint recovery.

This separation ensures the scheduler is the single source of truth for state, while workers can scale horizontally — from in-process threads to cross-process workers to distributed backends.

### Execution Flow Example

Here's how the three-layer Agent template executes across supersteps:

```
Superstep 1:
  READ:   messages = [user_input]
  EXECUTE: guard node → security check + risk assessment
  WRITE:  guard_result = {safe: true, risk: LOW}
  CHECKPOINT: #1

Superstep 2:
  READ:   messages + guard_result
  EXECUTE: plan node → task decomposition + Skill selection
  WRITE:  current_plan = {skill: "developer", tasks: [...]}
  CHECKPOINT: #2

Superstep 3:
  READ:   messages + current_plan
  EXECUTE: execute node (developer Sub-Agent) → execute task
  WRITE:  messages.append(assistant_response), iterations += 1
  CHECKPOINT: #3

Superstep 4:
  READ:   iterations + current_plan
  EXECUTE: should_continue node → condition evaluation
  WRITE:  routing result = true/false
  → If true: go back to Superstep 2 (Plan re-evaluation)
  → If false: execution complete
```

---

## Checkpoint Persistence

> **1.3.19 update (log-as-truth)**: Per [ADR-030](adr/030-event-sourced-execution-state.md) the checkpoint is **demoted to a materialized cache** of the event log. The log is authoritative; the cache is discardable. See [Event Store §](#event-store) for the primary mechanism.

The Checkpoint system provides **durable state persistence** for agent execution sessions. As of 1.3.19 it acts as a hot-path cache; the canonical record is the event log (see [Event Store](#event-store)). Materialization cadence: turn normal-end / interrupt / every N supersteps (default N=10). Each materialization carries only `channel_state` (with bounded-retainer projection of oversized values) + `log_version` (the event-store cursor at materialization time).

**Design principles (revised)**:

1. **Log is the source of truth** — the event log is rebuilt to recover; the cache is an optimization. Never partial-prune the log (fold-from-origin requires the full prefix).
2. **Materialization seam** — production code routes through `SessionStateMaterializer` (services/orchestration/), which implements the engine `CheckpointStore` ABC and writes through the existing `SessionStateStore` (Redis / PostgreSQL / Tiered) under the tenant triple `(org_id, user_id, session_id)`. Tenant context injected via `tenant_context_provider` closure (same pattern as `PostgresEventStore`).
3. **Bounded payload** — `channel_state` values over the budget (default head 32KB / tail 8KB) are replaced with `{"_omitted": True, "_prefix": ..., "_suffix": ..., "_omitted_bytes": N}` markers (dsh TextRetainer pattern). Recovery can fetch the original from the environment / offload via the marker.
4. **`PostgresCheckpointStore` is soft-deprecated** — kept for backward compatibility with persisted data in `checkpoints`; hard removal (and the `checkpoints` table drop) is deferred to a follow-up cleanup change, per the 13.4a-6/-7 two-stage precedent.

### Recovery Flow (cache + tail replay)

```
Session interrupted → Resume request hits /api/sessions/{id}/resume
  │
  ├── 1. Validate via event log: tail must have unclosed INTERRUPT event
  ├── 2. Load materialized cache from SessionStateStore (via materializer)
  ├── 3. If event_store wired, replay events with version > cache.log_version
  │        (fold via ChannelBehavior.write — same path as live mutation)
  ├── 4. Boundary equivalence assertion: fold full log, compare to cache
  │        → mismatch = RuntimeError("[PROJECTION.EQUIVALENT] ...") — fail-stop
  ├── 5. Reconstruct runtime with recovered state
  └── 6. Continue Pregel loop from interrupted node (route derived from
         last INTERRUPT event's CHANNEL_WRITE delta — log-derived, not metadata)
```

A checkpoint / cache record carries: session ID, superstep, current node, all channel values, and metadata (log_version anchor). `superstep` / `interrupted_node` / `interrupt_value` / route are **derived from the log** on restore, not stored in metadata.

---

## Human-in-the-Loop: interrupt and Command

### interrupt

Nodes can call `interrupt(value)` to pause execution and return control to the user. This is the mechanism for human approval workflows:

```python
def approval_node(state):
    if state["risk_level"] == "HIGH":
        user_decision = interrupt({
            "type": "approval",
            "operation": state["pending_operation"],
            "risk_level": "HIGH",
            "message": "This operation requires your approval"
        })
        if user_decision == "deny":
            return {"status": "cancelled"}
    return {"status": "approved"}
```

### Command

Nodes can return `Command` objects to control execution flow — handoff to another agent, resume an interrupted session with user input, or redirect the execution path:

```python
# Handoff to another Agent
Command(goto="other_agent", update={"context": "handoff data"})

# Resume interrupted execution
Command(resume=value, update={"user_decision": "approved"})
```

---

## Subgraph Composition

Subgraphs allow nesting one Workflow inside another. The outer graph contains a `subgraph` node that references an inner graph. State is mapped between the two scopes:

```
Outer Graph:
  ├── Node A
  ├── SubGraph B (Inner Graph)
  │     ├── Node B1
  │     ├── Node B2
  │     └── Node B3
  └── Node C

State Mapping:
  Outer Channel → Inner Channel (input mapping)
  Inner Channel → Outer Channel (output mapping)
```

Channels inside subgraphs use namespace prefixes (e.g., `subgraph_b.messages`) to avoid conflicts with the outer layer. This enables the Agent-as-Tool pattern, where an entire Agent's workflow is embedded as a single node in a parent workflow.

---

## Strategy System

The engine provides a pluggable strategy system for resilient execution:

- **Retry** — Exponential backoff with configurable max attempts, jitter, and custom predicates. Applied to LLM calls and tool execution.
- **Timeout** — Node-level and global timeouts. When a node exceeds its deadline, the worker is cancelled and the result is marked as timed out.
- **Fallback** — Primary model failure triggers automatic fallback to a configured secondary model, then to a default response.
- **Circuit Breaker** — Per-prefix circuit breaker for LLM routing. Tracks failure rates and transitions between CLOSED → OPEN → HALF_OPEN states. When OPEN, requests fail fast instead of waiting for timeouts.

### RetryStrategy (Extension Point #11)

The `RetryStrategy` abstract base class defines the retry policy as the engine's 11th Core extension point. It is integrated into the Pregel runtime via `RetryExecutor`, which wraps LLM calls and tool executions with retry logic:

| Method | Purpose |
|--------|---------|
| `should_retry(exception, attempt) -> bool` | Determine if the operation should be retried based on exception type and attempt count |
| `get_backoff(attempt) -> float` | Calculate backoff delay (seconds) for the given attempt |
| `with_config(max_attempts, backoff_strategy, jitter) -> RetryStrategy` | Return a new instance with modified configuration |

The default `NoRetryStrategy` never retries. Built-in implementations include `ExponentialBackoffStrategy` (exponential backoff with jitter) and `LinearBackoffStrategy` (linear backoff with configurable step). Custom predicates allow fine-grained control — for example, retrying only on `RateLimitError` but not on `AuthenticationError`.

---

## Streaming Output

The execution engine supports multiple streaming modes, allowing clients to subscribe to different levels of execution detail:

| Mode | Output Content | Use Case |
|------|---------------|----------|
| `values` | Complete state after each superstep | Debugging, state monitoring |
| `updates` | Incremental updates per superstep | Progress display |
| `messages` | LLM-generated token stream | Frontend real-time response display |
| `debug` | Internal execution details (tool calls, channel changes) | Development debugging |
| `custom` | User-defined streaming output | Custom events |

Clients specify desired stream modes in the `ExecutionRequest`. Multiple modes can be combined — for example, a frontend might subscribe to `messages` for real-time token display while simultaneously receiving `updates` for progress tracking.

---

## Event Store

> **1.3.19 update (log-as-truth)**: Per [ADR-030](adr/030-event-sourced-execution-state.md) the EventStore is promoted from observation log to **execution-state source of truth**. The `CHANNEL_WRITE` payload now carries the **post-adjudication** value (not just channel names). New event types (`STEP_END`, `EVICTION`, `CHANNEL_WRITE_REJECTED`, `SUBGRAPH_START`, `SUBGRAPH_END`) anchor commit points and subgraph dispatch.

The `EventStore` extension point provides append-only event logging with replay capability. As of 1.3.19 events carry enough information to **fully reconstruct channel state** via the `fold_session` machine (`engine/logfold.py`). This enables:

- **Cross-process recovery** — interrupt state survives process restart because the log is the durable record.
- **Audit trails** — every LLM request, tool call, and channel mutation is captured with payload.
- **Execution Replay** (8.20) — value-carrying log + OTel spans power a trace-partitioned timeline UI (vocabulary: `session` → `trace` → `event`; no "runId").
- **Projection Registry** (8.21) — `derive_messages()` is the first projection; future projections layer on top of the same fold.

### Event grammar (1.3.19)

Two layers:

* **Channel-delta layer (general)** — `CHANNEL_WRITE {channel, value, log_schema_version=2}`, `CHANNEL_WRITE_REJECTED` (audit only; fold skips).
* **Session-semantic layer** — `STEP_END` (commit point), `EVICTION` (fact event with `drop_indices`), `INTERRUPT {interrupt_value}`, `RESUME`, `LLM_REQUEST {messages, tools, prompt_id, prompt_version, model}`, `LLM_RESPONSE`, `NODE_START`, `NODE_END`, `TOOL_CALL`, `TOOL_RESULT`, `ERROR`, `PII_DETECTED`, `SUBGRAPH_START {child_session_id}`, `SUBGRAPH_END {child_session_id}`, `CUSTOM`.

Unknown historical types fall back to `CUSTOM` on read (additive schema evolution).

### Write-ahead ordering (WAL)

In each superstep: results collect → conflict adjudication → **single-transaction batched `append_batch`** of channel-delta events + `STEP_END` → channel apply. `STEP_END` and `INTERRUPT` are commit points. A torn tail (`CHANNEL_WRITE` without a commit after it) rolls back to the last complete superstep on restore. This is one DB round-trip per superstep, replacing the old per-superstep full snapshot.

### LogPolicy — default-in with blacklist

Channels are logged by default. A single `engine/logpolicy.py` registry excludes three classes:

1. **Ephemeral structural intermediates** — `_fanout__*`, `_resume_value`
2. **Re-injectable control channels** — `_`-prefixed and `sys.`-prefixed (services re-inject per request)
3. **Channels with dedicated persistence** — `_agent_state` (live object stored via `SessionStateStore`)

`_route` is explicitly **included** (conditional-edge routing is part of fold correctness).

### LogInvariants (runtime invariant checker)

`engine/loginvariants.py` registers fold-time checks via decorator (dsh companion-module pattern). Shipped: `STEP.BOUNDARY`, `TOOL.PAIRING` (pending tool calls cannot cross STEP_END), `DISPATCH.TREE` (every `SUBGRAPH_START` has matching `SUBGRAPH_END`). Adding a check = `@loginvariants.register("CODE", "description")` then implementing the closure.

### Boundary equivalence assertion (fail-stop)

On restore / interrupt / materialization: fold full log, compare to `ChannelManager` snapshot (non-LogPolicy channels only). Mismatch = `RuntimeError("[PROJECTION.EQUIVALENT] channel '<name>' diverged between cache and log fold; failing closed per log-as-truth invariant")`. In-process state divergence is a bug; recovery is "discard + re-fold from log", not hot-fix.

---

## Guardrail Hooks

Four guardrail hook types provide interception points at the boundaries of LLM and tool execution:

- **PreLLMHook** — Called before each LLM invocation. Can modify the prompt, reject the request, or inject additional context.
- **PostLLMHook** — Called after each LLM response. Can filter output, redact sensitive information, or trigger re-generation.
- **PreToolHook** — Called before each tool execution. Can validate parameters, check permissions, or reject the call.
- **PostToolHook** — Called after each tool returns. Can validate results, sanitize output, or log usage.

Each hook type has a `NoOp` default implementation. Custom hooks are registered at the Worker level.

---

## A2A Protocol Integration (Planned)

The A2A (Agent-to-Agent) protocol integration will add cross-framework agent communication capabilities to the engine layer:

**A2A Server**: Expose Hecate agents as A2A-compliant services. The engine will register agents with Agent Cards (`/.well-known/agent.json`) and handle incoming task requests via the A2A task lifecycle (submitted→working→completed/failed).

**A2A Client**: Consume external A2A agents as remote sub-agents. The engine will provide a `RemoteA2aAgent` abstraction that handles Agent Card discovery, task delegation, and artifact exchange. This extends the existing sub-agent mechanism (Agent-as-Tool) to work across frameworks.

**Integration Point**: A2A integration will be wired through the `EnginePort` interface, similar to how LLM/Tool/Knowledge integrations work. The engine will not import A2A-specific code directly — the service layer will provide the A2A adapter.

**Task Lifecycle**: A2A tasks map to the engine's existing `Session` concept. A task's state machine (submitted→working→completed/failed) aligns with the session lifecycle (active→interrupted→completed/failed).

See [ADR-011: A2A Protocol Adoption](adr/011-a2a-protocol-adoption.md) for the full decision record.

---

## Simulation Environment (Planned)

The **Simulation Environment** enables agents to execute and reason without affecting real systems. It provides:

- **Isolated Sandbox**: A copy of the runtime environment where actions can be tested
- **Dry-Run Execution**: Run workflows and actions without side effects
- **What-If Analysis**: Test different scenarios and observe outcomes
- **Validation**: Verify agent behavior before production deployment

The simulation environment builds on the existing Docker sandbox and checkpoint system, adding a "simulation mode" flag that prevents write-back to external systems.

---

## 5-Level Intent Recognition (Planned)

The **5-Level Intent Recognition** system provides hierarchical understanding of user intent:

1. **Atomic Intent** — Single user query (e.g., "show me the report")
2. **Workflow Intent** — Multi-step complex task (e.g., "generate quarterly report and send to stakeholders")
3. **Session Intent** — Overall dialogue goal (e.g., "analyze Q4 performance")
4. **Domain Intent** — Business domain context (e.g., "financial analysis")
5. **Meta Intent** — Platform-level intent (e.g., "optimize agent performance")

Each level informs the next, enabling more accurate routing and context gathering.

---

## Dynamic Orchestration (shipped)

The **Dynamic Orchestration** capability adds the seventh multi-agent pattern: a COORDINATOR node that turns a goal plus an agent roster into a runtime task DAG, dispatches workers in an isolated child session, and folds their outputs through a synthesis step.

- **Coordinator node type**: `NodeType.COORDINATOR` lives alongside `AGENT`, `FAN_OUT`, `MERGE`, etc. (`src/hecate/engine/types.py`).
- **TaskDAG schema**: planner-facing Pydantic model in `src/hecate/engine/dynamic_types.py` — `goal`, `tasks`, `dependencies`, `synthesis_prompt`, `synthesis_transform`, `verify`, `budgets`. The LLM writes intent; the executor materialises the graph.
- **Executor template**: `build_dynamic_orchestration_executor(dag, roster)` in `src/hecate/engine/templates.py` produces a `GraphConfig` whose nodes and channels are uniquely named (`task_<task_id>_<revision>`, `<task_id>.<expected_output>`). Dependency levels exceeding `max_concurrent` are split into sequential batches chained by FAN_OUT/MERGE pairs.
- **Magentic double-loop** (`src/hecate/engine/workers/coordinator_worker.py`): outer-loop replan (each iteration's prompt is rebuilt from goal + roster + ledger summary, never from prior reasoning tokens); inner-loop dispatch + evaluator five-question stall counter (`≤ budgets.stall_limit` defaults to 2; over the limit triggers `stall_cap_exceeded` and a best-effort return).
- **Three-axis budget + additive stop_reason**: `max_iterations`, `max_total_tasks`, `max_concurrent`, `stall_limit`, `token_budget`. Capped partial results emit an `ORCHESTRATOR_EVALUATION` with `stop_reason ∈ {token_capped, turn_capped, loop_capped, stall_capped}` plus the guidance string `"reuse it, retry tighter, or raise budget"` so the next iteration's planner does not mistake them for clean completions. Status enum stays unchanged — DeerFlow Phase 2 lesson.
- **Five-isolation properties** for the child session: distinct `child_session_id`; sub-graph's long-term memory writes keyed by the child id, not the parent thread; explicit `channel_mapping.input` (unregistered parent channels raise `KeyError`); explicit `channel_mapping.output` (sub-graph `messages` never copied to parent); failure authority limited to `WorkerResult.error` and the status contract (output text is never parsed).
- **Fail-closed pre-dispatch validation** (`src/hecate/engine/orchestrator_validator.py`): `validate_task_requirements(dag, roster)` rejects cycles, missing roster entries, capability gaps, and dangling references. Called from both CoordinatorWorker and the canvas save path.
- **Benefit-based delegation rubric** (`src/hecate/engine/dynamic_types.py::BENEFIT_BASED_DELEGATION_RUBRIC`): public constant embedded in the planner system prompt; byte-level snapshotted by `tests/test_coordinator_prompt.py`.
- **Event source-of-truth** (per ADR-030): two new additive `EventType` values, `ORCHESTRATOR_DECISION` (plan revision + dag + reasoning) and `ORCHESTRATOR_EVALUATION` (typed blocker + stop_reason + guidance_string), are emitted into the EventStore by default; `LogPolicy` does not exclude them. `SUBGRAPH_START`/`SUBGRAPH_END` reference pairs anchor child sessions. Fold-to-version reconstructs the plan-revision history.

**References**: [ADR-032](adr/032-dynamic-orchestration.md), [ADR-030](adr/030-event-sourced-execution-state.md), [ADR-007](adr/007-multi-agent-as-graph-templates.md), [ADR-001](adr/001-graph-first-orchestration.md).

**Deferred follow-ups**: append-only `PlanPatch` repair API (OMA v1.14 semantics); full consensus proposer→judge; async orchestration + mid-flight steering; plan-freeze artifact + exact replay; pattern-selector seventh entry, canvas `COORDINATOR` node, replay coordinator-card expansion. See [feature-catalog.md](../features/feature-catalog.md) for the full deferral table.

---

## Agentic RL Integration (Planned)

The engine will integrate with the Agentic RL Framework through:

- **Trace Hooks**: EventStore integration for capturing execution traces
- **Reward Signals**: Engine exposes success/failure signals for RL training
- **Policy Evolution**: Engine supports runtime policy updates from RL optimization
- **A/B Testing**: Engine supports running multiple agent configurations simultaneously for comparison

See [ADR-013: Agentic RL Framework](adr/013-agentic-rl-framework.md).

---

## Knowledge Graph Integration (Planned)

The engine will access structured knowledge via the `GraphStore` ABC (see [ADR-017](adr/017-knowledge-graph-architecture.md)) through the existing `EnginePort.knowledge_query()` interface. This extends the knowledge access layer beyond vector-based RAG:

- **Entity-centric retrieval**: `knowledge_query()` routes entity/relation queries to the GraphStore backend (Neo4j or in-memory)
- **Multi-hop traversal**: `GraphStore.get_neighbors(entity_id, depth)` enables multi-hop reasoning across connected entities
- **Community-level retrieval**: `GraphStore.detect_communities(algorithm)` clusters related entities for GraphRAG — broader context than chunk-level retrieval
- **OAG integration**: The Ontology-Augmented Generation pipeline (ADR-015) combines RAG retrieval + ontology logic + action execution, all routed through `EnginePort`

The engine itself remains graph-database-agnostic — the service layer provides the `Neo4jGraphStore` adapter, and the engine calls only the abstract `GraphStore` interface via `EnginePort`.

---

## Asynchronous Execution Mode (Planned)

The **Asynchronous Execution API Mode** (1.3.11) provides a third execution mode for long-running workflows (minutes to days):

```
Client → POST /api/workflows/{id}/execute → { task_id, status: "submitted" }
Client → GET /api/tasks/{task_id} → { status: "running" | "completed" | "failed", result }
Client → DELETE /api/tasks/{task_id} → { status: "cancelled" }
```

| Mode | Duration | Connection | Use Case |
|------|----------|------------|----------|
| Synchronous | < 60s | Blocking HTTP | Quick Q&A, API calls |
| Streaming | < 15min | Persistent SSE | Real-time chat, progress display |
| Asynchronous | 15min - 24h+ | Fire-and-forget + poll/webhook | Batch processing, report generation, multi-round research |

Task lifecycle: `submitted → running → completed/failed/cancelled`. The async mode wraps the existing Pregel runtime in a background task runner — the engine itself is unchanged. See [ADR-020](adr/020-async-execution-distributed-state.md).

---

## Distributed Session State (Planned)

The **Distributed Session State Store** (13.4a) enables multi-replica horizontal scaling with Redis-backed hot-path caching:

```
Read:  Redis cache (0.5ms) → cache miss → PostgreSQL checkpoint (5ms) → cache in Redis
Write: Every superstep → PostgreSQL (durable) → Redis cache (hot-path)
```

`SessionStateStore` ABC with `InMemorySessionStateStore` (development) and `RedisSessionStateStore` (production). Any replica can serve any session — no sticky sessions required. See [ADR-020](adr/020-async-execution-distributed-state.md).

---

## Composable ReAct Loop Middleware (Planned)

The **ReAct Loop Middleware** (E3 enhancement to Deterministic Hooks) adds stackable hooks at the reasoning-acting cycle level, beyond the fixed 4 Guardrail Hook types:

| Middleware Position | Fires When | Example Use |
|--------------------|-----------|-------------|
| `on_reasoning` | Before LLM reasoning step | Log reasoning chain, inject context |
| `on_acting` | Before tool execution | Check resource limits, audit actions |
| `on_reply` | After agent generates reply | Post-process response, update metrics |
| `on_model_call` | Before/after each LLM call | Cost tracking, provider-specific shaping |

Middleware chains compose like Python decorators — multiple middlewares can be stacked without modifying the engine. This complements the existing Context Engine Processor Chain (4.13) with reasoning-loop-level hooks.

---

## Saga/Compensation Pattern (Planned)

The **Saga/Compensation Pattern** (E4 enhancement to Temporal Conflict Resolution) provides automatic rollback for failed multi-step workflows:

```
Step 1: Create User → ✅
Step 2: Provision Account → ✅
Step 3: Send Welcome Email → ❌ FAILED

→ Compensation triggered:
   Step 2 rollback: Unprovision account
   Step 1 rollback: Delete user
→ Workflow status: "rolled_back"
```

Leverages the existing Temporal integration (13.10) to provide saga-level durability — automatic retry with compensation logic, long-running workflow support, and step-level rollback. Each node can declare an optional `compensation` action that executes in reverse order if a downstream node fails.

---

## Checkpoint Branching for What-If Analysis (Planned)

The **Checkpoint Branching** (E5 enhancement to Simulation Environment) creates new execution sessions from historical checkpoints with modified state:

```
Original Session (Session A):
  Checkpoint #1 → Checkpoint #2 → Checkpoint #3 → completed

What-If Branch (Session B):
  Fork from Checkpoint #2 (modify state: change user_input)
  → New execution path → different outcome
```

This enables parallel "what-if" scenario testing without affecting the original session. Branches are independent sessions with their own checkpoint chains, referencing the original checkpoint as their fork point.

---

## Worker Pool Auto-Scaling (Planned)

The **Worker Pool Auto-Scaling** (E6 enhancement to Progressive Worker Pool) adds runtime scaling parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_workers` | 4 | Minimum number of active workers |
| `max_workers` | 100 | Maximum number of workers |
| `scale_up_threshold` | 10 | Queue length that triggers scaling up |
| `scale_down_idle_time` | 60s | Idle time before scaling down a worker |

Auto-scaling applies to the cross-process and distributed Worker Pool tiers. The in-process thread pool uses a fixed pool size (configurable per session).

---

## Multi-Agent Handoff

The system supports **dynamic agent handoff** — a source AGENT node delegates control to a target AGENT node at runtime. The LLM decides which specialist agent should continue the conversation by calling a special `handoff_to_agent` tool.

### Execution Path

```
PregelRuntime._dispatch_node()
  │  Checks compiled edges for handoff/dynamic_handoff triggers
  │  Injects handoff_targets into execution_context
  ▼
AgentWorker._handle_direct_mode()
  │  Passes handoff_targets via context to port
  ▼
AgentExecutionPort.agent_execute()
  │  Injects handoff_to_agent tool into LLM tool list
  │  Tool has target enum = list of valid target node IDs
  ▼
LLM responds with handoff_to_agent(target="billing_agent")
  │
  ▼
AgentExecutionPort validates target, returns handoff_to
  │
  ▼
AgentWorker detects handoff_to, calls build_handoff_channel_updates()
  │  Returns WorkerResult(command=Command(goto=target))
  ▼
PregelRuntime reads Command(goto=...), dispatches target node next superstep
```

### Context Mode Strategies

The `handoff.context_mode` field on the source AGENT node config controls how much conversation context the downstream agent receives:

| Mode | Default | Behavior |
|------|---------|----------|
| `inherited` | ✅ | Full message history passed through unchanged. Downstream agent sees everything. |
| `isolated` | — | Only the AIMessage + ToolMessage handoff pair plus a `"Handed off from {source}"` system note. Fresh start. |
| `summarized` | — | Prior history collapsed into a single system message with structured fields: `from`, `intent`, `key_facts`, `open_questions`. |

**Default**: When `handoff.context_mode` is absent, the runtime treats it as `"inherited"`.

### AIMessage + ToolMessage Pairing Contract

Every handoff produces exactly two messages written to the `messages` channel:

1. **AIMessage** — the LLM's original tool-call message with `tool_call_id` preserved
2. **ToolMessage** — a synthetic acknowledgment with matching `tool_call_id` and content `"Handed off to {target_node_id}"`

This pairing ensures downstream agent APIs see a well-formed conversation (no orphaned tool calls). If the LLM provider returns duplicate `tool_call_id` values, the second occurrence is renamed with a UUID suffix.

### DSL Example

```json
{
  "nodes": {
    "triage": {
      "type": "agent",
      "config": {
        "agent_id": "uuid-triage",
        "handoff": {
          "context_mode": "summarized",
          "description": "Route to the right specialist"
        }
      }
    },
    "billing": {
      "type": "agent",
      "config": {
        "agent_id": "uuid-billing"
      }
    },
    "tech": {
      "type": "agent",
      "config": {
        "agent_id": "uuid-tech"
      }
    }
  },
  "edges": [
    {"source": "triage", "target": "billing", "trigger": "handoff"},
    {"source": "triage", "target": "tech", "trigger": "handoff"}
  ]
}
```

### Static vs Dynamic Handoff

| Aspect | Static (`trigger: "handoff"`) | Dynamic (`trigger: "dynamic_handoff"`) |
|--------|------|---------|
| Target | Single node ID | Dict of `{source_label: target_node_id}` |
| Tool enum | One value | Multiple values (all dict values) |
| Per-target description | Single fallback | Each target gets its own description |
| Use case | Fixed routing | LLM selects from multiple specialists |

The per-target description priority is: `handoff.description` (node-level override) > `AgentModel.description` > node `name`.

---

## Further Reading

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | System overview, design principles, module architecture |
| [Agent Studio Design](agent-studio-design.md) | Visual canvas, multi-agent orchestration, NL2X, visual node types |
| [Security Architecture](security-architecture.md) | Guardrail hooks deep dive, PII anonymization, audit system |
| [RAG Pipeline Design](rag-pipeline-design.md) | Knowledge retrieval pipeline, hybrid search, citations |
| [Access Channel Design](access-channel-design.md) | API surfaces, authentication, gateway control plane |
| [Core Concepts](concepts.md) | Entity definitions, relationships, data model |
| [ADR Directory](adr/) | Architecture Decision Records |
| [Graph DSL Schema](../../src/hecate/engine/graph-dsl.schema.json) | JSON Schema for graph definition |
