## Why

Hecate currently has no mechanism for persisting per-session working state across process restarts. The `execution_context` dict in `WorkflowExecutionService` is ephemeral — created fresh each call and lost when the process exits. This means conversation buffers, compressed summaries, permission caches, and tool/task sub-contexts cannot survive a crash or scale-down. Competitor platforms (AgentScope, Claude Code, Bedrock AgentCore) all solve this with an explicit "AgentState" concept that separates volatile per-session state from durable per-agent environment.

This change introduces the AgentState abstraction and AgentStateStore persistence layer, enabling cross-process session resume and laying the foundation for 4.25 Layered Memory System.

## What Changes

- **New `AgentState` dataclass** — structured representation of per-session working state: session_id, agent_id, summary, context, permission_context, tool_context, task_context, environment_root, metadata.
- **New `AgentStateStore` ABC** — persistence interface with `save()`, `load()`, `delete()`, `list_sessions()` methods.
- **New `InMemoryStateStore`** — default in-process implementation for single-machine use and testing.
- **`WorkflowExecutionService` integration** — load AgentState from store at call entry, inject into `execution_context`, save at call exit.
- **`EnvironmentManager` integration** — environment_root path populated into AgentState automatically.

## Capabilities

### New Capabilities
- `agent-state-separation`: Per-session AgentState data model, AgentStateStore ABC with InMemoryStateStore, and WorkflowExecutionService integration for state load/save lifecycle.

### Modified Capabilities
- `agent-environment`: Minor — sessions/ subdirectory now used for AgentState snapshots (no spec-level requirement change, implementation detail only).

## Impact

- **New files**: `src/hecate/services/state/` (state.py, store.py, __init__.py)
- **Modified files**: `src/hecate/services/workflow/execution_service.py` (state load/save lifecycle)
- **Tests**: `tests/test_services/test_state/` (state model + store + integration)
- **No breaking changes**: AgentState is additive; existing execution_context behavior unchanged.
- **No new dependencies**: Uses Pydantic (already available) and asyncio.Lock (stdlib).
