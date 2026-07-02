## Why

P2 has 4 remaining features; 2 of them — **1.1.8 Conversational vs Task Workflows** and **1.1.9 Workflow Version Management** — are pure backend work that share the same code path (WorkflowModel → WorkflowService → WorkflowAPI). Completing both pushes P2 to 55/57 (96%) and unblocks the public repository milestone.

Industry research (Dify, AgentArts, Google ADK, IBM watsonx) confirms that conversational/task mode distinction belongs at the **workflow level**, not the graph DSL level, and that version management requires publish semantics and version diff beyond basic rollback.

## What Changes

- Add `execution_mode` field to `WorkflowModel` (`conversational` | `task`) — industry-standard placement per Dify (WorkflowType), AgentArts (对话型/任务型)
- Add compile-time validation: task mode workflows **forbid** INTERRUPT and SUGGESTION nodes (AgentArts pattern)
- Add runtime behavior differentiation: task mode disables checkpointing and limits StreamMode to VALUES only; conversational mode enables full session state, conversation variables, and streaming
- Add `published_version` field to `WorkflowModel` and `labels` field to `WorkflowVersionModel` — following Hecate's existing Prompt versioning pattern
- Add `publish_version()` and `diff_versions()` methods to `WorkflowService`
- Add publish and diff API endpoints to the workflow management router
- Add system variables: `sys.conversation_id`, `sys.dialogue_count` (conversational mode only), `sys.execution_mode`

## Capabilities

### New Capabilities
- `workflow-execution-mode`: Conversational vs Task workflow execution mode — model field, compile-time validation, runtime behavior differences, system variables
- `workflow-version-publish`: Workflow publish semantics, deployment labels, and version diff comparison

### Modified Capabilities
- `graph-dsl`: Add execution_mode-aware validation — task mode forbids INTERRUPT and SUGGESTION node types at compile time

## Impact

- **Models**: `WorkflowModel` (add `execution_mode`, `published_version`), `WorkflowVersionModel` (add `labels`)
- **Services**: `WorkflowService` (add `publish_version`, `diff_versions`), `GraphCompiler` (add mode-aware validation)
- **API**: `api/management/workflows.py` (add `POST /publish/{version}`, `GET /diff` endpoints)
- **Engine**: `PregelRuntime` (mode-aware checkpoint/stream behavior), `types.py` (system variables)
- **Schemas**: `schemas/graph-dsl.schema.json` (add `execution_mode` optional field)
- **Tests**: New test files for mode validation, publish flow, diff comparison
- **Dependencies**: `deepdiff` for version diff (new optional dependency)
