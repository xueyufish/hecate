## ADDED Requirements

### Requirement: AgentStateStore ABC is marked deprecated via PEP 562 __deprecated__ module attribute

The engine SHALL mark `hecate.services.state.store` as a deprecated module by setting `__deprecated__ = ("Use hecate.engine.session_state.SessionStateStore instead.",)` at module level. The deprecation message SHALL direct users to `hecate.engine.session_state.SessionStateStore` as the replacement abstraction.

`__deprecated__` SHALL be set in the `store` submodule only (not in `state` submodule, which defines the still-used `AgentState` Pydantic model).

The `AgentStateStore` ABC and `InMemoryStateStore` class docstrings SHALL begin with a `.. deprecated::` Sphinx directive referencing this change and the migration guide at `docs/migrations/agent-state-store.md`.

#### Scenario: import hecate.services.state.store triggers no warning at import time
- **WHEN** a module imports `from hecate.services.state.store import AgentStateStore`
- **THEN** no `DeprecationWarning` is emitted at import time (only on attribute access, per PEP 562 semantics)

#### Scenario: accessing AgentStateStore via the deprecated module triggers DeprecationWarning
- **WHEN** Python evaluates `from hecate.services.state.store import AgentStateStore` and the module is marked with `__deprecated__`
- **THEN** Python SHALL emit `DeprecationWarning` at attribute access time with the configured message
- **THEN** the warning's `stacklevel` SHALL be `1` (PEP 562 default; import site is reported)

#### Scenario: AgentStateStore docstring contains deprecated directive
- **WHEN** a developer reads `AgentStateStore.__doc__` via `help(AgentStateStore)` or introspection
- **THEN** the docstring SHALL contain a `.. deprecated::` directive
- **THEN** the directive's text SHALL reference `hecate.engine.session_state.SessionStateStore` and the migration guide URL

### Requirement: AgentStateStore construction emits DeprecationWarning with stacklevel=2

The `AgentStateStore` ABC and `InMemoryStateStore` class SHALL emit a `DeprecationWarning` when their `__init__` methods are invoked. The warning SHALL be issued from inside `__init__` with `stacklevel=2` so the call site (not the ABC definition) is reported.

The warning message SHALL follow the format `"AgentStateStore is deprecated. Use hecate.engine.session_state.SessionStateStore instead. See docs/migrations/agent-state-store.md for migration."`. The `AgentStateStore` ABC SHALL NOT raise on construction — `__init__` is empty (no-op), so the deprecation is emitted via a class-level `__init_subclass__` hook OR by adding `__init__` to the concrete `InMemoryStateStore` only (the ABC is abstract and cannot be instantiated directly via Python semantics).

The concrete `InMemoryStateStore.__init__` SHALL emit the warning. The ABC `AgentStateStore` SHALL NOT have a callable `__init__`; users who attempt to instantiate the ABC directly SHALL receive Python's standard `TypeError: Can't instantiate abstract class` (no `DeprecationWarning` because the failure precedes construction).

#### Scenario: InMemoryStateStore() construction emits DeprecationWarning
- **WHEN** a caller invokes `InMemoryStateStore()` to construct an in-memory store
- **THEN** Python SHALL emit a `DeprecationWarning`
- **THEN** the warning message SHALL mention `SessionStateStore` as the replacement
- **THEN** the warning's reported source location SHALL be the caller (caller's line, not the class definition), achieved via `warnings.warn(..., stacklevel=2)`

#### Scenario: existing tests that import AgentStateStore do not change behavior under default warning filter
- **WHEN** `tests/test_services/test_state/test_state.py` runs under pytest's default warning filter
- **THEN** `DeprecationWarning` is suppressed by Python's default `default` filter (warnings shown once per source location; pytest's default may be `error` for some configs but `default` is the project baseline per `pyproject.toml`)
- **THEN** all 23 existing `WorkflowExecutionService` tests SHALL pass without modification
- **THEN** the project test command `pytest tests/ -q` SHALL remain green

#### Scenario: explicit warning visibility in CI for deprecation tracking
- **WHEN** an operator runs `python -W default::DeprecationWarning -m pytest tests/`
- **THEN** the warnings from `InMemoryStateStore()` construction SHALL appear in test output
- **THEN** the deprecation count SHALL be deterministic (one per construction site per test run)

### Requirement: WorkflowExecutionService.state_store parameter emits DeprecationWarning at construction

`services/workflow/execution_service.py` `WorkflowExecutionService.__init__` SHALL emit a `DeprecationWarning` when the `state_store` parameter is provided (not `None`). The warning SHALL be emitted with `stacklevel=2` so the caller's source line is reported.

The parameter SHALL remain in the signature (no removal) to preserve backward compatibility with existing 23 tests in `tests/test_services/test_workflow/test_execution_service.py` that use `state_store=mock_state_store`. The docstring SHALL begin with `.. deprecated::` referencing the migration to `checkpoint_store` (the already-recommended path per the existing `distributed-session-state-store` spec line 437).

The `state_store` parameter is `Optional[AgentStateStore]`. When `None` (the default), no warning is emitted (the deprecated path is not entered). When provided, the warning fires and the parameter is stored in `self._state_store` exactly as before — no behavioral change.

The chat.py production path (`/v1/chat/completions`) already does NOT pass `state_store` per the existing spec (line 437), so production traffic will never see this warning. The warning is purely diagnostic for direct callers and tests.

#### Scenario: WorkflowExecutionService(state_store=mock) emits DeprecationWarning
- **WHEN** a caller constructs `WorkflowExecutionService(port=port, state_store=mock_state_store)` with the deprecated parameter
- **THEN** Python SHALL emit a `DeprecationWarning` with message mentioning `checkpoint_store` (the replacement) and the migration guide
- **THEN** the `state_store` SHALL be stored in `self._state_store` (no behavioral change)
- **THEN** `self._state_store is not None` (the legacy load path remains functional for backward compat)

#### Scenario: WorkflowExecutionService() default construction emits no warning
- **WHEN** a caller constructs `WorkflowExecutionService(port=port)` without `state_store`
- **THEN** no `DeprecationWarning` is emitted (the deprecated path is not entered)
- **THEN** `self._state_store is None` (the legacy path is skipped; only the new `checkpoint_store` path is active when one is wired)

#### Scenario: chat.py production path emits no deprecation warning
- **WHEN** `src/hecate/api/v1/chat.py` constructs `WorkflowExecutionService(port=port, db=db, event_store=..., checkpoint_store=...)` for an incoming chat request
- **THEN** no `state_store` parameter is passed
- **THEN** no `DeprecationWarning` is emitted
- **THEN** the chat response is identical to pre-deprecation behavior

#### Scenario: existing 23 execution_service tests continue to pass
- **WHEN** `pytest tests/test_services/test_workflow/test_execution_service.py -q` runs
- **THEN** all 23 existing tests pass
- **THEN** tests that pass `state_store=mock_state_store` continue to work, with `DeprecationWarning` suppressed by default pytest filter
- **THEN** no test assertion or mock is broken by the deprecation warning

### Requirement: User migration documentation is provided at docs/migrations/agent-state-store.md

`docs/migrations/agent-state-store.md` SHALL exist as a Markdown guide for users migrating from `AgentStateStore` to `SessionStateStore`. The guide SHALL contain:

1. **Why deprecated** (1 paragraph): explain that `SessionStateStore` is the production-ready replacement with multi-tenant keying, multi-backend support, and lock semantics.
2. **Migration mapping** (1 table): for each `AgentStateStore` method (`save`, `load`, `delete`, `list_sessions`), map to the `SessionStateStore` equivalent (`save`, `load`, `list_recent`). Note that `delete` is intentionally not in `SessionStateStore` (TTL handles expiration; if a hard delete is needed, document the workaround using `redis.delete(key)` for the Redis backend or `DELETE FROM session_states WHERE ...` for the PG backend).
3. **Key migration** (1 example): show that the key changes from `(agent_id, session_id)` to `(org_id, user_id, session_id)`. Document how to obtain the three-tuple from the current request context.
4. **Code example** (1 Python snippet): before/after — show `await state_store.save(agent_id, session_id, AgentState(...))` and the equivalent `await session_state_store.save(org_id, user_id, session_id, SessionState(agent_state=agent_state.model_dump(mode='json')))`.
5. **Configuration** (1 snippet): show that `SessionStateStore` requires `SESSION_STATE_STORE_BACKEND` env var to select backend (`memory` / `redis` / `postgres` / `tiered`).
6. **Support window** (1 paragraph): the deprecation is announced in the next release; hard removal (`13.4a-7`) is planned for at least 1 release cycle later. The deprecation period SHALL be at least one minor version (e.g., 0.x → 0.x+1).

#### Scenario: docs/migrations/agent-state-store.md exists and contains all required sections
- **WHEN** a user navigates to `docs/migrations/agent-state-store.md` in the repository
- **THEN** the file SHALL exist and contain the 6 sections listed above
- **THEN** the "Why deprecated" section SHALL reference `SessionStateStore` and the `distributed-session-state-store` OpenSpec capability
- **THEN** the migration mapping table SHALL map all 4 `AgentStateStore` methods to their `SessionStateStore` equivalents (or note absence for `delete`)
- **THEN** the code example SHALL show before/after Python snippets that an import-and-replace reader can follow
- **THEN** the support window SHALL mention `13.4a-7` as the planned hard-removal change

#### Scenario: docs file is linked from deprecation warnings
- **WHEN** a `DeprecationWarning` from `AgentStateStore` or `WorkflowExecutionService(state_store=...)` is rendered
- **THEN** the warning message SHALL include the phrase `See docs/migrations/agent-state-store.md for migration`
- **THEN** the user can `Ctrl+Click` or follow the path in the warning text to find the guide

### Requirement: feature-catalog documents the deprecation status transition

`docs/features/feature-catalog.md` line 403 (the `13.4a | Distributed Session State Store (Redis)` row) SHALL be updated to:

1. Mark the row's deprecation state in the description (e.g., add a sentence: "AgentStateStore ABC deprecated in `13.4a-6`; hard removal planned in `13.4a-7` (next minor version)").
2. Remove or mark the existing `**NOT DONE**: Change 6 AgentStateStore removal pending` line as resolved: `**RESOLVED (deprecation)**: Change 6 deprecation implemented in `13.4a-6` (Aug 2026); hard removal in `13.4a-7` (≥ next minor).`
3. Reference the migration guide: `See docs/migrations/agent-state-store.md`.

`docs/features/roadmap.md` line 458 (the `13.4a` row) SHALL add a `Deprecated:` annotation linking to the migration guide. The status marker `✅ (5/5)` SHALL remain (the implementation is complete; deprecation is operational hygiene).

The `13.4a-7` follow-up change SHALL be added to the roadmap under a new "Pending cleanups" section: "AgentStateStore hard removal (`13.4a-7`) — scheduled ≥ 1 minor version after `13.4a-6`."

#### Scenario: feature-catalog line 403 references the deprecation
- **WHEN** a reader opens `docs/features/feature-catalog.md` and locates the `13.4a` row
- **THEN** the row description SHALL mention `13.4a-6` (deprecation) and `13.4a-7` (hard removal)
- **THEN** the existing `**NOT DONE**: Change 6 ... pending` line SHALL be removed or replaced with a `**RESOLVED (deprecation)**` line
- **THEN** the row SHALL link to `docs/migrations/agent-state-store.md`

#### Scenario: roadmap adds a 13.4a-7 follow-up entry
- **WHEN** a reader opens `docs/features/roadmap.md` and looks for `13.4a`
- **THEN** the `13.4a` row SHALL mention deprecation in the description
- **THEN** a new entry `13.4a-7 | AgentStateStore hard removal` SHALL appear in a `Pending cleanups` or equivalent section, with note that it is scheduled ≥ 1 minor version after `13.4a-6`

### Requirement: 13.4a-7 hard removal is scheduled but not implemented by this change

This change (`13.4a-6`) SHALL implement deprecation only — it SHALL NOT delete `AgentStateStore`, `InMemoryStateStore`, the `state_store` parameter on `WorkflowExecutionService`, or any related files. A follow-up change `13.4a-7` SHALL be the separate OpenSpec change that performs the hard removal.

`13.4a-7` SHALL be scheduled to ship at least one minor version after `13.4a-6` (e.g., if `13.4a-6` ships in v0.20.x, `13.4a-7` targets ≥ v0.21.0). The exact release target SHALL be decided when `13.4a-7` is proposed; this change does not pre-commit a release date.

`13.4a-7` is OUT OF SCOPE for this change. It SHALL be tracked as a follow-up item in the roadmap (per the previous requirement) and SHALL have its own OpenSpec proposal when proposed.

#### Scenario: this change does not delete AgentStateStore files
- **WHEN** the implementation of `13.4a-6` is complete
- **THEN** `src/hecate/services/state/store.py` SHALL still exist
- **THEN** `src/hecate/services/state/state.py` SHALL still exist
- **THEN** `src/hecate/services/state/__init__.py` SHALL still export `AgentStateStore` and `InMemoryStateStore`
- **THEN** the `state_store` parameter on `WorkflowExecutionService.__init__` SHALL still exist (only the `DeprecationWarning` is added)
- **THEN** `git grep "AgentStateStore" src/` SHALL return non-empty results

#### Scenario: 13.4a-7 follow-up is documented but not implemented
- **WHEN** a reader looks at the OpenSpec changes directory
- **THEN** `openspec/changes/deprecate-agent-state-store/` (this change) exists with full artifacts
- **THEN** `openspec/changes/remove-agent-state-store/` (the `13.4a-7` follow-up) does NOT exist (it is for the future, not this change)
- **THEN** `docs/features/roadmap.md` mentions `13.4a-7` in a "Pending cleanups" or equivalent section
