"""Durable input revisions: acceptance atomicity, order and corruption.

Covers the controller-loop input-history slice of the SQL store: revisions
commit in the same transaction as the wake's state CAS and command flip,
the (task, source command) key makes acceptance idempotent, the listing
returns the true causal order for replay, a tampered content digest stops
replay instead of reserializing history, and the committed decision count
fixes each revision's causal boundary. SQLite always; PostgreSQL when
``DURABLE_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import os
import uuid

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.contracts.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyKey,
    TaskLifecycleState,
)
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage.models import AcceptedDecisionRow, InputRevisionRow
from hecate_runtime.accepted_decision import RunRef
from sqlalchemy import select

POSTGRES_URL = os.environ.get("DURABLE_TEST_POSTGRES_URL", "")
URLS = [""] + ([POSTGRES_URL] if POSTGRES_URL else [])


@pytest.fixture(params=URLS, ids=["sqlite"] + (["postgres"] if len(URLS) > 1 else []))
def database(request, tmp_path):
    with decision_database(tmp_path, request.param) as values:
        yield values


RUN = RunRef("domain", "run-rev-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
SQL_TASK = BackendRef(RefKind.TASK, RUN.issuer_domain, "task-" + RUN.run_id)


def _revision(source_ref: str, content: dict, effective_turn: int, **extra) -> dict:
    return {
        "source": "provide_input",
        "source_ref": source_ref,
        "content": content,
        "wait_token": "wait-token-1",
        "effective_turn": effective_turn,
        **extra,
    }


def test_wake_acceptance_records_revision_and_receipt_together(database) -> None:
    """The revision, the requeue and the applied command commit in one step."""
    backend, _clock, _url = database
    backend.submit_task(
        key=IdempotencyKey(
            key=f"{RUN.issuer_domain}:{RUN.run_id}", subject="app", workspace="ws", request_digest="b" * 64
        ),
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        input_payload={"controller_loop": True},
    )
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING)
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.WAITING_INPUT)
    command_id = f"cmd-{uuid.uuid4()}"
    backend.record(
        ControlCommandRecord(
            command_id=command_id,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer="app-reader",
            task_ref=SQL_TASK,
            issued_at="2026-01-01T00:00:00+00:00",
            state=CommandState.REQUESTED,
        )
    )

    backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.QUEUED,
        extra_update={"wait": {"consumed": True}},
        applied_command_id=command_id,
        input_revision=_revision(command_id, {"region": "EU"}, effective_turn=2),
    )

    revisions = backend.list_input_revisions(SQL_TASK)
    assert len(revisions) == 1
    assert revisions[0]["content"] == {"region": "EU"}
    assert revisions[0]["effective_turn"] == 2
    assert revisions[0]["source_ref"] == command_id
    receipt = backend.get(command_id)
    assert receipt is not None and receipt.state is CommandState.APPLIED
    assert receipt.extra.get("input_revision") == revisions[0]["revision_id"]
    types = [e.payload.get("event_type") for e in backend.read_events(SQL_RUN, limit=50).events]
    assert "input_revision" in types
    # The applied receipt remains even if an authoritative row is lost.
    # Recovery must fail before asking a model that never sees this input.
    from sqlalchemy import delete

    with backend.session_factory() as session, session.begin():
        session.execute(delete(InputRevisionRow))
    with pytest.raises(ValueError, match="incomplete"):
        backend.list_input_revisions(SQL_TASK, SQL_RUN)


def test_racing_input_acceptances_publish_only_one_revision_and_receipt(database) -> None:
    """The losing CAS cannot publish an input, receipt or input event."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    backend, _clock, _url = database
    prepare_decision_run(backend, RUN)
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING)
    parked = backend.apply_task_state(SQL_TASK, TaskLifecycleState.WAITING_INPUT)
    commands = [f"racing-input-{index}" for index in range(2)]
    for command_id in commands:
        backend.record(
            ControlCommandRecord(
                command_id=command_id,
                kind=ControlCommandKind.PROVIDE_INPUT,
                issuer="app-reader",
                task_ref=SQL_TASK,
                issued_at="2026-01-01T00:00:00+00:00",
                state=CommandState.REQUESTED,
            )
        )
    ready = Barrier(2)

    def accept(command_id):
        ready.wait(timeout=10)
        try:
            backend.apply_task_state(
                SQL_TASK,
                TaskLifecycleState.QUEUED,
                expected_revision=parked.revision,
                extra_update={"wait": {"consumed": True}},
                applied_command_id=command_id,
                input_revision=_revision(command_id, {"region": command_id}, 1),
            )
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, commands)) == [False, True]
    rows = backend.list_input_revisions(SQL_TASK, SQL_RUN)
    assert len(rows) == 1
    receipts = [backend.get(command_id) for command_id in commands]
    assert sorted(receipt.state.value for receipt in receipts) == ["applied", "requested"]
    applied = next(receipt for receipt in receipts if receipt.state is CommandState.APPLIED)
    assert applied.extra["input_revision"] == rows[0]["revision_id"]
    events = backend.read_events(SQL_RUN, limit=50).events
    assert sum(event.payload.get("event_type") == "input_revision" for event in events) == 1


def test_expired_receipt_rolls_back_revision_and_wait_consumption(database) -> None:
    """Expiry checked after revision insertion still rolls the whole wake back."""
    backend, _clock, _url = database
    prepare_decision_run(backend, RUN)
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING)
    parked = backend.apply_task_state(
        SQL_TASK, TaskLifecycleState.WAITING_INPUT, extra_update={"wait": {"consumed": False}}
    )
    backend.record(
        ControlCommandRecord(
            command_id="expired-input",
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer="app-reader",
            task_ref=SQL_TASK,
            issued_at="2025-01-01T00:00:00+00:00",
            expires_at="2025-01-02T00:00:00+00:00",
            state=CommandState.REQUESTED,
        )
    )
    with pytest.raises(ValueError, match="expired"):
        backend.apply_task_state(
            SQL_TASK,
            TaskLifecycleState.QUEUED,
            expected_revision=parked.revision,
            extra_update={"wait": {"consumed": True}},
            applied_command_id="expired-input",
            input_revision=_revision("expired-input", {"region": "EU"}, 1),
        )
    current = backend.get_task_state(SQL_TASK)
    assert current.revision == parked.revision and current.extra["wait"]["consumed"] is False
    assert backend.get("expired-input").state is CommandState.REQUESTED
    assert backend.list_input_revisions(SQL_TASK, SQL_RUN) == []
    assert all(
        event.payload.get("event_type") != "input_revision" for event in backend.read_events(SQL_RUN, limit=50).events
    )


def test_same_source_command_replays_idempotently_and_conflicts_on_content(database) -> None:
    """Duplicate acceptance returns the original revision; new content is refused."""
    backend, _clock, _url = database
    backend.submit_task(
        key=IdempotencyKey(
            key=f"{RUN.issuer_domain}:{RUN.run_id}-2", subject="app", workspace="ws", request_digest="c" * 64
        ),
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        input_payload={},
    )
    source_ref = f"cmd-{uuid.uuid4()}"
    first = backend.apply_task_state(
        SQL_TASK, TaskLifecycleState.QUEUED, input_revision=_revision(source_ref, {"region": "EU"}, 1)
    )
    second = backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.RUNNING,
        input_revision=_revision(source_ref, {"region": "EU"}, 1),
    )
    assert second.revision == first.revision + 1  # the state advanced once
    revisions = backend.list_input_revisions(SQL_TASK)
    assert len(revisions) == 1  # ...but the revision did not duplicate
    with pytest.raises(ValueError, match="bound to different content"):
        backend.apply_task_state(
            SQL_TASK,
            TaskLifecycleState.WAITING_INPUT,
            input_revision=_revision(source_ref, {"region": "US"}, 1),
        )


def test_listing_returns_causal_order_and_seq_breaks_ties(database) -> None:
    """Order is (effective_turn, seq) — the fold order replay relies on."""
    backend, _clock, _url = database
    backend.submit_task(
        key=IdempotencyKey(
            key=f"{RUN.issuer_domain}:{RUN.run_id}-3", subject="app", workspace="ws", request_digest="d" * 64
        ),
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        input_payload={},
    )
    backend.apply_task_state(
        SQL_TASK, TaskLifecycleState.QUEUED, input_revision=_revision("cmd-late", {"tier": "gold"}, 3)
    )
    backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.RUNNING,
        input_revision=_revision("cmd-early-b", {"zone": "b"}, 2),
    )
    backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.WAITING_INPUT,
        input_revision=_revision("cmd-early-a", {"zone": "a"}, 2),
    )
    revisions = backend.list_input_revisions(SQL_TASK)
    # Same boundary: durable seq (insertion order) breaks the tie.
    assert [row["source_ref"] for row in revisions] == ["cmd-early-b", "cmd-early-a", "cmd-late"]
    assert [row["effective_turn"] for row in revisions] == [2, 2, 3]


def test_tampered_content_digest_stops_replay(database) -> None:
    """Corrupted history is an explicit error, never a silent reserialize."""
    backend, _clock, _url = database
    backend.submit_task(
        key=IdempotencyKey(
            key=f"{RUN.issuer_domain}:{RUN.run_id}-4", subject="app", workspace="ws", request_digest="e" * 64
        ),
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        input_payload={},
    )
    backend.apply_task_state(
        SQL_TASK, TaskLifecycleState.QUEUED, input_revision=_revision("cmd-x", {"region": "EU"}, 1)
    )
    with backend.session_factory() as session, session.begin():
        row = session.execute(select(InputRevisionRow)).scalar_one()
        row.content = {"region": "TAMPERED"}
    with pytest.raises(ValueError, match="content digest mismatch"):
        backend.list_input_revisions(SQL_TASK)


def test_count_accepted_decisions_measures_the_causal_boundary(database) -> None:
    """The wake writer fixes effective_turn from the committed chain length."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    for step in ("agent-turn-000001", "agent-turn-000002"):
        with backend.session_factory() as session, session.begin():
            record = AcceptedDecisionRow(
                id=f"dec-{step}",
                issuer_domain=RUN.issuer_domain,
                run_id=RUN.run_id,
                decision_step_id=step,
                schema_version=1,
                input_refs={},
                accepted_output={"text": "working"},
                actions=[],
                content_digest="0" * 64,
                fence_holder=owner.holder,
                fence_token=owner.fence_token,
                accepted_at="2026-01-01T00:00:00+00:00",
            )
            session.add(record)
    assert backend.count_accepted_decisions(SQL_RUN) == 2
    assert backend.count_accepted_decisions(BackendRef(RefKind.RUN, RUN.issuer_domain, "run-without-decisions")) == 0
    with backend.session_factory() as session, session.begin():
        session.add(
            AcceptedDecisionRow(
                id="foreign-decision",
                issuer_domain="foreign",
                run_id=RUN.run_id,
                decision_step_id="agent-turn-000001",
                schema_version=1,
                input_refs={},
                accepted_output={},
                actions=[],
                content_digest="0" * 64,
                accepted_at="2026-01-01T00:00:00+00:00",
            )
        )
    assert backend.count_accepted_decisions(SQL_RUN) == 2
    assert backend.count_accepted_decisions(BackendRef(RefKind.RUN, "foreign", RUN.run_id)) == 1


def test_in_memory_store_keeps_signature_parity() -> None:
    """The stub accepts and lists revisions with the same semantics."""
    from hecate_durable.stub import InMemoryDurableTaskStore

    store = InMemoryDurableTaskStore()
    store.apply_task_state(SQL_TASK, TaskLifecycleState.QUEUED, input_revision=_revision("cmd-m", {"region": "EU"}, 1))
    store.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.RUNNING,
        input_revision=_revision("cmd-m", {"region": "EU"}, 1),
    )
    revisions = store.list_input_revisions(SQL_TASK)
    assert len(revisions) == 1 and revisions[0]["content"] == {"region": "EU"}
    assert store.count_accepted_decisions(SQL_RUN) == 0


def test_stub_cas_failure_never_publishes_input_revision() -> None:
    """A rejected state transition must not leave accepted input behind."""
    from hecate_durable.stub import InMemoryDurableTaskStore

    store = InMemoryDurableTaskStore()
    store.apply_task_state(SQL_TASK, TaskLifecycleState.QUEUED)
    with pytest.raises(ValueError, match="stale revision"):
        store.apply_task_state(
            SQL_TASK,
            TaskLifecycleState.RUNNING,
            expected_revision=100,
            input_revision=_revision("bad-cas", {"region": "EU"}, 1),
        )
    assert store.list_input_revisions(SQL_TASK) == []


@pytest.mark.parametrize("field, value", [("effective_turn", 2), ("wait_token", "other-wait"), ("source", "other")])
def test_replayed_source_cannot_move_causal_binding(database, field, value) -> None:
    """Command identity fixes causal metadata as well as payload content."""
    backend, _clock, _url = database
    backend.submit_task(
        key=IdempotencyKey(key="binding", subject="app", workspace="ws", request_digest="f" * 64),
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        input_payload={},
    )
    original = _revision("bound-source", {"region": "EU"}, 1)
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.QUEUED, input_revision=original)
    with pytest.raises(ValueError, match="bound to different content"):
        backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING, input_revision={**original, field: value})
    assert backend.get_task_state(SQL_TASK).lifecycle_state is TaskLifecycleState.QUEUED
    with pytest.raises(ValueError, match="another Run"):
        backend.list_input_revisions(SQL_TASK, BackendRef(RefKind.RUN, "foreign", SQL_RUN.id))


def test_revision_boundary_is_selected_inside_acceptance_transaction(database) -> None:
    """The caller cannot accidentally select a different namespace's boundary."""
    backend, _clock, _url = database
    prepare_decision_run(backend, RUN)
    revision = _revision("atomic-boundary", {"region": "EU"}, 1)
    revision.pop("effective_turn")
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING, input_revision=revision)
    assert backend.list_input_revisions(SQL_TASK, SQL_RUN)[0]["effective_turn"] == 1


def test_migration_upgrade_and_downgrade_preserve_accepted_history(database, monkeypatch) -> None:
    """Platform DDL matches standalone storage and refuses to discard facts."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    backend, _clock, _url = database
    path = Path(__file__).resolve().parents[3] / "alembic/versions/020_durable_input_revision.py"
    spec = importlib.util.spec_from_file_location("input_revision_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with backend.engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        migration.downgrade()  # fixture-owned, empty table only
        migration.upgrade()
    prepare_decision_run(backend, RUN)
    backend.apply_task_state(
        SQL_TASK, TaskLifecycleState.RUNNING, input_revision=_revision("keep-history", {"region": "EU"}, 1)
    )
    with backend.engine.begin() as connection:
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
        with pytest.raises(RuntimeError, match="input history must be retained"):
            migration.downgrade()
    assert backend.list_input_revisions(SQL_TASK)[0]["content"] == {"region": "EU"}
