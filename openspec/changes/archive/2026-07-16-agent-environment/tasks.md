## 1. Configuration

- [x] 1.1 Add settings to `src/hecate/core/config.py`: `AGENT_ENV_ENABLED: bool = True`, `AGENT_ENV_TTL: int = 86400` (24 hours in seconds)

## 2. AgentEnvironment ABC + LocalEnvironment

- [x] 2.1 Create `src/hecate/services/environment/__init__.py`
- [x] 2.2 Create `src/hecate/services/environment/environment.py` — `FileInfo` dataclass (name, path, size, modified_at, is_dir), `AgentEnvironment` ABC (environment_id, root_path, read_file, write_file, list_files, delete_file, exists, ensure_dirs), `LocalEnvironment` implementation using `WORKSPACE_ROOT/{agent_id}/` with subdirectories: sessions/, files/, memory/, skills/

## 3. EnvironmentManager

- [x] 3.1 Create `src/hecate/services/environment/manager.py` — `EnvironmentManager` class with: `get_or_create(agent_id) -> AgentEnvironment` (lazy creation + caching), `close(agent_id)`, `close_all()`, TTL eviction (idle environments auto-cleanup), asyncio.Lock for thread safety

## 4. REST API

- [x] 4.1 Create `src/hecate/api/management/environment.py` — router prefix `/api/agents/{agent_id}/environment`: `GET /files` (list), `GET /files/{path}` (read), `POST /files` (upload), `DELETE /files/{path}` (delete), `GET /stats` (file count, total size)
- [x] 4.2 Register `environment_router` in `src/hecate/main.py`

## 5. WorkflowExecutionService Integration

- [x] 5.1 Update `src/hecate/services/workflow/execution_service.py` — add `environment_manager` parameter, call `get_or_create(agent_id)` before execution, pass environment root path in `execution_context`

## 6. Tests

- [x] 6.1 Test `LocalEnvironment` — write/read/list/delete files, ensure_dirs creates subdirectories, exists returns correct boolean
- [x] 6.2 Test `EnvironmentManager` — lazy creation, cached reuse, TTL eviction, close_all, concurrent access safety
- [x] 6.3 Test REST API — list/read/write/delete files via httpx AsyncClient, stats endpoint

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 7.2 Run `mypy src/` — 0 errors
- [x] 7.3 Run `python -m pytest tests/test_services/test_environment/ -q` — all pass
