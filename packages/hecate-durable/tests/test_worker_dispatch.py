"""Worker dispatch tests: claim exclusivity, reconciliation, bounded retry.

The worker is dialect-agnostic over the store view, so the suite runs on
both the SQL reference store (SQLite file; PostgreSQL via
``DURABLE_TEST_POSTGRES_URL``) and the InMemory doubles — the same
parameterization rule as the fault-injection suite.
"""

from __future__ import annotations

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
