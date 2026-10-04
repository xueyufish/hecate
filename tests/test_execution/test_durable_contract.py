"""Parameterized durable-execution seam contract suite.

Runs the same semantic assertions against every registered implementation
(``DURABLE_IMPLEMENTATIONS`` in conftest). The InMemory stub is the founding
member; the PostgreSQL core (``durable-execution-core``) and the platform
adapter (``platform-task-control-api``) register into the same dict when
their changes land, inheriting this suite unchanged.
"""

from __future__ import annotations

import threading
import typing

import pytest

from hecate.contracts.execution.durable import (
    ActionIntent,
    ActionLedgerState,
    ActionOutcome,
    ActionOutcomeRecord,
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    IdempotencyConflictError,
    IdempotencyKey,
    IdempotencyScopeError,
    InvalidCommandTransitionError,
    InvalidTaskTransitionError,
    TaskLifecycleState,
    TaskStateRecord,
    canonical_request_digest,
    may_auto_replay,
)
from hecate.contracts.execution.events import (
    ActorKind,
    ActorRef,
    EventEnvelope,
    EventKind,
    EventSource,
    validate_governance_event,
)
from hecate.contracts.execution.references import (
    artifact_ref,
    run_ref,
    task_ref,
)
from hecate.contracts.execution.tools import ToolSideEffectClass
from hecate.execution.durable import ActionLedger, ControlCommandRecorder, DurableTaskStore
from tests.test_execution.conftest import DURABLE_IMPLEMENTATIONS

TASK = task_ref("platform.local", "task-1")
RUN = run_ref("platform.local", "run-1")


@pytest.fixture(params=sorted(DURABLE_IMPLEMENTATIONS), ids=lambda name: name)
def suite(request: pytest.FixtureRequest) -> tuple[DurableTaskStore, ControlCommandRecorder, ActionLedger]:
    return DURABLE_IMPLEMENTATIONS[request.param]()


@pytest.fixture
def store(suite: tuple[DurableTaskStore, ControlCommandRecorder, ActionLedger]) -> DurableTaskStore:
    return suite[0]


@pytest.fixture
def recorder(
    suite: tuple[DurableTaskStore, ControlCommandRecorder, ActionLedger],
) -> ControlCommandRecorder:
    return suite[1]


@pytest.fixture
def ledger(suite: tuple[DurableTaskStore, ControlCommandRecorder, ActionLedger]) -> ActionLedger:
    return suite[2]


def _intent(action_key: str, digest: str, cls: ToolSideEffectClass) -> ActionIntent:
    return ActionIntent(
        action_key=action_key,
        action_name="ticket_write",
        arguments_digest=digest,
        side_effect_class=cls,
    )


# --- R1: task lifecycle states ----------------------------------------------


def test_task_lifecycle_happy_path(store: DurableTaskStore) -> None:
    store.apply_task_state(TASK, TaskLifecycleState.QUEUED)
    store.apply_task_state(TASK, TaskLifecycleState.RUNNING)
    final = store.apply_task_state(TASK, TaskLifecycleState.SUCCEEDED)
    latest = store.get_task_state(TASK)
    assert latest is not None
    assert latest.lifecycle_state is TaskLifecycleState.SUCCEEDED
    assert latest.revision == final.revision


def test_unknown_lifecycle_state_rejected() -> None:
    payload = {
        "task_ref": TASK.to_dict(),
        "lifecycle_state": "paused",
        "revision": 1,
        "recorded_at": "2026-10-04T00:00:00Z",
    }
    with pytest.raises(ValueError, match="paused"):
        TaskStateRecord.from_dict(payload)


def test_terminal_state_never_rolls_back(store: DurableTaskStore) -> None:
    store.apply_task_state(TASK, TaskLifecycleState.QUEUED)
    store.apply_task_state(TASK, TaskLifecycleState.SUCCEEDED)
    with pytest.raises(InvalidTaskTransitionError):
        store.apply_task_state(TASK, TaskLifecycleState.RUNNING)


def test_reconciliation_required_not_silently_converged(store: DurableTaskStore) -> None:
    store.apply_task_state(TASK, TaskLifecycleState.QUEUED)
    store.apply_task_state(TASK, TaskLifecycleState.RECONCILIATION_REQUIRED)
    with pytest.raises(InvalidTaskTransitionError):
        store.apply_task_state(TASK, TaskLifecycleState.SUCCEEDED)
    converged = store.apply_task_state(TASK, TaskLifecycleState.SUCCEEDED, reconciled=True)
    assert converged.lifecycle_state is TaskLifecycleState.SUCCEEDED


def test_stale_revision_rejected(store: DurableTaskStore) -> None:
    store.apply_task_state(TASK, TaskLifecycleState.QUEUED)
    store.apply_task_state(TASK, TaskLifecycleState.RUNNING)
    current = store.get_task_state(TASK)
    assert current is not None
    stale = current.revision + 5
    with pytest.raises(ValueError, match="revision"):
        store.apply_task_state(TASK, TaskLifecycleState.FAILED, expected_revision=stale)


# --- R2: control command receipts -------------------------------------------


def _command(state: CommandState) -> ControlCommandRecord:
    return ControlCommandRecord(
        command_id="cmd-1",
        kind=ControlCommandKind.CANCEL,
        issuer="user-1",
        task_ref=TASK,
        issued_at="2026-10-04T00:00:00Z",
        state=state,
    )


def test_transport_success_is_not_applied(recorder: ControlCommandRecorder) -> None:
    stored = recorder.record(_command(CommandState.REQUESTED))
    # record() returned over a "successful transport"; the receipt still says requested.
    assert stored.state is CommandState.REQUESTED
    acked = recorder.transition("cmd-1", CommandState.ACKNOWLEDGED)
    assert acked.state is CommandState.ACKNOWLEDGED
    applied = recorder.transition("cmd-1", CommandState.APPLIED)
    assert applied.state is CommandState.APPLIED  # applied only from an executor receipt


def test_terminal_command_states_absorb(recorder: ControlCommandRecorder) -> None:
    recorder.record(_command(CommandState.REQUESTED))
    recorder.transition("cmd-1", CommandState.APPLIED)
    with pytest.raises(InvalidCommandTransitionError):
        recorder.transition("cmd-1", CommandState.REJECTED)


def test_expired_command_never_applies(recorder: ControlCommandRecorder) -> None:
    recorder.record(_command(CommandState.REQUESTED))
    expired = recorder.transition("cmd-1", CommandState.EXPIRED)
    assert expired.state is CommandState.EXPIRED
    with pytest.raises(InvalidCommandTransitionError):
        recorder.transition("cmd-1", CommandState.APPLIED)


def test_payload_requires_schema_ref() -> None:
    with pytest.raises(ValueError, match="payload_schema_ref"):
        ControlCommandRecord(
            command_id="cmd-2",
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer="user-1",
            task_ref=TASK,
            issued_at="2026-10-04T00:00:00Z",
            state=CommandState.REQUESTED,
            payload={"answer": "yes"},
        )


def test_command_record_roundtrip_preserves_unknown_fields() -> None:
    command = ControlCommandRecord(
        command_id="cmd-3",
        kind=ControlCommandKind.PROVIDE_INPUT,
        issuer="user-1",
        task_ref=TASK,
        issued_at="2026-10-04T00:00:00Z",
        state=CommandState.REQUESTED,
        payload={"answer": "yes"},
        payload_schema_ref="https://example.test/provide-input/v1",
        expected_revision=2,
    )
    data = command.to_dict()
    data["vendor_note"] = "keep me"
    restored = ControlCommandRecord.from_dict(data)
    assert restored.extra == {"vendor_note": "keep me"}
    assert restored.expected_revision == 2


# --- R3: idempotent submission ----------------------------------------------


def _key(digest: str) -> IdempotencyKey:
    return IdempotencyKey(key="idem-1", subject="user-1", workspace="ws-1", request_digest=digest)


def test_same_key_same_digest_idempotent(store: DurableTaskStore) -> None:
    digest = canonical_request_digest({"prompt": "hello", "mode": "fast"})
    first = store.record_submission(_key(digest), TASK, RUN)
    second = store.record_submission(_key(digest), TASK, RUN)
    assert first is second or first.to_dict() == second.to_dict()


def test_same_key_different_digest_conflicts(store: DurableTaskStore) -> None:
    first = store.record_submission(_key("d" * 64), TASK, RUN)
    with pytest.raises(IdempotencyConflictError) as exc_info:
        store.record_submission(_key("e" * 64), TASK, RUN)
    assert exc_info.value.registered_digest == first.key.request_digest


def test_key_scope_rejects_context_mismatch() -> None:
    key = _key("d" * 64)
    with pytest.raises(IdempotencyScopeError):
        key.assert_matches_context(subject="user-2", workspace="ws-1")
    key.assert_matches_context(subject="user-1", workspace="ws-1")


def test_canonical_digest_is_key_order_independent() -> None:
    left = canonical_request_digest({"a": 1, "b": {"x": 1, "y": 2}})
    right = canonical_request_digest({"b": {"y": 2, "x": 1}, "a": 1})
    assert left == right
    assert canonical_request_digest({"a": 1}) != canonical_request_digest({"a": 2})


# --- R4: governance event profile -------------------------------------------


def _envelope(actor: ActorRef | None, source: EventSource | None) -> EventEnvelope:
    return EventEnvelope(
        contract_version="0.1",
        kind=EventKind.EVENT,
        event_id="evt-1",
        task_ref=TASK,
        run_ref=RUN,
        source_sequence=0,
        occurred_at="2026-10-04T00:00:00Z",
        received_at="2026-10-04T00:00:00Z",
        payload_schema_ref="https://example.test/governance/v1",
        actor=actor,
        source=source,
    )


def test_governance_event_requires_actor_and_source() -> None:
    actor = ActorRef(kind=ActorKind.AGENT_PRINCIPAL, id="principal-1")
    source = EventSource.PLATFORM
    with pytest.raises(ValueError, match="actor"):
        validate_governance_event(_envelope(None, source))
    with pytest.raises(ValueError, match="source"):
        validate_governance_event(_envelope(actor, None))
    validate_governance_event(_envelope(actor, source))


def test_envelope_actor_source_roundtrip() -> None:
    envelope = _envelope(
        ActorRef(kind=ActorKind.HUMAN, id="user-1", on_behalf_of="user-2"),
        EventSource.STANDALONE_HOST,
    )
    data = envelope.to_dict()
    assert data["source"] == "standalone_host"
    restored = EventEnvelope.from_dict(data)
    assert restored.actor == envelope.actor
    assert restored.source is EventSource.STANDALONE_HOST


def test_envelope_rejects_unknown_source_value() -> None:
    data = _envelope(None, None).to_dict()
    data["source"] = "mystery"
    with pytest.raises(ValueError):
        EventEnvelope.from_dict(data)


# --- R5: action ledger ------------------------------------------------------


def test_recovery_without_intent_is_never_started(ledger: ActionLedger) -> None:
    recovery = ledger.recovery("missing-action")
    assert recovery.state is ActionLedgerState.NEVER_STARTED
    assert recovery.intent is None
    assert recovery.pending_reconciliation is False


def test_claim_requires_recorded_intent(ledger: ActionLedger) -> None:
    # Intent-before-dispatch is enforced at the seam: claiming without a
    # persisted intent is a usage error, never a silent fresh start.
    with pytest.raises(Exception, match="intent"):
        ledger.claim("missing-action")


def test_claim_winner_and_loser(ledger: ActionLedger) -> None:
    ledger.record_intent(_intent("a1", "d" * 64, ToolSideEffectClass.READONLY))
    winner = ledger.claim("a1")
    assert winner.claimed is True
    loser = ledger.claim("a1")
    assert loser.claimed is False
    assert loser.recovery.state is ActionLedgerState.CLAIMED


def test_concurrent_claim_single_winner(ledger: ActionLedger) -> None:
    ledger.record_intent(_intent("a2", "d" * 64, ToolSideEffectClass.READONLY))
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def claimer() -> None:
        barrier.wait()
        results.append(ledger.claim("a2").claimed)

    threads = [threading.Thread(target=claimer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(results) == 1


def test_same_action_key_different_digest_conflicts(ledger: ActionLedger) -> None:
    ledger.record_intent(_intent("a3", "d" * 64, ToolSideEffectClass.READONLY))
    with pytest.raises(IdempotencyConflictError):
        ledger.record_intent(_intent("a3", "e" * 64, ToolSideEffectClass.READONLY))


def test_unknown_outcome_is_pending_not_placeholder(ledger: ActionLedger) -> None:
    ledger.record_intent(_intent("a4", "d" * 64, ToolSideEffectClass.EXTERNAL_SIDE_EFFECT))
    ledger.claim("a4")
    ledger.record_outcome(
        ActionOutcomeRecord(
            action_key="a4",
            outcome=ActionOutcome.UNKNOWN,
            result_digest="sha256:partial",
        )
    )
    recovery = ledger.recovery("a4")
    assert recovery.state is ActionLedgerState.OUTCOME_UNKNOWN
    assert recovery.pending_reconciliation is True
    assert recovery.last_outcome is not None
    assert recovery.last_outcome.result_digest == "sha256:partial"
    assert ledger.claim("a4").claimed is False  # never auto-replayed


def test_succeeded_recovery_returns_real_reference(ledger: ActionLedger) -> None:
    result = artifact_ref("platform.local", "art-1")
    ledger.record_intent(_intent("a5", "d" * 64, ToolSideEffectClass.NON_IDEMPOTENT_WRITE))
    ledger.claim("a5")
    ledger.record_outcome(ActionOutcomeRecord(action_key="a5", outcome=ActionOutcome.SUCCEEDED, result_ref=result))
    recovery = ledger.recovery("a5")
    assert recovery.last_outcome is not None
    assert recovery.last_outcome.result_ref == result
    assert recovery.pending_reconciliation is False
    assert ledger.claim("a5").claimed is False  # a succeeded action never re-executes


def test_failed_outcome_replay_follows_side_effect_class(ledger: ActionLedger) -> None:
    ledger.record_intent(_intent("a6", "d" * 64, ToolSideEffectClass.IDEMPOTENT_WRITE))
    ledger.claim("a6")
    ledger.record_outcome(ActionOutcomeRecord(action_key="a6", outcome=ActionOutcome.FAILED))
    assert ledger.claim("a6").claimed is True  # idempotent write may replay

    ledger.record_intent(_intent("a7", "d" * 64, ToolSideEffectClass.NON_IDEMPOTENT_WRITE))
    ledger.claim("a7")
    ledger.record_outcome(ActionOutcomeRecord(action_key="a7", outcome=ActionOutcome.FAILED))
    loser = ledger.claim("a7")
    assert loser.claimed is False
    assert loser.recovery.pending_reconciliation is True


@pytest.mark.parametrize(
    ("state", "effect_class", "expected"),
    [
        (ActionLedgerState.NEVER_STARTED, ToolSideEffectClass.NON_IDEMPOTENT_WRITE, True),
        (ActionLedgerState.CLAIMED, ToolSideEffectClass.READONLY, True),
        (ActionLedgerState.CLAIMED, ToolSideEffectClass.IDEMPOTENT_WRITE, True),
        (ActionLedgerState.CLAIMED, ToolSideEffectClass.NON_IDEMPOTENT_WRITE, False),
        (ActionLedgerState.CLAIMED, ToolSideEffectClass.EXTERNAL_SIDE_EFFECT, False),
        (ActionLedgerState.CLAIMED, ToolSideEffectClass.UNKNOWN, False),
        (ActionLedgerState.OUTCOME_UNKNOWN, ToolSideEffectClass.READONLY, False),
        (ActionLedgerState.STORE_UNAVAILABLE, ToolSideEffectClass.READONLY, True),
        (ActionLedgerState.STORE_UNAVAILABLE, ToolSideEffectClass.IDEMPOTENT_WRITE, False),
        (ActionLedgerState.STORE_UNAVAILABLE, ToolSideEffectClass.NON_IDEMPOTENT_WRITE, False),
    ],
)
def test_auto_replay_policy_table(state: ActionLedgerState, effect_class: ToolSideEffectClass, expected: bool) -> None:
    assert may_auto_replay(state, effect_class) is expected


# --- R6: seam shape ---------------------------------------------------------


def test_seam_signatures_carry_only_contract_types() -> None:
    def allowed(module: str) -> bool:
        # typing machinery modules differ across Python versions (e.g. an
        # ``int | None`` hint resolves to ``typing.Union`` on 3.14 but
        # ``types.UnionType`` on 3.12) and are never leak sources; the check
        # exists to catch ORM/web/engine/vendor classes in seam signatures.
        return (
            module == "builtins"
            or module == "typing"
            or module == "types"
            or module == "collections.abc"
            or module.startswith("hecate.contracts")
        )

    for abc in (DurableTaskStore, ControlCommandRecorder, ActionLedger):
        for name, method in abc.__dict__.items():
            if not getattr(method, "__isabstractmethod__", False):
                continue
            hints = typing.get_type_hints(method)
            for annotation in hints.values():
                origin = typing.get_origin(annotation)
                args = typing.get_args(annotation)
                for candidate in (annotation, origin, *args):
                    if isinstance(candidate, type) and not allowed(candidate.__module__):
                        raise AssertionError(f"{abc.__name__}.{name} leaks {candidate.__module__}.{candidate.__name__}")


def test_contract_suite_has_registered_implementations() -> None:
    assert DURABLE_IMPLEMENTATIONS, "at least one implementation must be registered"
    assert len(set(DURABLE_IMPLEMENTATIONS)) == len(DURABLE_IMPLEMENTATIONS)
    # The founding member is the InMemory stub; production implementations
    # register alongside it, never replacing it.
    assert "inmemory-stub" in DURABLE_IMPLEMENTATIONS
