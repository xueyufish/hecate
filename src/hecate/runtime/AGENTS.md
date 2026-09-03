# runtime/ — execution engine domain

The runtime domain is the execution engine (renamed from `engine/` in PR #117).
Zero external dependencies on other domains and workspace wheels — pinned by a
subprocess probe. `__init__.py` is deliberately empty: import directly from
submodules (`from hecate.runtime.pregel import PregelRuntime`).

## Self-sufficiency invariant

Guarded by `tests/test_runtime/test_runtime_self_sufficiency.py` (subprocess
import probe — blocks transitive/lazy imports an AST scan cannot see) and
`tests/test_layering_domain.py` (AST top-level import scan). Blocked prefixes:

- `hecate.services` (legacy — the dir is deleted; prefix stays until the
  guard is simplified), `hecate.tools`, `hecate.enterprise`, `hecate.channel`,
  `hecate.studio`, `hecate.ops`
- All workspace wheels: `hecate_ops`, `hecate_sandbox`, `hecate_memory`,
  `hecate_enterprise`, `hecate_llm`, `hecate_channel_slack`,
  `hecate_channel_feishu`

Exactly **two** function-level lazy imports cross the domain boundary (the
sanctioned exceptions — do not add more):

- `runtime/tool_access.py` → `hecate.tools.tool.shell_analysis`
  (content-aware shell gating)
- `runtime/workers/coordinator_worker.py` →
  `hecate.studio.workflows.templates` (dynamic orchestration executor)

## Extension point inventory

| Extension point | File | Default impl |
|-----|------|--------------|
| RuntimePort | `ports.py` | `StubRuntimePort` (test double); production: `core/composition/runtime_port_adapter.py::create_runtime_port` |
| Worker / WorkerPool | `worker.py` | `AgentWorker` / `DirectWorkerPool` |
| CheckpointStore | `checkpoint.py` | `InMemoryCheckpointStore` |
| EventStore | `eventstore.py` | `InMemoryEventStore` |
| ContextEngine | `context.py` | `InMemoryContextEngine` |
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

Wired today: ContextEngine (PregelRuntime execution_context), guardrail hooks +
middleware chains on both the Pregel path and the `channel/api/v1/chat.py`
direct tool loop (assembled by `runtime/security/guardrail_assembly.py`), and
RetryStrategy via RetryExecutor.

## Companion modules

- `replay/` — time-travel replay, logfold/loginvariants/logpolicy,
  orchestrator_validator
- `temporal/` — Temporal distributed execution (temporal extra)
- `self_improvement/` — FailureAnalyzer / ConstraintGenerator /
  ConstraintInjector (moved from `services/harness/`)
- `security/` — guardrail assembly (wiring; hook interfaces stay in the
  kernel `guardrail.py`)

Deep dive: `docs/design/engine-design.md`.
