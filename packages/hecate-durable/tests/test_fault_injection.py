"""Fault-injection suite for the SQL durable core (step6 worktree A).

Covers the four fault classes the plan requires at THIS step — worker
crash, lease expiry, late receipts, and storage failure — against every
configured dialect (SQLite file everywhere; PostgreSQL when
``DURABLE_TEST_POSTGRES_URL`` is set). Crashes are modeled honestly: rows
are committed, the process state is abandoned, and a NEW store instance
over the same database must recover per the contract.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from _helpers import MutableClock, make_store
from hecate_durable.contracts.durable import (
    ActionIntent,
    ActionLedgerState,
    ActionOutcome,
    ActionOutcomeRecord,
    IdempotencyKey,
    TaskLifecycleState,
)
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import (
    LateOutcomeError,
    SqlDurableStore,
    StaleFenceError,
)

TASK = BackendRef(kind=RefKind.TASK, issuer_domain="test", id="task-1")
RUN = BackendRef(kind=RefKind.RUN, issuer_domain="test", id="run-1")


def _key(digest: str = "d" * 64) -> IdempotencyKey:
    return IdempotencyKey(key="k1", subject="app", workspace="ws", request_digest=digest)


def _write_intent(
    action_key: str = "run-1:reserve", *, effect: ToolSideEffectClass = ToolSideEffectClass.NON_IDEMPOTENT_WRITE
) -> ActionIntent:
    return ActionIntent(
        action_key=action_key,
        action_name="submit_inventory_update",
        arguments_digest="a" * 64,
        side_effect_class=effect,
    )


# --- crash windows -------------------------------------------------------------


def test_crash_after_claim_recovers_claimed_pending_reconciliation(
    store: SqlDurableStore, dialect: str, tmp_path
) -> None:
    """TOOL dispatch window: intent + claim committed, outcome missing."""

    store.record_intent_ex(_write_intent(), task_ref=TASK, run_ref=RUN, execution_id="e1")
    receipt, _token = store.claim_ex("run-1:reserve", holder="w1")
    assert receipt.claimed

    # Process dies; a new instance recovers from the committed rows only.
    store.dispose()
    recovered = make_store(dialect, tmp_path)
    try:
        verdict = recovered.recovery("run-1:reserve")
        assert verdict.state is ActionLedgerState.CLAIMED
        assert verdict.pending_reconciliation is True, "a claimed write never auto-replays"
        assert verdict.intent is not None and verdict.intent.action_name == "submit_inventory_update"
    finally:
        recovered.dispose()


def test_crash_after_outcome_recovers_real_result(store: SqlDurableStore, dialect: str, tmp_path) -> None:
    """Outcome committed before the task converged: recovery returns the
    REAL recorded result content and reference — never placeholder text."""

    store.record_intent_ex(_write_intent(), task_ref=TASK, run_ref=RUN, execution_id="e2")
    _receipt, token = store.claim_ex("run-1:reserve", holder="w1")
    store.record_outcome_ex(
        ActionOutcomeRecord(
            action_key="run-1:reserve",
            outcome=ActionOutcome.SUCCEEDED,
            result_ref=BackendRef(kind=RefKind.ARTIFACT, issuer_domain="test", id="art-1"),
            result_digest="b" * 64,
        ),
        claim_token=token,
        result_payload={"written": True, "sku": "SKU-A1"},
    )

    store.dispose()
    recovered = make_store(dialect, tmp_path)
    try:
        verdict = recovered.recovery("run-1:reserve")
        assert verdict.last_outcome is not None
        assert verdict.last_outcome.outcome is ActionOutcome.SUCCEEDED
        assert verdict.last_outcome.result_ref is not None
        assert verdict.last_outcome.result_ref.id == "art-1"
        actions = recovered.list_run_actions(RUN)
        assert actions[0]["result_payload"] == {"written": True, "sku": "SKU-A1"}
    finally:
        recovered.dispose()


def test_crash_between_submission_and_run_replays_idempotently(store: SqlDurableStore, dialect: str, tmp_path) -> None:
    """Submit response lost: the retried submission finds the accepted
    record instead of creating a second task."""

    association = store.submit_task(key=_key(), task_ref=TASK, run_ref=RUN, input_payload={"input": {}})
    store.dispose()

    recovered = make_store(dialect, tmp_path)
    try:
        replay = recovered.submit_task(key=_key(), task_ref=TASK, run_ref=RUN, input_payload={"input": {}})
        assert replay.task_ref.id == association.task_ref.id
        assert replay.run_ref.id == association.run_ref.id
        assert len(recovered.list_tasks()) == 1, "no duplicate task from the replayed submission"
    finally:
        recovered.dispose()


def test_state_and_outbox_commit_together(store: SqlDurableStore) -> None:
    """Every task transition lands with its governance event in one commit."""

    store.submit_task(key=_key(), task_ref=TASK, run_ref=RUN, input_payload={"input": {}})
    store.apply_task_state(TASK, TaskLifecycleState.RUNNING)
    page = store.read_events(RUN)
    types = [e.payload.get("event_type") for e in page.events if e.payload.get("event_type")]
    assert "task_submitted" in types and "task_state" in types
    # governance profile is enforced on every stored envelope
    for envelope in page.events:
        assert envelope.payload.get("event_type") is None or (
            envelope.actor is not None and envelope.source is not None
        )


# --- lease expiry + fencing -------------------------------------------------------


def test_lease_expiry_transfers_with_monotonic_fencing(store: SqlDurableStore, clock: MutableClock) -> None:
    _replace_lease_clock(store, clock)
    leases = store.leases

    first = leases.acquire("run-1", holder="worker-a", ttl_seconds=30)
    assert first is not None and first.fencing_token == 1

    clock.advance(60)  # lease expires
    second = leases.acquire("run-1", holder="worker-b", ttl_seconds=30)
    assert second is not None
    assert second.fencing_token == 2, "ownership change bumps the fencing token"

    # The expired holder cannot renew or pass the fence anymore.
    assert leases.renew("run-1", holder="worker-a", ttl_seconds=30) is None
    with pytest.raises(StaleFenceError):
        leases.validate_fence("run-1", first.fencing_token)
    leases.validate_fence("run-1", second.fencing_token)


def test_lease_renewal_extends_without_token_bump(store: SqlDurableStore, clock: MutableClock) -> None:
    _replace_lease_clock(store, clock)
    first = store.leases.acquire("run-1", holder="worker-a", ttl_seconds=30)
    assert first is not None
    clock.advance(10)
    renewed = store.leases.renew("run-1", holder="worker-a", ttl_seconds=30)
    assert renewed is not None and renewed.fencing_token == first.fencing_token


def _replace_lease_clock(store: SqlDurableStore, clock: MutableClock) -> None:
    from hecate_durable.storage.lease import LeaseManager

    store.leases = LeaseManager(store._session_factory, clock=lambda: clock.now)  # noqa: SLF001


def test_expired_worker_outcome_is_fenced_out(store: SqlDurableStore) -> None:
    """The G2 fencing loop: worker A's attempt fails and releases the claim,
    worker B re-claims (token bumps), and A's LATE duplicate response arrives
    under the stale token — rejected without overwriting B's authority.

    The action is idempotent-write: the only class whose failed attempts may
    re-claim (a non-idempotent failure stays pending reconciliation)."""

    store.record_intent_ex(
        _write_intent(effect=ToolSideEffectClass.IDEMPOTENT_WRITE),
        task_ref=TASK,
        run_ref=RUN,
        execution_id="e3",
    )
    _r1, token_one = store.claim_ex("run-1:reserve", holder="worker-a")
    assert token_one is not None
    # A's attempt definitively fails (slot released for a safe retry).
    store.record_outcome_ex(
        ActionOutcomeRecord(action_key="run-1:reserve", outcome=ActionOutcome.FAILED),
        claim_token=token_one,
    )
    # B wins the retry claim with a fresh token.
    _r2, token_two = store.claim_ex("run-1:reserve", holder="worker-b")
    assert token_two is not None and token_two != token_one

    # A's late duplicate success arrives under the stale token.
    with pytest.raises(LateOutcomeError):
        store.record_outcome_ex(
            ActionOutcomeRecord(action_key="run-1:reserve", outcome=ActionOutcome.SUCCEEDED),
            claim_token=token_one,
        )

    # The authoritative state never saw the stale write; the new owner's
    # outcome still lands.
    store.record_outcome_ex(
        ActionOutcomeRecord(action_key="run-1:reserve", outcome=ActionOutcome.SUCCEEDED),
        claim_token=token_two,
        result_payload={"written": True},
    )
    verdict = store.recovery("run-1:reserve")
    assert verdict.last_outcome is not None and verdict.last_outcome.outcome is ActionOutcome.SUCCEEDED
    # The rejected late receipt was journaled, not silently dropped.
    page = store.read_events(RUN)
    types = [e.payload.get("event_type") for e in page.events]
    assert "late_outcome_rejected" in types


# --- storage failure ---------------------------------------------------------------


class _BrokenSessionFactory:
    """Simulates an unreachable database: every session open fails."""

    def __call__(self):
        raise RuntimeError("storage node unreachable")


def test_storage_failure_recovery_reports_store_unavailable(store: SqlDurableStore) -> None:
    """A failed recovery read returns store_unavailable — never a downgrade
    to never_started."""

    store.record_intent_ex(_write_intent(), task_ref=TASK, run_ref=RUN, execution_id="e4")
    verdict_ok = store.recovery("run-1:reserve")
    assert verdict_ok.state is ActionLedgerState.CLAIMED

    real_factory = store._session_factory  # noqa: SLF001
    store._session_factory = _BrokenSessionFactory()  # noqa: SLF001
    try:
        verdict = store.recovery("run-1:reserve")
    finally:
        store._session_factory = real_factory  # noqa: SLF001
    assert verdict.state is ActionLedgerState.STORE_UNAVAILABLE


def test_storage_failure_write_paths_raise(store: SqlDurableStore) -> None:
    """Write paths fail loudly — callers (the gate) then fail closed for
    side-effecting classes."""

    real_factory = store._session_factory  # noqa: SLF001
    store._session_factory = _BrokenSessionFactory()  # noqa: SLF001
    try:
        with pytest.raises(RuntimeError, match="storage node unreachable"):
            store.record_intent(_write_intent())
        with pytest.raises(RuntimeError, match="storage node unreachable"):
            store.apply_task_state(TASK, TaskLifecycleState.QUEUED)
    finally:
        store._session_factory = real_factory  # noqa: SLF001


# --- event log integrity -------------------------------------------------------------


def test_event_log_dedup_and_conflict(store: SqlDurableStore) -> None:
    from dataclasses import replace

    from hecate_durable.storage import EventConflictError
    from hecate_durable.storage.eventlog import build_envelope

    envelope = build_envelope(
        task_ref=TASK,
        run_ref=RUN,
        source="standalone_host",
        source_sequence=1,
        event_type="external_upload",
        payload={"n": 1},
        occurred_at="2026-01-01T00:00:00Z",
        event_id="evt-dup",
    )
    assert store.events.append(envelope) == 1
    assert store.events.append(envelope) == 1, "identical replay is idempotent"
    replay_with_new_timestamps = replace(
        envelope,
        occurred_at="2026-01-02T00:00:00Z",
        received_at="2026-01-02T00:00:01Z",
    )
    assert store.events.append(replay_with_new_timestamps) == 1, "event identity is stable across replay clocks"

    different = build_envelope(
        task_ref=TASK,
        run_ref=RUN,
        source="standalone_host",
        source_sequence=2,
        event_type="external_upload",
        payload={"n": 2},
        occurred_at="2026-01-01T00:00:01Z",
        event_id="evt-dup",
    )
    with pytest.raises(EventConflictError):
        store.events.append(different)


def test_event_log_out_of_order_and_gap_markers(store: SqlDurableStore) -> None:
    from hecate_durable.storage.eventlog import build_envelope

    for seq in (3, 1, 5):
        store.events.append(
            build_envelope(
                task_ref=TASK,
                run_ref=RUN,
                source="standalone_host",
                source_sequence=seq,
                event_type="external_upload",
                payload={"seq": seq},
                occurred_at="2026-01-01T00:00:00Z",
                event_id=f"evt-{seq}",
            )
        )
    page = store.read_events(RUN)
    # Reads order by sequence and mark the missing 2 and 4 explicitly.
    markers = [
        (e.gap.from_sequence, e.gap.to_sequence) if e.gap is not None else ("event", e.source_sequence)
        for e in page.events
    ]
    assert markers == [("event", 1), (2, 2), ("event", 3), (4, 4), ("event", 5)]
    assert page.next_cursor == 5
    tail = store.read_events(RUN, cursor=5)
    assert tail.events == [] and tail.next_cursor == 5


# --- cross-dialect concurrency ----------------------------------------------------------


def test_concurrent_claims_admit_exactly_one(store: SqlDurableStore) -> None:
    """Storage-level double dispatch: many workers race the claim; exactly
    one wins and every loser observes the claimed verdict."""

    store.record_intent_ex(_write_intent(), task_ref=TASK, run_ref=RUN, execution_id="e5")

    barrier = threading.Barrier(8)

    def claim(_worker: int) -> bool:
        barrier.wait()
        receipt, _token = store.claim_ex("run-1:reserve", holder=f"w{_worker}")
        return receipt.claimed

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(results) == 1
    losers = [r for r in results if not r]
    assert len(losers) == 7
