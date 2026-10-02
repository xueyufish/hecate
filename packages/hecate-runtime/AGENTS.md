# runtime/ — agent runtime domain

The runtime domain implements the agent runtime: the execution engine plus the
agent-execution semantics layered on top of it (renamed from `engine/` when the
domain scope grew beyond the bare engine). Terms are used with fixed meanings:

- **Execution engine** — the graph-execution mechanism: Pregel/BSP superstep
  loop, ChannelManager, EventStore, CheckpointStore, compiler, Worker ABC.
  Agent-agnostic; also executes pure workflows.
- **Agent runtime** — engine + agent-execution semantics: production workers
  (LLM / Tool / Condition / Coordinator), retry, interrupt/approval, streaming
  modes, session state, handoff. Containment: engine ⊂ runtime.

Full definition and naming discipline: `docs/design/engine-design.md`
("Terminology: Engine vs. Runtime").

Zero external dependencies on other domains and workspace wheels — pinned by a
subprocess probe. `__init__.py` is deliberately empty: import directly from
submodules (`from hecate.runtime.pregel import PregelRuntime`).

## Self-sufficiency invariant

Guarded by `tests/test_runtime/test_runtime_self_sufficiency.py` (subprocess
import probe — blocks transitive/lazy imports an AST scan cannot see) and
`tests/test_layering_domain.py` (AST top-level import scan). Blocked prefixes:

- `hecate.tools`, `hecate.enterprise`, `hecate.channel`, `hecate.studio`,
  `hecate.ops`
- All workspace wheels: `hecate_ops`, `hecate_sandbox`, `hecate_memory`,
  `hecate_enterprise`, `hecate_llm`, `hecate_channel_slack`,
  `hecate_channel_feishu`

Function-level lazy imports are the only sanctioned way to cross the domain
boundary (module-level imports are rejected by the AST scan; the runtime
self-sufficiency probe AST-scans ALL import sites against the inventory
below — an undocumented lazy import fails CI. Each row carries an exit
condition; new rows must include one):

- `runtime/tool_access.py` → `hecate.tools.tool.shell_analysis`
  (content-aware shell gating). *Exit*: a second shell-analysis
  implementation appears or an engine hook replaces content gating —
  promote to an injected interface.
- `runtime/workers/coordinator_worker.py` →
  `hecate.studio.workflows.templates` (dynamic orchestration executor).
  *Exit*: template construction becomes parameter-injected / DSL-driven.
- `runtime/agent_tool.py` → `hecate.channel.a2a.client` + `types`
  (A2A handoff transport). *Exit*: A2A client injected via a port
  (fold into the A2A server-auth initiative).
- `runtime/compaction.py`, `runtime/context_processors.py` →
  `hecate_memory.memory.consolidation` (consolidation backend).
  *Exit*: consolidation extracted behind a runtime-owned Protocol.
- `runtime/offloader.py` → `hecate_sandbox.environment` (offload
  execution env). *Exit*: sandbox environment exposed as a runtime port.
- `runtime/task_memory_hook.py` → `hecate_memory.memory.task_memory` +
  `EpisodeModel` (task-memory writes). *Exit*: writes injected via the
  memory provider Protocol (dropping the ORM import).
- `runtime/workers/tool_worker.py` → `hecate.tools.tool.builtin`
  (memory-tool name set for retrieval escalation). *Exit*: the name set
  moves to a shared constants module or is injected.
- `runtime/security/egress.py` → `hecate.ops.dlp.*` (scanner via DI +
  TYPE_CHECKING annotations; action enum at its runtime use site).
  *Exit*: full DI when the DLP action enum moves out of ops.
- `runtime/security/hooks/output_security.py` → `hecate.ops.dlp.*`,
  `hecate.ops.output_security.*`, `hecate.ops.security.findings_writer`
  (scan/redact/record dispatch). *Exit*: scan, redact, and finding-write
  injected via the security hook port.
- `runtime/security/guardrail_assembly.py` →
  `hecate.ops.security.findings_writer` (finding-write wiring into the
  guardrail bundle). *Exit*: finding writes injected via the security
  hook port (same initiative as the output_security row).

(`runtime/agent_execution_port.py` rows removed — the adapter moved to
`core/composition/agent_execution_port.py` in
runtime-boundary-pluggability; composition may know all modules.)

## Extension point inventory

| Extension point | File | Default impl |
|-----|------|--------------|
| RuntimePort | `ports.py` | `StubRuntimePort` (test double); production: `core/composition/runtime_port_adapter.py::create_runtime_port` |
| Worker / WorkerPool | `worker.py` | `AgentWorker` / `DirectWorkerPool` |
| CheckpointStore | `checkpoint.py` | `InMemoryCheckpointStore` |
| EventStore | `eventstore.py` | `InMemoryEventStore` |
| ContextEngine | `context.py` | `InMemoryContextEngine` |
| ContextProcessor chain (4.13) | `context_processors.py`; policy resolution in `context_policy.py` | default chain via `default_chain_processors()`; production assembly: `ContextChainFactory` in `WorkflowExecutionService` |
| CompactionSummarizer (ADR-033) | `compaction.py` | ABC only; production adapter: `PortCompactionSummarizer` in `WorkflowExecutionService` (routes via RuntimePort) |
| SchedulerStrategy | `scheduler.py` | `FIFOScheduler` |
| EvictionPolicy | `eviction.py` | `NoEviction`, `SizeBasedEviction` |
| OptimizationPass | `optimization.py` | `DeadNodeElimination`, `ParallelBranchDetection` |
| Guardrail hooks (Pre/Post × LLM/Tool) | `guardrail.py` | `NoOp*Hook` variants |
| MiddlewareChain | `middleware.py` | `middleware_factory.py` builders; legacy hooks via `middleware_adapters.py` |
| MonotonicDenialTracker (concrete dataclass) | `monotonic_denials.py` | per-session, wired via `runtime/security/guardrail_assembly.py` |
| RetryStrategy | `retry.py` | `NoRetryStrategy` |
| ConflictResolver (concrete class) | `temporal/conflict.py` | strategies via `ConflictStrategy` enum |
| Shell analysis (module functions, no class) | `tools/tool/shell_analysis.py` | feeds `runtime/tool_access.py` content-aware gating |

`RuntimePort` defines 9 abstract methods (`llm_invoke`, `tool_execute`,
`knowledge_query`, `checkpoint_save/load`, `conversation_load/save`,
`create_span`, `end_span`) plus 6 optional defaults: `context_assemble`,
`evidence_query`, `agent_execute`, `tool_execute_sandbox`, `workflow_execute`,
`llm_invoke_structured` (the production adapter overrides the last to stream
structured `tool_calls`).

Wired today: ContextEngine (PregelRuntime execution_context), ContextProcessor
chain (4.13 — `execution_context["context_chain"]`, resolved per node from
`node_config["context_processors"]`; engine-only contexts run the default
chain), guardrail hooks +
middleware chains on both the Pregel path and the `channel/api/v1/chat.py`
direct tool loop (assembled by `runtime/security/guardrail_assembly.py`), and
RetryStrategy via RetryExecutor.

Chain conventions (4.13): processors are runtime-internal extension points
(plain noun + ABC, no `Port`/`Base` marker); they operate on atomic
`ContextUnit`s (tool-call-linked messages never split); the
`CompressionProcessor` has two backends — `projection` (default) and
`surface_replacement` (durable compaction per ADR-033, implemented in
`compaction.py`: ledger view + bracket events; the messages channel and the
fold stay untouched). Note the assembly-time exclusivity: the
`surface_replacement` backend cannot be combined with a non-`NoEviction`
eviction policy. Ledger ranges are messages-channel ordinals.
Registry trust boundary: only types in `PROCESSOR_REGISTRY` pass config
validation (third-party code cannot enter the in-process T0 chain by naming
itself in configuration).

## Companion modules

- `replay/` — time-travel replay, logfold/loginvariants/logpolicy,
  orchestrator_validator
- `temporal/` — Temporal distributed execution (temporal extra)
- `security/` — guardrail assembly (wiring; hook interfaces stay in the
  kernel `guardrail.py`)

Deep dive: `docs/design/engine-design.md`.
