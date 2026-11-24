# Verify managed wait resource recovery

The drill exercises platform-local Controller tasks with pure API tools over
persistent SQL state. The model and business tools are deterministic doubles.
Dispatch, decisions, actions, commands, approval and wait reconcilers run through
the production chain.

## Release and recovery assertions

The tests in `tests/test_execution/test_wait_resource_recovery.py` drive separate
workers using `tests/test_execution/_wait_resource_process.py`. Input, approval
and timer waits release inflight dispatches, heartbeat tasks, dispatch leases
and checked-out database connections. Completed worker cycles make no further
model or tool calls for the waiting task. The same worker completes another
task while the original remains parked.

After the park and cleanup are observed, the parent kills worker A and starts
worker B against the same database. Recovery preserves Task/Run identity,
completed receipts, wait contracts, deadlines and Controller limits. Repeated
commands replay one receipt, distinct late commands are rejected, and repeated
timer scans consume one wake. Timer due times are injected and recorded.

Fault cases cancel an input wait, expire its timeout, or revoke an approval
while the worker is stopped. Replacement workers cannot execute new work under
these dispositions. A separate turn-cap case verifies that pre-park turns still
count against the original limit.

## Timer wake under backlog and worker takeover

The timer drills use the production wait reconciler in the worker process
and let the wait expire on the database clock. After the park, the parent
submits more than one dispatch batch of work. Each queued task makes one
scripted model call with a fixed delay.

In the first drill a single worker drains the backlog while the deadline
passes. The worker records queue depth immediately before each scan, so
the consuming scan must show that sibling tasks are still queued. In the
takeover drill worker A pauses after completing a cycle with queued work
remaining. The parent stops A at that boundary and uses the database
clock to verify that the deadline passes while both workers are offline.
Worker B starts against the same database and wakes the original Task
and Run on its first scan.

Both drills check that a manual wake is rejected and that the worker
consumes the wait once across the observed cycles, at or after its
deadline. The completed action receipt, wait contract and deadline
survive recovery without executing the declaring action again. The
parked wait releases the dispatch lease, heartbeat tasks and checked-out
connections, and all queued tasks finish successfully.

Evidence records database-clock samples after the wake scan returns and
after the parent observes terminal state. The first segment measures the
deadline to the first sample. The second measures the interval between
the samples. Both include observation overhead. Evidence also includes
the database tier, queue depth, dispatch batch and worker configuration.
Every observation window is bounded.

## Evidence and measurement

Each test writes a separate JSON file. Set `HECATE_WAITRES_EVIDENCE_DIR` to retain
the files outside pytest temporary directories. Evidence includes the database
tier, source commit, working-tree status, source-file digests, Task/Run identities
and worker PIDs. Connection credentials and wait tokens are excluded.

Completed worker cycles record CPU seconds from `time.process_time()`, monotonic
timestamps, inflight and heartbeat counts, SQLAlchemy pool checkouts, dispatches,
and model/tool calls. Idle-cycle CPU and wall deltas include shared scanners.
Earlier recovery drills measure latency with the parent's monotonic clock.
Resource release and execution correctness are the hard
assertions. Performance measurements establish a baseline. Provider token usage
and monetary costs are unknown for the scripted model.

## Run locally and on PostgreSQL

The default local tier uses a shared SQLite file with WAL enabled.

```bash
bash scripts/dev.sh test tests/test_execution/test_wait_resource_recovery.py -q -rA
```

Install the declared PostgreSQL profile and select a dedicated test database.
Each test creates and removes its own schema. Child credentials travel through
the environment.

```bash
python scripts/dev.py sync --profile postgres
export HECATE_STEP6_POSTGRES_URL=postgresql+psycopg://hecate:hecate@localhost:5432/postgres
export HECATE_WAITRES_EVIDENCE_DIR=/tmp/wait-resource-evidence
python scripts/dev.py test tests/test_execution/test_wait_resource_recovery.py \
  -q -rA --timeout=420 --timeout-method=thread -p no:cacheprovider
```

The `step6-continuation-pg` CI job runs the drill on PostgreSQL and uploads the
JSON files as `managed-wait-resource-evidence`. SQLite evidence applies to the
SQLite tier. The PostgreSQL result is required for the PostgreSQL profile.

Shared worker fixes also require `packages/hecate-durable/tests/test_worker_dispatch.py`
and `packages/hecate-runner/tests/test_waiting_wake.py`. After integration, rerun the
Controller clarification report tests alongside the affected waits.
