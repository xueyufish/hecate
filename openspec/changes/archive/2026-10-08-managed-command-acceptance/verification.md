# managed-command-acceptance — Verification

> 本记录描述 change 的验证口径与已通过的命令;恢复重建后需重跑一次全部门禁并在"重跑记录"小节登记结果。

## Evidence

- `tests/scenarios/test_sc05_managed_wake_chain.py` — 5 process-level
  scenarios over a clean-installed Runner wheel and a real platform HTTP
  server (uvicorn on TCP, not ASGI): full wake chain, host restart while
  waiting, control-plane restart + idempotent command replay, lost effect
  upload + retry, expired command. All five pass on SQLite.
- `tests/scenarios/test_sc05_managed_reconnect.py` — 9 component-level
  scenarios (no assertion changes from before extraction). All pass on
  SQLite after the stack moved to `tests/scenarios/tools/managed_platform.py`.
- Platform stack extraction: `tests/scenarios/tools/managed_platform.py`
  and shared fixtures in `tests/scenarios/conftest.py` (`step6_runner_database_url`,
  `managed_secrets`). SC05's original test bodies are unchanged; the
  `managed_stack` fixture and `serve_over_tcp` are now reusable from
  `step6-pg-process-matrix`.
- Runner fixes captured in this change:
  - `packages/hecate-runner/src/hecate_runner/durable.py`:
    `is_decided_action(...)` returns True when the ledger already holds
    a decision for the action key, so the engine can skip the lease
    gate for the replay (a used lease would otherwise starve the
    attempt's real new dispatch of authorization).
  - `packages/hecate-runner/src/hecate_runner/engine.py`:
    `_lease_refusal` waits up to `_LEASE_REFRESH_WAIT_SECONDS` (5 s) on
    `CredentialError` ("nonce already consumed") to give the channel
    loop time to install the next lease; explicit denial with evidence
    after the budget. Constants live at module top.

## Commands

```bash
# Wake-chain scenarios (process-level)
uv run pytest tests/scenarios/test_sc05_managed_wake_chain.py -q -p no:cacheprovider

# Combined SC05 suites + manifest consistency
uv run pytest tests/scenarios/test_sc05_managed_reconnect.py \
                 tests/scenarios/test_sc05_managed_wake_chain.py \
                 tests/scenarios/test_manifest_consistency.py -q -p no:cacheprovider

# Gates
ruff check src/ tests/ packages/
ruff format --check src/ tests/ packages/
mypy src/ packages/
```

## Honest scope

- SC05 is `planned` after this change: the process-level wake chain is
  proven on SQLite; the `HECATE_STEP6_POSTGRES_URL`-gated parametrization
  is wired but not run in this environment (PG matrix + restart/lease
  takeover belong to `step6-pg-process-matrix`).
- The two runner fixes sit inside step6b (action-time lease enforcement)
  and step6c (lease refresh across multi-action runs); the larger step6b
  closure — tool-level action scope, legal approval, revocation, and
  the declared disconnect window — remains a step7 deliverable.
- Step6 overall still "partial complete" per the evolution plan; this
  change moves step6c from "no process evidence" to "process evidence
  on SQLite" and adds the two runner fixes that surfaced in the gap.

## 重跑记录(恢复重建后)

- 2026-10-08(worktree `feat/managed-command-acceptance`,基线 `dc852a5`):
  - `pytest tests/scenarios/test_sc05_managed_reconnect.py tests/scenarios/test_sc05_managed_wake_chain.py tests/scenarios/test_manifest_consistency.py -q -p no:cacheprovider` → **21 passed, 2 warnings in 152.55s**(SQLite 参数化)。
  - `ruff check src/ tests/ packages/` → All checks passed;`ruff format --check src/ tests/ packages/` → 1501 files already formatted。
  - `mypy src/ packages/` → Success: no issues found in 892 source files。
  - runner 两文件经 wheel 字节级还原(`is_decided_action`/`_LEASE_REFRESH_*` 与首次交付一致);测试与文档按会话记录重写后由本轮跑绿确认。
