"""Accepted-decision turn recovery against real SQL decisions, leases and ledger.

Covers the agent-turn slice over the durable stack: the composed turn
commits before any business call, a worker crash after commit (or after
action-1) recovers through the same entry with zero model calls, a
completed action replays from the ledger without new side effects, and a
record substituted between the decision and action phases is rejected
before any dispatch. SQLite always; PostgreSQL when
``DURABLE_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from ast import literal_eval
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.accepted_action_step import execution_id_for_action
from hecate_runtime.accepted_decision import (
    DecisionConflictError,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.agent_turn import AgentTurn
from hecate_runtime.decision_step import DecisionProposal, DecisionResponder, DecisionStep
from hecate_runtime.eventstore import InMemoryEventStore
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor

RUN = RunRef("domain", "run-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
SQL_TASK = BackendRef(RefKind.TASK, "domain", "task-run-1")
URLS = [""] + ([os.environ["DURABLE_TEST_POSTGRES_URL"]] if os.environ.get("DURABLE_TEST_POSTGRES_URL") else [])


class StubCountingPort(StubRuntimePort):
    """Record business dispatches per action; no external systems."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append(args["action_key"])
        return {"business": args["action_key"]}


class StubTurnResponder(DecisionResponder):
    """Counts calls; returns the fixed three-action proposal."""

    def __init__(self) -> None:
        self.calls = 0

    async def respond(self, run_ref: RunRef, decision_step_id: str, input_refs: dict) -> DecisionProposal:
        self.calls += 1
        from hecate_runtime.accepted_decision import AcceptedAction

        return DecisionProposal(
            accepted_output={"text": "accepted"},
            actions=(
                AcceptedAction("action-1", "memory_add", {"action_key": "action-1"}),
                AcceptedAction("action-2", "memory_add", {"action_key": "action-2"}),
                AcceptedAction("action-3", "web_search", {"action_key": "action-3"}),
            ),
        )


@pytest.fixture(params=URLS, ids=["sqlite"] + (["postgres"] if len(URLS) > 1 else []))
def database(request, tmp_path):
    """Own a fresh SQLite file or isolated PostgreSQL schema per case."""
    with decision_database(tmp_path, request.param) as values:
        yield values


def _executor(backend, owner, port):
    hook = SqlActionLedgerHook(
        backend,
        task_ref=SQL_TASK,
        run_ref=SQL_RUN,
        holder=owner.holder,
        lease=LeaseHandle(owner.lease_key, owner.holder, owner.fence_token, ""),
    )
    return ToolCallExecutor(port=port, event_store=InMemoryEventStore(), action_hook=hook)


def _probe():
    return Path(__file__).with_name("_accepted_agent_turn_process.py")


def _run_probe(config: str, mode: str, effect: Path):
    return subprocess.run(  # noqa: S603 — fixed interpreter and local test helper; no shell
        [sys.executable, "-I", str(_probe()), config, mode, str(effect)],
        capture_output=True,
        text=True,
        timeout=25,
    )


async def test_full_turn_commits_then_dispatches_through_durable_stack(database) -> None:
    """One turn over the real stack: commit lands first, then all actions run."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    responder = StubTurnResponder()
    port = StubCountingPort()
    turn = AgentTurn(store, responder, _executor(backend, owner, port))

    task_before = backend.get_task_state(SQL_TASK)
    outcome = await turn.run(run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner)
    assert backend.get_task_state(SQL_TASK) == task_before
    assert outcome.resumed is False
    assert responder.calls == 1
    committed = await store.lookup(RUN, "step-1")
    assert committed is not None and committed.accepted_at
    assert [o.status for o in outcome.report.outcomes] == ["succeeded"] * 3
    assert port.calls == ["action-1", "action-2", "action-3"]
    assert outcome.record.content_digest == committed.content_digest


async def test_commit_crash_recovers_without_model_call(database, tmp_path) -> None:
    """Crash after commit, before any claim: recovery dispatches fresh, no model."""
    backend, _clock, url = database
    owner = prepare_decision_run(backend, RUN)
    config = json.dumps({"url": url, "ownership": asdict(owner)})
    effect = tmp_path / "turn-effects.txt"

    crashed = _run_probe(config, "commit-crash", effect)
    assert crashed.returncode == 9, crashed.stderr  # hard crash after the commit
    assert not effect.exists()  # zero business effects before the crash
    assert len(backend.list_run_actions(SQL_RUN)) == 0  # no ledger claims either
    committed = await SqlAcceptedDecisionStore(backend).lookup(RUN, "step-1")
    assert committed is not None and committed.accepted_at  # the commit itself is durable

    recovered = _run_probe(config, "recover-fresh", effect)
    assert recovered.returncode == 0, recovered.stderr
    restored = json.loads(recovered.stdout)
    assert restored["statuses"] == ["succeeded"] * 3
    assert restored["resumed"] is True
    original = await SqlAcceptedDecisionStore(backend).lookup(RUN, "step-1")
    assert original is not None
    assert restored["record"] == original.to_dict()
    for position, action in enumerate(original.actions):
        result = restored["outcomes"][position]["result"]
        assert result is not None
        # These fixtures contain only string values. literal_eval accepts
        # both the initial dict representation and the ledger's JSON form.
        assert literal_eval(result["content"]) == {"business": action.action_key}
        assert restored["outcomes"][position]["execution_id"] == execution_id_for_action(
            RUN, "step-1", action.action_key
        )
    assert effect.read_text(encoding="utf-8").splitlines() == [
        "effect-action-1",
        "effect-action-2",
        "effect-action-3",
    ]
    assert len(backend.list_run_actions(SQL_RUN)) == 3


async def test_crash_after_a1_replays_result_then_continues(database, tmp_path) -> None:
    """Crash after action-1: recovery replays it from the ledger, then A2/A3."""
    backend, _clock, url = database
    owner = prepare_decision_run(backend, RUN)
    config = json.dumps({"url": url, "ownership": asdict(owner)})
    effect = tmp_path / "turn-effects.txt"

    crashed = _run_probe(config, "a1-crash", effect)
    assert crashed.returncode == 9, crashed.stderr
    assert effect.read_text(encoding="utf-8").splitlines() == ["effect-action-1"]
    assert len(backend.list_run_actions(SQL_RUN)) == 1

    recovered = _run_probe(config, "recover-after-a1", effect)
    assert recovered.returncode == 0, recovered.stderr
    restored = json.loads(recovered.stdout)
    assert restored["statuses"] == ["succeeded"] * 3
    assert restored["resumed"] is True
    original = await SqlAcceptedDecisionStore(backend).lookup(RUN, "step-1")
    assert original is not None
    assert restored["record"] == original.to_dict()
    for position, action in enumerate(original.actions):
        result = restored["outcomes"][position]["result"]
        assert result is not None
        # These fixtures contain only string values. literal_eval accepts
        # both the initial dict representation and the ledger's JSON form.
        assert literal_eval(result["content"]) == {"business": action.action_key}
        assert restored["outcomes"][position]["execution_id"] == execution_id_for_action(
            RUN, "step-1", action.action_key
        )
    assert effect.read_text(encoding="utf-8").splitlines() == [
        "effect-action-1",
        "effect-action-2",
        "effect-action-3",
    ]
    assert len(backend.list_run_actions(SQL_RUN)) == 3


@pytest.mark.parametrize("commit_mode", ["final-answer-commit", "final-answer-commit-crash"])
async def test_final_answer_recovery_in_new_process_has_zero_model_and_tool_calls(
    database, tmp_path, commit_mode
) -> None:
    """Commit a final answer in one interpreter; another recovers it storage-only."""
    backend, _clock, url = database
    owner = prepare_decision_run(backend, RUN)
    config = json.dumps({"url": url, "ownership": asdict(owner)})
    effect = tmp_path / "final-answer-effects.txt"

    task_before = backend.get_task_state(SQL_TASK)
    committed = _run_probe(config, commit_mode, effect)
    assert committed.returncode == (9 if commit_mode.endswith("-crash") else 0), committed.stderr
    assert backend.get_task_state(SQL_TASK) == task_before
    assert not effect.exists()  # zero business effects
    assert len(backend.list_run_actions(SQL_RUN)) == 0  # zero ledger claims
    stored = await SqlAcceptedDecisionStore(backend).lookup(RUN, "step-1")
    assert stored is not None and stored.schema_version == 2
    if commit_mode == "final-answer-commit":
        written = json.loads(committed.stdout)
        assert written["is_final_answer"] is True
        assert written["statuses"] == []
        assert written["record"] == stored.to_dict()

    recovered = _run_probe(config, "recover-final-answer", effect)
    assert recovered.returncode == 0, recovered.stderr
    restored = json.loads(recovered.stdout)
    assert restored["resumed"] is True
    assert restored["is_final_answer"] is True
    assert restored["statuses"] == []
    assert restored["record"] == stored.to_dict()  # same text, digest, version
    assert backend.get_task_state(SQL_TASK) == task_before
    assert not effect.exists()
    assert len(backend.list_run_actions(SQL_RUN)) == 0


async def test_record_substituted_between_phases_conflicts_before_dispatch(database) -> None:
    """A valid-but-different record swapped in after the decision hit: conflict."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    await DecisionStep(store, StubTurnResponder()).execute(
        run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner
    )
    original = await store.lookup(RUN, "step-1")
    assert original is not None
    forged = replace(original, accepted_output={"text": "substituted"})
    forged = replace(forged, content_digest=canonical_decision_digest(forged))
    base_lookup = store.lookup
    calls = 0

    async def swapping_lookup(run_ref, decision_step_id):
        nonlocal calls
        calls += 1
        return forged if calls >= 2 else await base_lookup(run_ref, decision_step_id)

    store.lookup = swapping_lookup
    port = StubCountingPort()
    turn = AgentTurn(store, StubTurnResponder(), _executor(backend, owner, port))
    with pytest.raises(DecisionConflictError):
        await turn.run(run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner)
    assert port.calls == []
    assert len(backend.list_run_actions(SQL_RUN)) == 0  # no ledger claims either


async def test_turn_reports_recovery_over_replayed_prefix_in_process(database) -> None:
    """Same-entry recovery in-process: A1 replays, A2/A3 run, no new model call."""
    backend, _clock, _url = database
    store = SqlAcceptedDecisionStore(backend)
    owner = prepare_decision_run(backend, RUN)
    first_responder = StubTurnResponder()
    port = StubCountingPort()

    class StubCrashAfterA1Store(SqlAcceptedDecisionStore):
        """Raise out of the turn once action-1's outcome is durable."""

        async def lookup(self, run_ref, decision_step_id):
            if len(backend.list_run_actions(SQL_RUN)) >= 1:
                raise RuntimeError("simulated crash after action-1")
            return await super().lookup(run_ref, decision_step_id)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await AgentTurn(StubCrashAfterA1Store(backend), first_responder, _executor(backend, owner, port)).run(
            run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner
        )
    assert port.calls == ["action-1"]

    fresh_port = StubCountingPort()
    recovery_responder = StubTurnResponder()
    outcome = await AgentTurn(store, recovery_responder, _executor(backend, owner, fresh_port)).run(
        run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner
    )
    assert outcome.resumed is True
    assert recovery_responder.calls == 0  # recovery never re-asks the model
    assert [o.status for o in outcome.report.outcomes] == ["succeeded"] * 3
    assert fresh_port.calls == ["action-2", "action-3"]  # A1 replayed from the ledger


async def test_unknown_receipt_recovery_in_new_process_never_redoes_write(database, tmp_path) -> None:
    """A2 effect without receipt remains unknown across real interpreter restarts."""
    backend, _clock, url = database
    owner = prepare_decision_run(backend, RUN)
    config = json.dumps({"url": url, "ownership": asdict(owner)})
    effect = tmp_path / "unknown-turn-effects.txt"
    before = backend.get_task_state(SQL_TASK)

    first = _run_probe(config, "receipt-loss", effect)
    assert first.returncode == 0, first.stderr
    initial = json.loads(first.stdout)
    assert initial["outcomes"][-1]["stop_reason"] == "reconciliation_required"
    assert effect.read_text(encoding="utf-8").splitlines() == ["effect-action-1", "effect-action-2"]
    assert len(backend.list_run_actions(SQL_RUN)) == 2
    assert backend.get_task_state(SQL_TASK) == before

    recovered = _run_probe(config, "recover-unknown", effect)
    assert recovered.returncode == 0, recovered.stderr
    restored = json.loads(recovered.stdout)
    assert restored["resumed"] is True
    assert restored["record"] == initial["record"]
    assert restored["statuses"] == ["succeeded", "stopped"]
    assert restored["outcomes"][-1]["stop_reason"] == "needs_review"
    assert restored["unprocessed_action_keys"] == ["action-3"]
    assert json.loads(restored["outcomes"][0]["result"]["content"]) == {"business": "action-1"}
    assert effect.read_text(encoding="utf-8").splitlines() == ["effect-action-1", "effect-action-2"]
    assert len(backend.list_run_actions(SQL_RUN)) == 2
    assert backend.get_task_state(SQL_TASK) == before


async def test_lease_expiring_after_commit_cannot_dispatch_first_action(database) -> None:
    """The turn must recheck ownership between acceptance and its first action."""
    backend, clock, _url = database
    owner = prepare_decision_run(backend, RUN)

    class StubExpiringCommitStore(SqlAcceptedDecisionStore):
        """Expire the authoritative lease immediately after durable commit."""

        async def commit(self, record, ownership=None):
            committed = await super().commit(record, ownership)
            clock.advance(120)
            return committed

    store = StubExpiringCommitStore(backend)
    port = StubCountingPort()
    responder = StubTurnResponder()
    outcome = await AgentTurn(store, responder, _executor(backend, owner, port)).run(
        run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}, ownership=owner
    )
    assert responder.calls == 1
    assert outcome.record.accepted_at
    assert [o.status for o in outcome.report.outcomes] == ["stopped"]
    assert outcome.report.outcomes[0].stop_reason == "stale_ownership"
    assert outcome.report.unprocessed_action_keys == ("action-2", "action-3")
    assert port.calls == []
    assert backend.list_run_actions(SQL_RUN) == []


async def test_real_store_requires_owner_to_accept_decision(database) -> None:
    """A missing owner cannot commit or claim actions through a permissive stub."""
    from hecate_runtime.accepted_decision import StaleDecisionFenceError

    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    store = SqlAcceptedDecisionStore(backend)
    port = StubCountingPort()
    with pytest.raises(StaleDecisionFenceError):
        await AgentTurn(store, StubTurnResponder(), _executor(backend, owner, port)).run(
            run_ref=RUN, decision_step_id="step-1", input_refs={"config": "v1"}
        )
    assert await store.lookup(RUN, "step-1") is None
    assert port.calls == []
    assert backend.list_run_actions(SQL_RUN) == []
