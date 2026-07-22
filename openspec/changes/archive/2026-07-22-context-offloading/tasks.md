## 1. Config Settings

- [x] 1.1 Add `CONTEXT_OFFLOAD_ENABLED: bool = True` to `Settings` class in `src/hecate/core/config.py`
- [x] 1.2 Add `CONTEXT_OFFLOAD_THRESHOLD_TOKENS: int = 6000` to `Settings` class in `src/hecate/core/config.py`
- [x] 1.3 Add `.env.example` entries for both new settings with comments

## 2. ContextOffloader Implementation

- [x] 2.1 Create `src/hecate/services/context/offloader.py` with `ContextOffloader` class skeleton (imports, `__init__` accepting optional `AgentEnvironment`)
- [x] 2.2 Implement `offload(messages, session_id) -> dict` method: serialize messages to JSON, write to `memory/sessions/{session_id}/offloaded_{timestamp}.json`, return reference stub
- [x] 2.3 Implement `_build_stub(path, messages) -> dict` method: generate compact system-role reference message with topic summary and `read_file` hint, capped at 500 chars
- [x] 2.4 Implement `_filename_timestamp() -> str`: generate `YYYYMMDDHHMMSS` format; handle same-second collisions with `_1`, `_2` suffix by checking `environment.exists()`
- [x] 2.5 Implement `_heuristic_summary(messages) -> str`: extract first 200 chars of each user message, join and truncate to 500 chars total
- [x] 2.6 Add `is_enabled() -> bool` method: returns `False` when environment is None (signals pipeline to skip)
- [x] 2.7 Update `src/hecate/services/context/__init__.py` to export `ContextOffloader`

## 3. LLMWorker Pipeline Modification

- [x] 3.1 In `LLMWorker._apply_context_pipeline()` (engine/workers/llm_worker.py), read `execution_context.get("context_offloader")` and `settings.CONTEXT_OFFLOAD_ENABLED`
- [x] 3.2 Capture dropped messages: compute `dropped = messages[len(selected):]` after `select_messages` returns (messages before the selected window)
- [x] 3.3 Compute dropped token count via `ctx_engine.estimate_tokens(dropped)`; check against `CONTEXT_OFFLOAD_THRESHOLD_TOKENS`
- [x] 3.4 If threshold met and offloader enabled: call `offloader.offload(dropped, session_id)`, receive stub
- [x] 3.5 Rebuild filtered list as `[stub] + selected`, re-estimate tokens
- [x] 3.6 If still over budget: proceed to `ctx_engine.compress([stub] + selected)` as last resort
- [x] 3.7 If offloader absent OR threshold not met: proceed to `compress(selected)` as before (backward compatible)
- [x] 3.8 Apply the same changes to `execute_stream()` pipeline path

## 4. Execution Context Wiring

- [x] 4.1 In PregelRuntime (engine/pregel.py), add `context_offloader` parameter to `__init__`
- [x] 4.2 In `_execution_context()`, inject `ctx["context_offloader"] = self._context_offloader` when not None
- [x] 4.3 Update WorkflowExecutionService (or wherever PregelRuntime is constructed) to build a `ContextOffloader(environment=env)` when an `AgentEnvironment` is available and inject it into PregelRuntime

## 5. Tests

- [x] 5.1 Create `tests/test_services/test_context/test_offloader.py` with `ContextOffloader` unit tests
- [x] 5.2 Test: offload writes valid JSON file to environment with full message structure preserved
- [x] 5.3 Test: offload returns compact stub with role=system, content ≤ 500 chars, includes file path
- [x] 5.4 Test: stub includes `read_file("path")` retrieval instruction
- [x] 5.5 Test: stub topic summary extracts user message prefixes, truncates to 500 chars
- [x] 5.6 Test: filename timestamp format is `YYYYMMDDHHMMSS`, same-second collisions get `_1` suffix
- [x] 5.7 Test: `is_enabled()` returns False when environment is None
- [x] 5.8 Create/extend `tests/test_engine/test_workers/test_llm_worker_pipeline.py` with pipeline integration tests
- [x] 5.9 Test: pipeline offloads when offloader present and threshold met (file written, stub in filtered list)
- [x] 5.10 Test: pipeline skips offload when offloader absent (backward compatible — matches old 4-step output)
- [x] 5.11 Test: pipeline skips offload when dropped tokens < threshold (proceeds to compress)
- [x] 5.12 Test: pipeline falls back to compress when offload insufficient (stub + selected still over budget)
- [x] 5.13 Test: pipeline offload disabled via config flag (CONTEXT_OFFLOAD_ENABLED=false → skip offload)

## 6. Documentation

- [x] 6.1 Add docstrings to `ContextOffloader` class and all public methods (English, per AGENTS.md)
- [x] 6.2 Update `src/hecate/services/context/__init__.py` module docstring to mention offloading
- [x] 6.3 Add inline comment on `_apply_context_pipeline()` explaining the 5-step flow and why offload precedes compress

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/` — expect 0 errors
- [x] 7.2 Run `ruff format --check src/ tests/` — expect all formatted
- [x] 7.3 Run `mypy src/` — expect 0 errors
- [x] 7.4 Run `python -m pytest tests/test_services/test_context/test_offloader.py -v` — all pass
- [x] 7.5 Run `python -m pytest tests/test_engine/test_workers/test_llm_worker_pipeline.py -v` — all pass (or existing test file if naming differs)
- [x] 7.6 Run `python -m pytest tests/ -q` — no regressions (engine + context + worker tests sufficient; full suite if time permits)
