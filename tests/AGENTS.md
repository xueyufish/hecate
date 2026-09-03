# tests/ — conventions

- Directory names follow legacy domains (e.g. `test_auth/` imports
  `hecate.enterprise.auth`, `test_a2a/` imports `hecate.channel.a2a`) — do not
  judge a test's target by its directory name; check the imports. Do not bulk
  rename directories.
- Shared fixtures in `conftest.py` (repo root): `db_session` (AsyncSession +
  auto-rollback), `setup_database` (autouse, create_all/drop_all per test),
  `client` (httpx AsyncClient + DI overrides). Use `db_session` in all DB
  tests — never create separate engines in test files.
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed. Database is
  in-memory SQLite (`sqlite+aiosqlite://`); never connect to real PostgreSQL
  in unit tests.
- Runtime-engine tests use lightweight stub classes (`SimpleWorker`,
  `InterruptWorker`), not mocking frameworks. No factories — create models
  inline with `db_session.add()` + `await db_session.flush()`.
- ruff S101 (assert in tests) is expected — per-file-ignores in
  `pyproject.toml` handle it.
- New runtime extension points: test that the interface is not instantiable,
  test the default impl, test edge cases.
- Per-directory conftests exist only in `test_services/test_browser` and
  `test_services/test_session_state` (fakeredis). Don't add more unless a
  fixture can't be shared.
