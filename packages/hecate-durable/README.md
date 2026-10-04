# hecate-durable

Durable execution core: the independently installable persistence package behind
step6's local-reliable-execution track (worktree `durable-execution-core`).

## What lives here

| Module | Responsibility |
|---|---|
| `hecate_durable.contracts` | The durable-execution contract cluster (`references` / `events` / `tools` / `durable`) — moved here from `src/hecate/contracts/execution/` so the standalone-host wheel closure never pulls the full `hecate` application. The original import paths forward here (same pattern as `src/hecate/runtime/` → `hecate_runtime`). |
| `hecate_durable.seams` | `DurableTaskStore` / `ControlCommandRecorder` / `ActionLedger` — the language-neutral seams from phase-0 (`durable-execution-contracts`). |
| `hecate_durable.stub` | The InMemory doubles; test/local-preview only, no restart durability. |
| `hecate_durable.storage` | The SQL reference storage: `SqlDurableStore` (implements all three seams), `SqlEventLog` (transactional outbox + cursor reads with gap markers), `LeaseManager` (leases + monotonic fencing tokens). |

The only runtime dependency is SQLAlchemy. PostgreSQL is the reference
production dialect (`pip install hecate-durable[postgres]`); SQLite (file) is
the development/CI dialect running the identical semantics — atomicity comes
from single-statement conditional updates and unique constraints, never from
dialect-specific locks.

## Semantics pinned by the contract suite

`SqlDurableStore` registers into `DURABLE_IMPLEMENTATIONS`
(`tests/test_execution/conftest.py`) and must pass the same parameterized
assertions as the InMemory stub: closed task lifecycle with terminal
absorption and explicit reconciliation, command receipts where transport
success is never `applied`, idempotency keys bound to server-verified
scope + request digest, and the four-state action ledger
(`never_started / claimed / outcome_unknown / store_unavailable`) with atomic
claims, digest-conflict rejection, and recovery that returns real results —
never placeholder text.

Beyond the seams, the storage layer adds what the plan's step6 requires:

- **Transactional outbox** — every state write appends its governance event
  row in the same database transaction; readers resume by cursor with
  explicit gap markers, dedup is per `event_id`, and out-of-order sequences
  are ordered at read time.
- **Leases + fencing** — resource-key leases with monotonic tokens; claim
  tokens fence action outcomes so a late receipt from an expired holder is
  rejected and journaled, never applied over authoritative state.
- **G2 closure across restarts** — intents and outcomes carry
  session/execution/tool-call correlation columns, so platform Actions map
  explicitly onto runtime `TOOL_CALL`/`TOOL_RESULT` events, and outcomes
  persist real result content/digest/reference for recovery.

## Usage (standalone host profile)

```python
from hecate_durable.storage import SqlDurableStore

store = SqlDurableStore("sqlite:///runner-state.db")  # or postgresql+psycopg://...
store.create_schema()  # host-owned schema; no platform alembic
```

The `hecate-runner` durable profile consumes this package; see
`packages/hecate-runner/README.md`. Platform-side projection and scheduling
(`platform-task-control-api`) consume the same seams.

## Tests

`packages/hecate-durable/tests/` runs on SQLite by default; set
`DURABLE_TEST_POSTGRES_URL` to run the same fault-injection suite against a
real PostgreSQL instance (crash, lease expiry, late receipts, storage
failure).
