"""Reference-dialect event pagination and attempt-state query regressions."""

from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage.eventlog import SqlEventLog, build_envelope


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
