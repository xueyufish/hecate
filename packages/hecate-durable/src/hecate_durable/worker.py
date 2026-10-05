"""Durable dispatch worker and outbox relay.

``DurableWorker`` claims queued tasks under a resource-key lease, hands them
to an injected dispatcher, and reconciles interrupted work on startup and on
every cycle: due queued tasks re-enter claiming, ``running`` tasks whose
lease expired are re-queued under a fresh fencing token and re-dispatched,
live leases are left alone. Retry is bounded — exhausted attempts park the
task in ``reconciliation_required`` with the failure journaled; there is no
unbounded retry and no silent drop.

The dispatcher is injected (a platform binds its task-control execution
callback, a runner binds its engine dispatch); this module never imports
platform or host code. The store view is duck-typed: ``list_tasks`` /
``get_task_state`` / ``apply_task_state`` plus a lease manager exposing
``acquire``/``renew``/``release`` (the SQL pair or the in-memory doubles).

Fencing story: the lease serializes *dispatch*; the task's ``revision``
(revision CAS on every state write) rejects a superseded executor's late
terminal write; the action ledger's claim tokens fence protected side
effects. A partitioned executor therefore cannot overwrite the takeover —
its writes fail closed and stay journaled.

``OutboxRelay`` drains the transactional outbox (``durable_event_log`` rows
committed in the same transaction as the state change that produced them)
into an injected projection callback under a persistent cursor. Projection
failures never block state writes and never lose events: the pump retries
with the cursor unchanged, and only a poison envelope is skipped after
bounded consecutive failures — recorded in the cursor row so the read-model
gap stays explicit (the authoritative row remains in the log).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import func, select

from hecate_durable.contracts.durable import (
    InvalidTaskTransitionError,
    TaskLifecycleState,
    TaskStateRecord,
)
from hecate_durable.contracts.references import BackendRef
from hecate_durable.storage.lease import LeaseHandle, LeaseManager, StaleFenceError
from hecate_durable.storage.models import EventRow, OutboxCursorRow, OutboxReceiptRow

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset(
    {
        TaskLifecycleState.SUCCEEDED,
        TaskLifecycleState.FAILED,
        TaskLifecycleState.CANCELLED,
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_iso(value: str) -> datetime | None:
    try:
        deadline = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline


class TaskDispatcher(Protocol):
    """Injected execution callback; drives the task to terminal or waiting."""

    async def __call__(self, task_ref: BackendRef, record: TaskStateRecord, lease: LeaseHandle) -> None: ...


class WorkerStore(Protocol):
    """The duck-typed store view the worker needs (SQL store or stub)."""

    def list_tasks(self, states: set[TaskLifecycleState] | None = None) -> list[TaskStateRecord]: ...

    def get_task_state(self, task_ref: BackendRef) -> TaskStateRecord | None: ...

    def apply_task_state(self, task_ref: BackendRef, target: TaskLifecycleState, **kwargs: Any) -> TaskStateRecord: ...


class DurableWorker:
    """Lease-claimed durable dispatch with reconciliation and bounded retry."""

    def __init__(
        self,
        store: WorkerStore,
        dispatcher: TaskDispatcher,
        *,
        leases: Any,
        worker_id: str | None = None,
        lease_ttl: float = 60.0,
        poll_interval: float = 1.0,
        max_attempts: int = 5,
        backoff_base: float = 2.0,
        batch: int = 32,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._leases = leases
        self._worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self._lease_ttl = lease_ttl
        self._poll_interval = poll_interval
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._batch = batch
        self._clock = clock or _utc_now
        self._draining = False
        self._inflight: set[str] = set()
        self._heartbeat_tasks: set[asyncio.Task[None]] = set()

    # -- public API -----------------------------------------------------------

    async def dispatch_once(self, task_ref: BackendRef) -> bool:
        """One claim + dispatch attempt for one task; ``True`` when dispatched.

        Claims the dispatch lease, CAS-advances ``queued → running`` (the
        revision also fences a superseded executor), and invokes the
        dispatcher. Failures count against the bounded retry budget; a
        task that moved on (cancelled, terminal, waiting) is skipped
        honestly rather than forced.
        """

        if self._draining or self._lease_key(task_ref) in self._inflight:
            return False
        record = await asyncio.to_thread(self._store.get_task_state, task_ref)
        if record is None or record.lifecycle_state is not TaskLifecycleState.QUEUED or not self._due(record):
            return False
        key = self._lease_key(task_ref)
        handle = await asyncio.to_thread(self._leases.acquire, key, self._claim_holder(), self._lease_ttl)
        if handle is None:
            return False  # another worker instance owns the dispatch
        record = await asyncio.to_thread(self._store.get_task_state, task_ref)
        if (
            record is None
            or record.lifecycle_state is not TaskLifecycleState.QUEUED
            or not self._due(record)
            or handle.fencing_token != self._leases.current_token(key)
        ):
            await self._release(handle)
            return False  # moved on concurrently, or the lease was taken over
        try:
            await asyncio.to_thread(
                self._store.apply_task_state,
                task_ref,
                TaskLifecycleState.RUNNING,
                expected_revision=record.revision,
                extra_update={"dispatch_starts": int(record.extra.get("dispatch_starts", 0)) + 1},
                **self._fence_kwargs(handle),
            )
        except (InvalidTaskTransitionError, ValueError, StaleFenceError):
            await self._release(handle)
            return False  # cancelled or otherwise moved between read and claim
        self._inflight.add(key)
        heartbeat = asyncio.create_task(self._heartbeat(key, handle))
        self._heartbeat_tasks.add(heartbeat)
        try:
            await self._dispatcher(task_ref, record, handle)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — dispatch failure is a retryable outcome
            logger.warning("dispatch failed for task %s: %s", task_ref.id, exc)
            await self._record_failure(task_ref, exc, expected_revision=record.revision + 1, handle=handle)
            return False
        finally:
            self._inflight.discard(key)
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._heartbeat_tasks.discard(heartbeat)
            try:
                await self._release(handle)
            except Exception:  # noqa: BLE001 — lease expiry clears it regardless
                logger.exception("lease release failed for task %s", task_ref.id)

    async def reconcile(self) -> int:
        """One reconciliation pass: due queued tasks + stale running tasks."""

        dispatched = 0
        queued = await asyncio.to_thread(self._store.list_tasks, {TaskLifecycleState.QUEUED})
        due = [record for record in queued if self._due(record)]
        for record in due[: self._batch]:
            if self._draining:
                break
            if await self.dispatch_once(record.task_ref):
                dispatched += 1
        running = await asyncio.to_thread(self._store.list_tasks, {TaskLifecycleState.RUNNING})
        recovered = 0
        for record in running:
            if self._draining:
                break
            if await self._recover_stale_running(record):
                dispatched += 1
                recovered += 1
                if recovered >= self._batch:
                    break
        return dispatched

    async def run_forever(self) -> None:
        """Dispatch loop; exits when :meth:`request_drain` was requested."""

        while not self._draining:
            try:
                await self.reconcile()
            except Exception:  # noqa: BLE001 — the loop outlives a bad cycle
                logger.exception("reconcile cycle failed")
            await asyncio.sleep(self._poll_interval)

    def request_drain(self) -> None:
        self._draining = True

    @property
    def draining(self) -> bool:
        return self._draining

    async def drain(self, timeout: float = 30.0) -> int:
        """Stop claiming and wait for in-flight dispatches; returns the count still running."""

        self._draining = True
        deadline = time.monotonic() + timeout
        while self._inflight and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return len(self._inflight)

    # -- internals ------------------------------------------------------------

    def _lease_key(self, task_ref: BackendRef) -> str:
        return f"dispatch:{task_ref.issuer_domain}:{task_ref.id}"

    def _claim_holder(self) -> str:
        return f"{self._worker_id}:{uuid.uuid4().hex}"

    def _fence_kwargs(self, handle: LeaseHandle) -> dict[str, Any]:
        return {"lease": handle} if getattr(self._store, "leases", None) is self._leases else {}

    async def _release(self, handle: LeaseHandle) -> None:
        await asyncio.to_thread(
            self._leases.release, handle.lease_key, handle.holder, fencing_token=handle.fencing_token
        )

    def _due(self, record: TaskStateRecord) -> bool:
        next_at = record.extra.get("next_dispatch_at")
        if not next_at:
            return True
        deadline = _parse_iso(str(next_at))
        return deadline is None or deadline <= self._clock()

    async def _heartbeat(self, lease_key: str, handle: LeaseHandle) -> None:
        """Renew the dispatch lease while the dispatcher runs."""

        interval = max(self._lease_ttl / 3.0, 0.05)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await asyncio.to_thread(
                    self._leases.renew,
                    lease_key,
                    handle.holder,
                    self._lease_ttl,
                    fencing_token=handle.fencing_token,
                )
            except StaleFenceError:
                logger.warning("dispatch lease %s was taken over; renewal stopped", lease_key)
                return
            if renewed is None:
                logger.warning("dispatch lease %s lost mid-dispatch; the takeover fences this executor", lease_key)
                return

    async def _recover_stale_running(self, record: TaskStateRecord) -> bool:
        """Re-queue a ``running`` task whose executor lost (or never held) the lease."""

        task_ref = record.task_ref
        key = self._lease_key(task_ref)
        if key in self._inflight:
            return False
        handle = await asyncio.to_thread(self._leases.acquire, key, self._claim_holder(), self._lease_ttl)
        if handle is None:
            return False  # a live executor holds it
        try:
            fresh = await asyncio.to_thread(self._store.get_task_state, task_ref)
            if fresh is None or fresh.lifecycle_state is not TaskLifecycleState.RUNNING:
                return False
            if int(fresh.extra.get("dispatch_starts", 0)) >= self._max_attempts:
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    task_ref,
                    TaskLifecycleState.RECONCILIATION_REQUIRED,
                    expected_revision=fresh.revision,
                    extra_update={"last_error": "dispatch restart budget exhausted"},
                    **self._fence_kwargs(handle),
                )
                return False
            await asyncio.to_thread(
                self._store.apply_task_state,
                task_ref,
                TaskLifecycleState.QUEUED,
                expected_revision=fresh.revision,
                extra_update={"requeued_reason": "dispatch lease expired"},
                **self._fence_kwargs(handle),
            )
        except (InvalidTaskTransitionError, ValueError, StaleFenceError):
            return False  # moved on concurrently; the revision CAS fences it
        finally:
            try:
                await self._release(handle)
            except Exception:  # noqa: BLE001
                logger.exception("lease release failed during recovery of task %s", task_ref.id)
        return await self.dispatch_once(task_ref)

    async def _record_failure(
        self, task_ref: BackendRef, error: Exception, *, expected_revision: int, handle: LeaseHandle
    ) -> None:
        """Count the attempt; re-queue with backoff or park in reconciliation."""

        record = await asyncio.to_thread(self._store.get_task_state, task_ref)
        if (
            record is None
            or record.lifecycle_state is not TaskLifecycleState.RUNNING
            or record.revision != expected_revision
            or self._leases.current_token(handle.lease_key) != handle.fencing_token
        ):
            return  # the dispatcher already drove a terminal/waiting state
        attempts = int(record.extra.get("dispatch_attempts", 0)) + 1
        failure_note = {"dispatch_attempts": attempts, "last_error": str(error)[:512]}
        try:
            if attempts >= self._max_attempts:
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    task_ref,
                    TaskLifecycleState.RECONCILIATION_REQUIRED,
                    extra_update=failure_note,
                    expected_revision=expected_revision,
                    **self._fence_kwargs(handle),
                )
            else:
                backoff = self._backoff_base**attempts
                next_at = self._clock() + timedelta(seconds=backoff)
                await asyncio.to_thread(
                    self._store.apply_task_state,
                    task_ref,
                    TaskLifecycleState.QUEUED,
                    extra_update={**failure_note, "next_dispatch_at": next_at.isoformat()},
                    expected_revision=expected_revision,
                    **self._fence_kwargs(handle),
                )
        except (InvalidTaskTransitionError, ValueError, StaleFenceError):
            # The dispatcher moved the task concurrently; its outcome is
            # authoritative and the failure note is dropped with the attempt.
            logger.info("failure recording skipped for task %s: state moved on", task_ref.id)


class OutboxRelay:
    """Cursor-bounded projector over the transactional outbox.

    ``project`` receives one stored envelope dict per event and must be
    idempotent per ``event_id`` (the platform's read model deduplicates on
    its unique event-id index). A pump never advances the cursor past a
    failing event unless the bounded consecutive-failure budget marks it
    skipped — the skip is recorded on the cursor row, so the read-model gap
    is explicit and the authoritative log row remains untouched.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        project: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        relay_key: str = "default",
        batch: int = 100,
        max_consecutive_failures: int = 5,
        poll_interval: float = 1.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._project = project
        self._relay_key = relay_key
        self._batch = batch
        self._max_failures = max_consecutive_failures
        self._poll_interval = poll_interval
        self._clock = clock or _utc_now
        self._stopping = False
        self._leases = LeaseManager(session_factory, clock=self._clock)

    async def pump_once(self) -> int:
        """Project one bounded batch; returns the number of events projected."""

        handle = self._leases.acquire(f"outbox:{self._relay_key}", uuid.uuid4().hex, 60.0)
        if handle is None:
            return 0
        try:
            return await self._pump(handle)
        finally:
            self._leases.release(handle.lease_key, handle.holder, fencing_token=handle.fencing_token)

    async def _pump(self, handle: LeaseHandle) -> int:
        with self._session_factory() as session:
            cursor = self._cursor(session)
            last_id = cursor.last_event_row_id
            failing_id = cursor.failing_event_id
            failure_count = cursor.failure_count
            skipped: list[str] = list(cursor.skipped_event_ids or [])
        rows = self._fetch(last_id)
        if not rows:
            return 0
        projected = 0
        advanced = last_id
        for row in rows:
            if self._leases.renew(handle.lease_key, handle.holder, 60.0, fencing_token=handle.fencing_token) is None:
                break
            failure_count = self._failure_count(row.event_id)
            try:
                await self._project(row.envelope)
            except Exception:  # noqa: BLE001 — projection failure is the relay's concern
                failing_id, failure_count = row.event_id, failure_count + 1
                if failure_count >= self._max_failures:
                    logger.error(
                        "outbox event %s failed %d consecutive projections; skipping with explicit gap",
                        row.event_id,
                        failure_count,
                        exc_info=True,
                    )
                    skipped.append(row.event_id)
                    self._receipt(row.event_id, "skipped", failure_count, handle)
                    failing_id, failure_count = None, 0
                    advanced = max(advanced, row.id)
                    self._advance(advanced, skipped, failing_id, failure_count)
                    continue
                self._receipt(row.event_id, "retry", failure_count, handle)
                self._advance(advanced, skipped, failing_id, failure_count)
                break  # transient: retry the same cursor next pump
            else:
                failing_id, failure_count = None, 0
                self._receipt(row.event_id, "projected", 0, handle)
                advanced = max(advanced, row.id)
                projected += 1
        self._advance(advanced, skipped, failing_id, failure_count)
        return projected

    async def run_forever(self) -> None:
        while not self._stopping:
            try:
                await self.pump_once()
            except Exception:  # noqa: BLE001 — the relay outlives a bad cycle
                logger.exception("outbox pump cycle failed")
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._stopping = True

    def status(self) -> dict[str, Any]:
        """Cursor watermark and explicit skip list (read-model gap visibility)."""

        with self._session_factory() as session:
            cursor = self._cursor(session)
            max_id = session.execute(select(EventRow.id).order_by(EventRow.id.desc()).limit(1)).scalar_one_or_none()
            return {
                "relay_key": self._relay_key,
                "last_event_row_id": cursor.last_event_row_id,
                "durable_max_id": max_id or 0,
                "lag": session.execute(select(func.count()).select_from(EventRow).where(self._pending())).scalar_one(),
                "skipped_event_ids": list(cursor.skipped_event_ids or []),
            }

    # -- internals ------------------------------------------------------------

    def _cursor(self, session: Any) -> OutboxCursorRow:
        row = session.get(OutboxCursorRow, self._relay_key)
        if row is None:
            row = OutboxCursorRow(
                relay_key=self._relay_key,
                last_event_row_id=0,
                skipped_event_ids=[],
                failing_event_id=None,
                failure_count=0,
                updated_at=self._clock().isoformat(),
            )
            session.add(row)
            session.flush()
        return row

    def _fetch(self, after_id: int) -> list[EventRow]:
        with self._session_factory() as session:
            return list(
                session.execute(select(EventRow).where(self._pending()).order_by(EventRow.id).limit(self._batch))
                .scalars()
                .all()
            )

    def _pending(self) -> Any:
        # A PostgreSQL sequence is allocated before commit. Scanning by a
        # high-water ID alone permanently loses a lower-ID late commit.
        return (
            ~select(OutboxReceiptRow.event_id)
            .where(
                OutboxReceiptRow.relay_key == self._relay_key,
                OutboxReceiptRow.event_id == EventRow.event_id,
                OutboxReceiptRow.state.in_(("projected", "skipped")),
            )
            .exists()
        )

    def _failure_count(self, event_id: str) -> int:
        with self._session_factory() as session:
            receipt = session.get(OutboxReceiptRow, (self._relay_key, event_id))
            return receipt.failure_count if receipt else 0

    def _receipt(self, event_id: str, state: str, failures: int, handle: LeaseHandle) -> None:
        with self._session_factory() as session, session.begin():
            self._leases.assert_valid(session, handle)
            receipt = session.get(OutboxReceiptRow, (self._relay_key, event_id))
            if receipt is None:
                receipt = OutboxReceiptRow(relay_key=self._relay_key, event_id=event_id)
                session.add(receipt)
            receipt.state = state
            receipt.failure_count = failures
            receipt.updated_at = self._clock().isoformat()

    def _advance(self, last_event_row_id: int, skipped: list[str], failing_id: str | None, failure_count: int) -> None:
        with self._session_factory() as session, session.begin():
            cursor = self._cursor(session)
            cursor.last_event_row_id = last_event_row_id
            cursor.skipped_event_ids = skipped
            cursor.failing_event_id = failing_id
            cursor.failure_count = failure_count
            cursor.updated_at = self._clock().isoformat()


def _load_attr(path: str) -> Any:
    """Resolve a ``module:attribute`` factory path (worker entry only)."""

    import importlib

    module_name, _, attr = path.partition(":")
    if not module_name or not attr:
        raise ValueError(f"dispatcher factory must be 'module:attribute', got {path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def _build_argument_parser() -> Any:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m hecate_durable.worker", description=__doc__.splitlines()[0])
    parser.add_argument("--dsn", required=True, help="SQL URL for the durable store (SQLite or PostgreSQL)")
    parser.add_argument(
        "--dispatcher",
        required=True,
        help="'module:attribute' factory returning the async dispatcher callback",
    )
    parser.add_argument(
        "--relay-project", default=None, help="optional 'module:attribute' factory for the outbox projector"
    )
    parser.add_argument("--relay-key", default="default")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--lease-ttl", type=float, default=60.0)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument(
        "--run-seconds",
        type=float,
        default=None,
        help="stop gracefully after this many seconds (smoke tests); default runs until interrupted",
    )
    return parser


def _main(argv: list[str] | None = None) -> int:
    """Standalone worker process entry; exits non-zero on startup failure."""

    from hecate_durable.storage.store import SqlDurableStore

    args = _build_argument_parser().parse_args(argv)
    try:
        store = SqlDurableStore(args.dsn)
        store.create_schema()
    except Exception as exc:  # noqa: BLE001 — startup failure must be explicit
        print(f"worker startup failed: durable store unavailable at {args.dsn!r}: {exc}", flush=True)
        return 2
    try:
        dispatcher_factory = _load_attr(args.dispatcher)
        dispatcher = dispatcher_factory() if callable(dispatcher_factory) else dispatcher_factory
    except Exception as exc:  # noqa: BLE001
        print(f"worker startup failed: dispatcher factory {args.dispatcher!r} could not be loaded: {exc}", flush=True)
        return 2
    worker: DurableWorker = DurableWorker(
        store,
        dispatcher,
        leases=store.leases,
        worker_id=args.worker_id,
        lease_ttl=args.lease_ttl,
        poll_interval=args.poll_interval,
        max_attempts=args.max_attempts,
    )
    relay: OutboxRelay | None = None
    if args.relay_project:
        project_factory = _load_attr(args.relay_project)
        project = project_factory() if callable(project_factory) else project_factory
        relay = OutboxRelay(store.session_factory, project, relay_key=args.relay_key)

    async def _run() -> None:
        tasks: list[asyncio.Task[None]] = [asyncio.create_task(worker.run_forever())]
        if relay is not None:
            tasks.append(asyncio.create_task(relay.run_forever()))
        if args.run_seconds is not None:
            stopper = asyncio.create_task(_stop_after(args.run_seconds, worker, relay))
            tasks.append(stopper)
        try:
            await asyncio.gather(*tasks)
        finally:
            worker.request_drain()
            if relay is not None:
                relay.stop()
            remaining = await worker.drain()
            if remaining:
                logger.warning("worker stopped with %d dispatch(es) still in flight", remaining)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run())
    return 0


async def _stop_after(seconds: float, worker: DurableWorker, relay: OutboxRelay | None) -> None:
    await asyncio.sleep(seconds)
    worker.request_drain()
    if relay is not None:
        relay.stop()


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess smoke test
    raise SystemExit(_main())
