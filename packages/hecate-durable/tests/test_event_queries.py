"""Reference-dialect event pagination and attempt-state query regressions."""

import sqlite3
from contextlib import closing

from hecate_durable.contracts.durable import IdempotencyKey, TaskLifecycleState
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage.eventlog import SqlEventLog, build_envelope
from sqlalchemy import event


def test_latest_state_is_scoped_to_run_and_source(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "run")
    other = BackendRef(RefKind.RUN, "other-host", "run")
    expected = store.emit_event(task, run, event_type="task_state", payload={"state": "waiting_approval"})
    store.emit_event(task, run, event_type="progress", payload={"status": "succeeded"})
    store.emit_event(task, other, event_type="run_terminal", payload={"status": "failed"})
    external = SqlEventLog(store.session_factory, source="execution_backend", clock=clock.iso)
    with store.session_factory() as session, session.begin():
        external.emit(session, task_ref=task, run_ref=run, event_type="run_terminal", payload={"status": "succeeded"})
    latest = store.events.latest(run, event_types=("task_state", "run_terminal"))
    assert latest is not None
    assert latest.event_id == expected["event_id"]
    assert store.events.latest(run, event_types=("absent",)) is None


def test_pagination_and_gap_capacity(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "run")
    for sequence in range(3):
        store.emit_event(task, run, event_type="progress", payload={"sequence": sequence})
    first = store.read_events(run, limit=2)
    assert first.has_more and first.next_cursor == 2
    last = store.read_events(run, cursor=first.next_cursor, limit=2)
    assert not last.has_more and last.next_cursor == 3
    assert len(first.events) + len(last.events) == 3
    gap_run = BackendRef(RefKind.RUN, "host", "gap-run")
    store.events.append(
        build_envelope(
            task_ref=task,
            run_ref=gap_run,
            source="standalone_host",
            source_sequence=3,
            event_type="progress",
            payload={},
            occurred_at=clock.iso(),
        )
    )
    gap = store.read_events(gap_run, limit=1)
    assert gap.has_more and gap.next_cursor == 2
    actual = store.read_events(gap_run, cursor=gap.next_cursor, limit=1)
    assert not actual.has_more and actual.events[0].source_sequence == 3


def _lose_rows(store, run_ref, sequences: set[int]) -> None:
    """Committed event rows disappear; the sequence counter row survives."""

    from hecate_durable.storage.models import EventRow
    from sqlalchemy import delete

    with store.session_factory() as session, session.begin():
        session.execute(
            delete(EventRow).where(
                EventRow.run_issuer == run_ref.issuer_domain,
                EventRow.run_id == run_ref.id,
                EventRow.source_sequence.in_(sequences),
            )
        )


def test_committed_tail_gap_reports_exact_missing_range(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "tail-run")
    for sequence in range(3):
        store.emit_event(task, run, event_type="progress", payload={"sequence": sequence})
    _lose_rows(store, run, {3})
    # The plain read cannot see the loss: an empty page, nothing fabricated.
    plain = store.read_events(run, cursor=2)
    assert plain.events == [] and plain.next_cursor == 2
    committed = store.read_committed_events(run, cursor=2)
    assert [(event.kind.value, event.gap.from_sequence, event.gap.to_sequence) for event in committed.events] == [
        ("gap", 3, 3)
    ]
    assert committed.events[0].task_ref == task  # a surviving row still names the Task
    assert committed.next_cursor == 3 and committed.has_more is False
    # Once consumed the stream stays quiet: no repeated tail gap.
    again = store.read_committed_events(run, cursor=committed.next_cursor)
    assert again.events == [] and again.next_cursor == 3


def test_committed_tail_gap_when_every_event_row_is_lost(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "tail-all")
    store.submit_task(
        key=IdempotencyKey(key="managed-delivery:tail-all", subject="host", workspace="ws", request_digest="tail-all"),
        task_ref=task,
        run_ref=run,
        input_payload={"goal": "tail"},
    )
    store.emit_event(task, run, event_type="progress", payload={"sequence": 0})
    store.emit_event(task, run, event_type="progress", payload={"sequence": 1})
    # The submission event that submit_task emitted is part of the stream.
    _lose_rows(store, run, {1, 2, 3})
    page = store.read_committed_events(run)
    assert [(event.kind.value, event.gap.from_sequence, event.gap.to_sequence) for event in page.events] == [
        ("gap", 1, 3)
    ]
    assert page.events[0].task_ref == task  # the durable submission binding names the Task
    assert page.next_cursor == 3 and page.has_more is False


def test_current_attempt_is_discoverable_after_losing_all_its_events(store):
    task = BackendRef(RefKind.TASK, "host", "task")
    original = BackendRef(RefKind.RUN, "host", "original")
    successor = BackendRef(RefKind.RUN, "host", "successor")
    store.submit_task(
        key=IdempotencyKey(key="delivery", subject="host", workspace="ws", request_digest="delivery"),
        task_ref=task,
        run_ref=original,
        input_payload={},
    )
    store.apply_task_state(task, TaskLifecycleState.RUNNING, event_run_ref=successor)
    _lose_rows(store, successor, {1})

    assert store.bound_event_runs_for_task(task) == [original, successor]
    [gap] = store.read_committed_events(successor).events
    assert gap.task_ref == task
    assert gap.gap is not None and gap.gap.to_dict() == {"from_sequence": 1, "to_sequence": 1}


def test_committed_read_uses_one_snapshot_during_append(store):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "concurrent")
    if store.engine.dialect.name == "sqlite":
        # Allow the concurrent commit while SQLite still holds the page's read snapshot.
        with closing(sqlite3.connect(store.engine.url.database)) as connection:
            assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    store.emit_event(task, run, event_type="progress", payload={})

    appended = False

    def append_after_counter_read(conn, cursor, statement, parameters, context, executemany):
        nonlocal appended
        if not appended and statement.lstrip().startswith("SELECT") and "durable_run_sequence" in statement:
            appended = True
            store.emit_event(task, run, event_type="progress", payload={})

    event.listen(store._read_engine, "after_cursor_execute", append_after_counter_read)
    try:
        page = store.read_committed_events(run, cursor=1)
    finally:
        event.remove(store._read_engine, "after_cursor_execute", append_after_counter_read)

    assert appended
    assert page.events == [] and page.next_cursor == 1 and not page.has_more
    [later] = store.read_committed_events(run, cursor=1).events
    assert later.source_sequence == 2 and later.gap is None


def test_committed_tail_gap_defers_past_a_full_page(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "tail-page")
    for sequence in range(5):
        store.emit_event(task, run, event_type="progress", payload={"sequence": sequence})
    _lose_rows(store, run, {5})
    first = store.read_committed_events(run, limit=4)
    assert [event.kind.value for event in first.events] == ["event", "event", "event", "event"]
    assert first.has_more is True  # the full page defers the tail to the next one
    assert all(event.gap is None for event in first.events)
    second = store.read_committed_events(run, cursor=first.next_cursor, limit=4)
    assert [(event.kind.value, event.gap.from_sequence, event.gap.to_sequence) for event in second.events] == [
        ("gap", 5, 5)
    ]
    assert second.next_cursor == 5 and second.has_more is False


def test_committed_read_without_loss_matches_plain_read(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "complete")
    for sequence in range(3):
        store.emit_event(task, run, event_type="progress", payload={"sequence": sequence})
    for cursor in (0, 3):
        assert store.read_committed_events(run, cursor=cursor).events == store.read_events(run, cursor=cursor).events
    page = store.read_committed_events(run)
    assert not page.has_more and page.next_cursor == 3


def test_foreign_source_and_counterless_streams_keep_plain_behavior(store, clock):
    task = BackendRef(RefKind.TASK, "host", "task")
    run = BackendRef(RefKind.RUN, "host", "scoped")
    store.emit_event(task, run, event_type="progress", payload={"sequence": 0})
    # A different source's counter on the same run never leaks into this
    # stream's committed tail.
    external = SqlEventLog(store.session_factory, source="execution_backend", clock=clock.iso)
    with store.session_factory() as session, session.begin():
        external.emit(session, task_ref=task, run_ref=run, event_type="external_note", payload={})
    assert store.read_committed_events(run).events == store.read_events(run).events
    # A stream whose rows survive but whose counter row is gone (legacy
    # data) keeps the plain read behavior: no fabricated tail.
    counterless = BackendRef(RefKind.RUN, "host", "counterless")
    store.emit_event(task, counterless, event_type="progress", payload={"sequence": 0})
    from hecate_durable.storage.models import RunSequenceRow
    from sqlalchemy import delete

    with store.session_factory() as session, session.begin():
        session.execute(delete(RunSequenceRow).where(RunSequenceRow.run_id == counterless.id))
    page = store.read_committed_events(counterless)
    assert page.events == store.read_events(counterless).events and not page.has_more
