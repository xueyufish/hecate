# Expand-Contract Migration Guide

## Why expand-contract

Traditional migrations lock the table for the duration of a schema change. On a large hot table, this stalls every write for minutes — visible to users as timeouts and errors. Expand-contract splits each migration into **additive** (expand) and **destructive** (contract) phases, separated by at least one deploy, so old code and new schema coexist safely.

## The four phases

```
Phase 1: Expand          Phase 2: Backfill       Phase 3: Switch reads   Phase 4: Contract
─────────────────────    ─────────────────────   ──────────────────     ──────────────────
ALTER TABLE ADD COLUMN   UPDATE batch           Deploy code that       ALTER TABLE DROP COLUMN
(nullable, no default)  SKIP LOCKED             reads from new         ALTER COLUMN SET NOT NULL
                                                column only
─────────────────────    ─────────────────────   ──────────────────     ──────────────────
Old code: ignores new    Old code: still reads   Old code: must not     New code: assumes old
New code: tolerates null old column              read new column         column is gone
Safe: yes                Safe: yes               Safe: yes              Safe: only after all
                                                                    pods upgraded
```

## How Alembic autogenerate splits revisions

`alembic/env.py` registers a `process_revision_directives` hook. When you run `alembic revision --autogenerate -m "add user_avatar_url"`, the hook inspects the detected operations:

| Operation class                           | Phase    |
|-------------------------------------------|----------|
| `CreateTableOp`                           | expand   |
| `AddColumnOp`                             | expand   |
| `CreateIndexOp`                           | expand   |
| `CreateConstraintOp`                      | expand   |
| `DropTableOp`                             | contract |
| `DropColumnOp`                            | contract |
| `DropIndexOp`                             | contract |
| `DropConstraintOp`                        | contract |
| `AlterColumnOp`                           | contract |
| Anything else                             | `NotImplementedError` |

A mixed revision (expand + contract detected in the same `--autogenerate` run) is split into two linked files:
- `xxxx_expand.py` — additive changes, `down_revision` = previous head
- `xxxx_contract.py` — destructive changes, `down_revision` = expand

The CLI flag `--autogenerate` is required to trigger the split. Hand-written migrations are not split (you control the phasing manually).

## When to skip autogenerate

Write a single manual migration when:
- The change is purely additive (add column + index only) → one expand revision is enough
- The change is purely destructive and you have already done the expand in a prior release
- The operation is non-classifiable (e.g. `CreateSequenceOp`) → write manually, the hook will raise `NotImplementedError` if you try to autogenerate it

## `lock_timeout` safety net

`env.py` also calls `SET lock_timeout = '2s'` on every migration connection. Default value is configurable via the `ALEMBIC_LOCK_TIMEOUT` env var. This prevents a DDL blocked on a long transaction from stalling every subsequent query indefinitely — the migration instead fails fast with a clear error.

## Deployment sequence

```
Day 0: Deploy expand revision
       ├─ hecate-migrate --expand-only (or just `hecate-migrate`)
       ├─ Run Alembic expand revision
       ├─ Old code keeps working (new column nullable, ignored)
       └─ New code starts writing the new column

Day 1: Run backfill
       └─ Batch update existing rows into the new column (SKIP LOCKED, sleep between batches)

Day 2: Deploy code that reads from new column
       └─ Old code paths still work (reads fallback to old column)

Day N (after stable): Deploy contract revision
       ├─ hecate-migrate --contract-only
       └─ Run Alembic contract revision (drop old column, SET NOT NULL)
```

## Acceptance criteria for a safe migration

A migration is safe to ship when:
- [ ] expand revision adds only nullable columns, new tables, new indexes
- [ ] backfill is batched (chunks of 5k–50k rows, `FOR UPDATE SKIP LOCKED`)
- [ ] backfill is idempotent (safe to re-run after partial completion)
- [ ] code can run for ≥1 hour with both old and new schema present
- [ ] contract revision runs only after all replicas run the new code

## Reference

- OpenStack Nova `process_revision_directives` pattern
- PostgreSQL expand-contract (komodoai 37 migrations, devopsness.com, jusdb.com)
- that.guru blog on Alembic + SQLAlchemy zero-downtime upgrades
