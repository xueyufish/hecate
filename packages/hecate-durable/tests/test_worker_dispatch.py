"""Worker dispatch tests: claim exclusivity, reconciliation, bounded retry.

The worker is dialect-agnostic over the store view, so the suite runs on
both the SQL reference store (SQLite file; PostgreSQL via
``DURABLE_TEST_POSTGRES_URL``) and the InMemory doubles — the same
parameterization rule as the fault-injection suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from _helpers import MutableClock, dialects, make_store
from hecate_durable.contracts.durable import TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage.lease import LeaseManager
from hecate_durable.stub import InMemoryDurableTaskStore, InMemoryLeaseManager
from hecate_durable.worker import DurableWorker, OutboxRelay

# --- fixtures -----------------------------------------------------------------


class RecordingDispatcher:
    """Async dispatcher double: records invocations, scripted outcomes."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.calls: list[str] = []
        self.fail_times = fail_times

    async def __call__(self, task_ref: BackendRef, record, lease) -> None:  # noqa: ANN001
        self.calls.append(task_ref.id)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("scripted dispatch failure")
        self._store.apply_task_state(task_ref, TaskLifecycleState.SUCCEEDED)

    def bind(self, store) -> None:  # noqa: ANN001
        self._store = store


@pytest.fixture(params=dialects() + ["inmemory"])
def suite(request: pytest.FixtureRequest, tmp_path) -> AsyncIterator[tuple[object, object, MutableClock]]:
    from hecate_durable.storage.models import Base

    clock = MutableClock()
    if request.param == "inmemory":
        store = InMemoryDurableTaskStore()
        leases = InMemoryLeaseManager(clock=lambda: clock.now)
    else:
        store = make_store(request.param, tmp_path, name=f"worker-{request.param}.db")
        # The postgres URL names one physical database shared by the whole
        # parameterized run — reset the package-owned tables before every
        # test so leftover rows (e.g. a previous test's terminal task with
        # the same id) cannot leak in. Same isolation rule as conftest.
        Base.metadata.drop_all(store.engine)
        Base.metadata.create_all(store.engine)
        leases = LeaseManager(store.session_factory, clock=lambda: clock.now)
        store.leases = leases

    yield store, leases, clock
    if request.param != "inmemory":
        store.dispose()


def submit_task(store, task_id: str, *, state: TaskLifecycleState | None = None, **extra) -> BackendRef:
    """Seed one queued task (optionally advanced to ``state``) for a test."""

    ref = BackendRef(RefKind.TASK, "host", task_id)
    store.apply_task_state(ref, TaskLifecycleState.QUEUED, input_payload={"goal": "test"}, extra_update=dict(extra))
    if state is not None and state is not TaskLifecycleState.QUEUED:
        store.apply_task_state(ref, state)
    return ref


def make_worker(store, leases, clock: MutableClock, dispatcher, **kwargs) -> DurableWorker:
    return DurableWorker(
        store,
        dispatcher,
        leases=leases,
        worker_id="worker-under-test",
        lease_ttl=30.0,
        poll_interval=0.01,
        clock=lambda: clock.now,
        **kwargs,
    )


# --- dispatch ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_dispatches_queued_task(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1")
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher)

    assert await worker.dispatch_once(ref) is True
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.SUCCEEDED
    # A terminal task is never re-dispatched.
    assert await worker.dispatch_once(ref) is False
    assert len(dispatcher.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_claim_is_exclusive(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1")
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher)

    # Another holder owns the dispatch lease: this worker skips honestly.
    assert leases.acquire(f"dispatch:{ref.issuer_domain}:{ref.id}", "other-worker", 30.0) is not None
    assert await worker.dispatch_once(ref) is False
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.QUEUED
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_reconcile_dispatches_due_tasks(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1")
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher)

    assert await worker.reconcile() == 1
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.SUCCEEDED


@pytest.mark.asyncio
async def test_backoff_gates_retry_until_due(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1")
    dispatcher = RecordingDispatcher(fail_times=1)
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher, max_attempts=3)

    assert await worker.dispatch_once(ref) is False  # first attempt fails
    state = store.get_task_state(ref)
    assert state.lifecycle_state is TaskLifecycleState.QUEUED
    assert state.extra["dispatch_attempts"] == 1
    assert "next_dispatch_at" in state.extra
    # Not due yet: reconcile does nothing even though the task is queued.
    assert await worker.reconcile() == 0
    clock.advance(3600)
    assert await worker.reconcile() == 1
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.SUCCEEDED


@pytest.mark.asyncio
async def test_exhausted_attempts_park_in_reconciliation(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1")
    dispatcher = RecordingDispatcher(fail_times=99)
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher, max_attempts=3)

    for _ in range(3):
        clock.advance(3600)  # clear the backoff gate each cycle
        await worker.reconcile()
    state = store.get_task_state(ref)
    assert state.lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert state.extra["dispatch_attempts"] == 3
    assert "scripted dispatch failure" in state.extra["last_error"]
    # Parked tasks are not retried automatically.
    clock.advance(3600)
    assert await worker.reconcile() == 0


# --- reconciliation ---------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_running_is_requeued_and_redispatched(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1", state=TaskLifecycleState.RUNNING)  # crashed after claim
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher)

    assert await worker.reconcile() == 1
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.SUCCEEDED


@pytest.mark.asyncio
async def test_live_lease_is_not_disturbed(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1", state=TaskLifecycleState.RUNNING)
    leases.acquire(f"dispatch:{ref.issuer_domain}:{ref.id}", "live-executor", 300.0)
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher)

    assert await worker.reconcile() == 0
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.RUNNING
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_drain_stops_claiming(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "t1")
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher)

    worker.request_drain()
    assert await worker.dispatch_once(ref) is False
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.QUEUED


# --- outbox relay -----------------------------------------------------------


def _submit_with_stream(store, task_id: str) -> tuple[BackendRef, BackendRef]:
    """Submit with run linkage so state transitions emit outbox events."""

    task = BackendRef(RefKind.TASK, "host", task_id)
    run = BackendRef(RefKind.RUN, "host", f"run-{task_id}")
    from hecate_durable.contracts.durable import IdempotencyKey

    store.submit_task(
        key=IdempotencyKey(key=f"key-{task_id}", subject="s", workspace="w", request_digest="d" * 8),
        task_ref=task,
        run_ref=run,
        input_payload={"goal": "test"},
    )
    return task, run


async def test_relay_projects_events_once(tmp_path) -> None:
    store = make_store("sqlite", tmp_path, name="relay.db")
    store.create_schema()
    task, _run = _submit_with_stream(store, "t1")
    store.apply_task_state(task, TaskLifecycleState.RUNNING)
    store.apply_task_state(task, TaskLifecycleState.SUCCEEDED)

    projected: list[dict] = []

    async def project(envelope: dict) -> None:
        projected.append(envelope)

    relay = OutboxRelay(store.session_factory, project)
    assert await relay.pump_once() == 3
    assert await relay.pump_once() == 0  # cursor advanced; no re-projection
    assert [e["payload"]["event_type"] for e in projected] == [
        "task_submitted",
        "task_state",
        "task_state",
    ]
    status = relay.status()
    assert status["lag"] == 0 and status["skipped_event_ids"] == []


async def test_relay_retries_transient_failures(tmp_path) -> None:
    store = make_store("sqlite", tmp_path, name="relay-retry.db")
    store.create_schema()
    _submit_with_stream(store, "t1")

    attempts = {"n": 0}

    async def flaky(envelope: dict) -> None:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("read model down")

    relay = OutboxRelay(store.session_factory, flaky)
    assert await relay.pump_once() == 0  # failed; cursor unchanged
    assert await relay.pump_once() == 1  # recovered; no loss
    assert relay.status()["lag"] == 0


async def test_relay_skips_poison_after_bounded_failures(tmp_path) -> None:
    store = make_store("sqlite", tmp_path, name="relay-poison.db")
    store.create_schema()
    task, _run = _submit_with_stream(store, "t1")
    first_event_id = store.read_events(_run).events[0].event_id
    store.apply_task_state(task, TaskLifecycleState.RUNNING)

    poisoned = {first_event_id}

    async def project(envelope: dict) -> None:
        if envelope["event_id"] in poisoned:
            raise RuntimeError("poison envelope")

    relay = OutboxRelay(store.session_factory, project, max_consecutive_failures=3)
    for _ in range(3):
        await relay.pump_once()  # bounded retries on the poison event
    assert await relay.pump_once() == 0  # poison skipped, nothing else pending
    status = relay.status()
    assert first_event_id in status["skipped_event_ids"]
    assert status["lag"] == 0  # cursor moved past the explicit gap


def test_expired_same_holder_gets_new_generation(suite) -> None:
    store, leases, clock = suite
    first = leases.acquire("resource", "worker", 30)
    clock.advance(31)
    assert leases.renew("resource", "worker", 30, fencing_token=first.fencing_token) is None
    second = leases.acquire("resource", "worker", 30)
    assert second.fencing_token > first.fencing_token
    assert leases.release("resource", "worker", fencing_token=first.fencing_token) is False
    assert leases.current_token("resource") == second.fencing_token
    leases.release("resource", "worker", fencing_token=second.fencing_token)
    third = leases.acquire("resource", "worker", 30)
    assert third.fencing_token > second.fencing_token


async def test_old_dispatch_failure_cannot_requeue_new_running_attempt(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "late-failure")
    entered, finish = asyncio.Event(), asyncio.Event()

    async def stale_dispatch(task, record, lease):
        entered.set()
        await finish.wait()
        raise RuntimeError("old executor woke up")

    worker = make_worker(store, leases, clock, stale_dispatch)
    old = asyncio.create_task(worker.dispatch_once(ref))
    await entered.wait()
    clock.advance(31)
    successor = leases.acquire(f"dispatch:{ref.issuer_domain}:{ref.id}", "successor", 30)
    assert successor is not None
    store.apply_task_state(ref, TaskLifecycleState.QUEUED, expected_revision=1)
    store.apply_task_state(ref, TaskLifecycleState.RUNNING, expected_revision=2)
    fresh = store.get_task_state(ref)
    finish.set()
    assert await old is False
    assert store.get_task_state(ref) == fresh


async def test_deferred_old_tasks_do_not_starve_due_tasks(suite) -> None:
    store, leases, clock = suite
    submit_task(store, "deferred", next_dispatch_at="2099-01-01T00:00:00+00:00")
    ready = submit_task(store, "ready")
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher, batch=1)
    assert await worker.reconcile() == 1
    assert store.get_task_state(ready).lifecycle_state is TaskLifecycleState.SUCCEEDED


async def test_outbox_late_lower_id_is_delivered_and_cursor_survives_restart(tmp_path) -> None:
    from hecate_durable.storage.models import EventRow
    from sqlalchemy import select

    store = make_store("sqlite", tmp_path, name="late-commit.db")
    store.create_schema()
    _submit_with_stream(store, "first")
    with store.session_factory() as session, session.begin():
        late = session.execute(select(EventRow)).scalar_one()
        session.delete(late)
    _submit_with_stream(store, "second")
    with store.session_factory() as session, session.begin():
        second = session.execute(select(EventRow)).scalar_one()
        second.id = 2
    seen: list[str] = []

    async def project(event):
        seen.append(event["event_id"])

    relay = OutboxRelay(store.session_factory, project)
    assert await relay.pump_once() == 1
    with store.session_factory() as session, session.begin():
        # The SQLite test reproduces visibility order; PostgreSQL allocates
        # this lower ID in an earlier, still uncommitted transaction.
        session.add(
            EventRow(
                id=1,
                run_issuer=late.run_issuer,
                run_id=late.run_id,
                source=late.source,
                source_sequence=late.source_sequence,
                event_id=late.event_id,
                envelope=late.envelope,
                received_at=late.received_at,
            )
        )
    restarted = OutboxRelay(store.session_factory, project)
    assert restarted.status()["lag"] == 1
    assert await restarted.pump_once() == 1
    assert seen == [second.event_id, late.event_id]
    assert await restarted.pump_once() == 0
    store.dispose()


async def test_postgres_reordered_commits_are_not_lost(store) -> None:
    """Real PG transactions allocate IDs in one order and commit in another."""
    if store.engine.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL for concurrent writer transactions")
    seen = []

    async def project(envelope):
        seen.append(envelope["event_id"])

    with store.session_factory() as slow, slow.begin():
        early = store.events.emit(
            slow,
            task_ref=BackendRef(RefKind.TASK, "pg", "a"),
            run_ref=BackendRef(RefKind.RUN, "pg", "a"),
            event_type="test",
            payload={},
        )
        slow.flush()
        with store.session_factory() as fast, fast.begin():
            late = store.events.emit(
                fast,
                task_ref=BackendRef(RefKind.TASK, "pg", "b"),
                run_ref=BackendRef(RefKind.RUN, "pg", "b"),
                event_type="test",
                payload={},
            )
        relay = OutboxRelay(store.session_factory, project)
        assert await relay.pump_once() == 1
        assert seen == [late.event_id]
    assert await relay.pump_once() == 1
    assert seen == [late.event_id, early.event_id]


@pytest.mark.asyncio
async def test_crash_restart_budget_is_bounded(suite) -> None:
    store, leases, clock = suite
    ref = submit_task(store, "crash-loop", state=TaskLifecycleState.RUNNING, dispatch_starts=2)
    dispatcher = RecordingDispatcher()
    dispatcher.bind(store)
    worker = make_worker(store, leases, clock, dispatcher, max_attempts=2)
    assert await worker.reconcile() == 0
    assert store.get_task_state(ref).lifecycle_state is TaskLifecycleState.RECONCILIATION_REQUIRED
    assert dispatcher.calls == []
