## Context

Hecate's workflow system has three layers: `WorkflowModel` (persistence) → `WorkflowService` (business logic) → `WorkflowAPI` (HTTP endpoints). The engine layer (`GraphCompiler` → `PregelRuntime`) executes compiled graphs.

Currently:
- `WorkflowModel` has `current_version` but no execution mode or published version tracking
- `WorkflowVersionModel` stores immutable snapshots with `graph_dsl`, `compiled_graph`, `change_summary` — but no deployment labels
- `WorkflowService` provides CRUD + rollback but no publish or diff
- `AgentModel.mode` distinguishes `chat | three_layer | workflow` but says nothing about the workflow's execution semantics
- The engine has no concept of execution mode — all workflows behave identically

Industry research across Dify, AgentArts, Google ADK, and IBM watsonx confirms:
1. **Mode belongs at the workflow level**, not graph DSL level (Dify: `WorkflowType`, AgentArts: 对话型/任务型)
2. **Task mode forbids interaction nodes** at validation time (AgentArts pattern)
3. **Publish semantics** use draft/published dual model (Dify) or version labels (LangFuse prompts)

## Goals / Non-Goals

**Goals:**
- Add `execution_mode` to `WorkflowModel` with compile-time validation and runtime behavior differentiation
- Add `published_version` to `WorkflowModel` and `labels` to `WorkflowVersionModel`
- Implement `publish_version()` and `diff_versions()` in `WorkflowService`
- Add publish and diff API endpoints
- Complete features 1.1.8 and 1.1.9 (P2 → 55/57)

**Non-Goals:**
- Frontend UI changes for workflow mode selection or version comparison (P3)
- Visual diff / side-by-side comparison (requires Canvas UI)
- Workflow version branching or merge (Git-like semantics)
- Migration of existing workflows — all existing workflows default to `conversational` mode
- Changing `AgentModel.mode` — it already has `workflow` as a valid value

## Decisions

### Decision 1: Mode at WorkflowModel, not Graph DSL

**Choice**: Add `execution_mode` to `WorkflowModel`, not to `GraphConfig` / `graph-dsl.schema.json`.

**Rationale**: The graph DSL defines *structure* (nodes, edges, channels). Execution mode defines *behavior* (session management, streaming, node restrictions). These are separate concerns. Dify puts `WorkflowType` on the Workflow table; AgentArts makes it a workflow creation choice. Placing it on `WorkflowModel` allows the same graph definition to potentially serve both modes in the future, and keeps the engine layer mode-agnostic except for validation.

**Alternatives considered**:
- Graph DSL level: Rejected — couples structure to behavior, prevents reuse
- AgentModel level: Rejected — agent mode is already `chat|three_layer|workflow`; nesting execution_mode there is confusing

### Decision 2: Task mode validation in GraphCompiler, not runtime

**Choice**: Validate node restrictions at compile time in `GraphCompiler.compile()`.

**Rationale**: AgentArts forbids interaction nodes in task workflows at design time. Catches errors early, before execution. The compiler receives the `execution_mode` as a parameter and raises `GraphValidationError` if INTERRUPT or SUGGESTION nodes are present in task mode.

### Decision 3: Runtime behavior via parameter, not subclassing

**Choice**: Pass `execution_mode` as a parameter to `PregelRuntime.execute()`, not via separate runtime subclasses.

**Rationale**: The behavioral differences (checkpointing on/off, streaming mode selection) are simple boolean/enum switches. Subclassing would over-engineer this.

### Decision 4: Publish via `published_version` pointer, not dual records

**Choice**: Add `published_version: int | None` to `WorkflowModel`. Publish sets this pointer to an existing version number.

**Rationale**: Dify uses draft/published dual records, but Hecate already has a version list model. A pointer is simpler — no new table, no duplication. Rollback remains the existing "create new version with old content" pattern. The published version is just a labeled pointer.

**Alternatives considered**:
- Dify dual-record model: Rejected — would require schema redesign
- Labels-only (LangFuse prompt pattern): Rejected — requires label querying to find published version; pointer is simpler

### Decision 5: Labels follow existing Prompt pattern

**Choice**: Add `labels: list[str]` to `WorkflowVersionModel`, following the existing `PromptVersionModel.labels` pattern.

**Rationale**: Hecate already has this pattern for prompts. Consistency is valuable.

### Decision 6: JSON-aware diff for version comparison

**Choice**: Implement `diff_versions()` using `deepdiff` library for structural JSON comparison of `graph_dsl` fields. Return categorized changes: nodes added/removed/modified, edges added/removed/modified, state changes.

**Rationale**: Graph-aware diff (understanding node semantics) is ideal but over-engineered for P2. `deepdiff` provides structural diff out of the box. The API returns a structured result that a future frontend can render.

## Risks / Trade-offs

- **Risk**: Existing workflows default to `conversational` — users may not realize task mode exists → **Mitigation**: API documentation and future UI toggle
- **Risk**: `deepdiff` adds a new dependency → **Mitigation**: Add to `[dev]` optional dependency group first; only promote to core if needed at runtime
- **Risk**: Task mode forbidding INTERRUPT nodes may be too restrictive for some use cases → **Mitigation**: Users can use conversational mode for semi-automated workflows; task mode is explicitly for headless automation
- **Risk**: `published_version` pointer can point to a deleted version → **Mitigation**: Soft-deleted versions remain queryable; validate at publish time that target version exists
