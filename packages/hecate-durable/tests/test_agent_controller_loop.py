"""Controller-loop recovery against real SQL decisions, leases and ledger.

Covers the multi-turn loop slice over the durable stack: the loop advances
on real tool feedback across processes, hard crashes at the three
boundaries (decision committed, action completed, feedback projected into
the next turn's rebuild) recover in a fresh interpreter with zero re-asked
decisions and zero re-executed effects, an approval wait parks through the
signal path and its wake continues the same chain, and an unknown action
outcome stops classified without blind retries. SQLite always; PostgreSQL
when ``DURABLE_TEST_POSTGRES_URL`` is set.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
from hecate_runtime.accepted_decision import RunRef, harness_input_snapshot

RUN = RunRef("domain", "run-loop-1")
SQL_RUN = BackendRef(RefKind.RUN, RUN.issuer_domain, RUN.run_id)
SQL_TASK = BackendRef(RefKind.TASK, RUN.issuer_domain, "task-run-loop-1")
URLS = [""] + ([os.environ["DURABLE_TEST_POSTGRES_URL"]] if os.environ.get("DURABLE_TEST_POSTGRES_URL") else [])


@pytest.fixture(params=URLS, ids=["sqlite"] + (["postgres"] if len(URLS) > 1 else []))
def database(request, tmp_path):
    """Own a fresh SQLite file or isolated PostgreSQL schema per case."""
    with decision_database(tmp_path, request.param) as values:
        yield values


def _probe() -> Path:
    return Path(__file__).with_name("_agent_controller_loop_process.py")


def _run_probe(config: str, mode: str, effect: Path):
    return subprocess.run(  # noqa: S603 — fixed interpreter and local test helper; no shell
        [sys.executable, "-I", str(_probe()), config, mode, str(effect)],
        capture_output=True,
        text=True,
        timeout=25,
    )


def _config(backend, owner) -> str:
    return json.dumps({"url": backend.engine.url.render_as_string(hide_password=False), "ownership": asdict(owner)})


def _effects(effect: Path) -> list[str]:
    return effect.read_text(encoding="utf-8").splitlines() if effect.exists() else []


async def test_full_loop_advances_on_real_feedback(database, tmp_path) -> None:
    """Three turns, two governed effects, one final answer — one entry call."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "loop-effects.txt"

    done = _run_probe(_config(backend, owner), "full", effect)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["state"] == "final_answer"
    assert result["final_text"] == "the loop answer"
    assert result["turns"] == 3
    assert result["asks"] == ["agent-turn-000001", "agent-turn-000002", "agent-turn-000003"]
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]
    assert len(backend.list_run_actions(SQL_RUN)) == 2


async def test_no_progress_stop_recovers_after_process_exit_before_terminal(database, tmp_path) -> None:
    """Recover the identical stop from SQL without another model ask or read."""
    from hecate_durable.contracts.durable import TaskLifecycleState

    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "observations.txt"
    config = json.loads(_config(backend, owner))
    config["no_progress"] = {"limit": 2, "observations": ["web_search"]}
    serialized = json.dumps(config)
    crashed = _run_probe(serialized, "no-progress-crash", effect)
    assert crashed.returncode == 9, crashed.stderr
    first = json.loads(crashed.stdout)
    assert first["reason"] == "no_progress_limit"
    assert len(first["asks"]) == 2
    assert backend.get_task_state(SQL_TASK).lifecycle_state is TaskLifecycleState.RUNNING
    recovered = _run_probe(serialized, "no-progress-recover", effect)
    assert recovered.returncode == 0, recovered.stderr
    second = json.loads(recovered.stdout)
    assert second["asks"] == []
    assert second["no_progress"] == first["no_progress"]
    assert _effects(effect) == ["effect-same", "effect-same"]
    state = backend.get_task_state(SQL_TASK)
    assert state.lifecycle_state is TaskLifecycleState.FAILED
    assert state.extra["result"]["no_progress"] == first["no_progress"]
    events = backend.read_events(SQL_RUN, limit=100).events
    assert sum(event.payload.get("event_type") == "run_terminal" for event in events) == 1


@pytest.mark.parametrize(
    "harness_declaration",
    [None, {"version": 1, "sections": [{"type": "memory", "content": {"facts": ["frozen policy"]}}]}],
    ids=["legacy", "harness"],
)
@pytest.mark.parametrize("snapshot_pipeline", [False, True], ids=["scripted", "snapshot"])
async def test_crash_after_turn2_decision_recovers_in_new_process(
    database, tmp_path, harness_declaration, snapshot_pipeline
) -> None:
    """Hard crash after turn 2's commit: recovery replays and finishes."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "loop-effects.txt"

    config = json.loads(_config(backend, owner))
    config["snapshots"] = snapshot_pipeline
    if harness_declaration is not None:
        config["harness"] = harness_declaration
    serialized = json.dumps(config)
    crashed = _run_probe(serialized, "crash-after-decision", effect)
    assert crashed.returncode == 9, crashed.stderr
    assert _effects(effect) == ["effect-loop-a1"]  # turn 1's action ran; turn 2 never dispatched
    committed = await SqlAcceptedDecisionStore(backend).lookup(RUN, "agent-turn-000002")
    assert committed is not None and committed.accepted_at  # the crash point is durable
    if harness_declaration is not None:
        assert committed.input_refs["harness_version"] == harness_declaration["version"]
        assert committed.input_refs["harness_digest"]
    original_snapshot = harness_input_snapshot(committed)
    assert (original_snapshot is not None) is snapshot_pipeline

    recovered = _run_probe(serialized, "recover", effect)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["state"] == "final_answer" and result["final_text"] == "the loop answer"
    # Turn 2's decision resumed from storage; only turn 3 was freshly asked.
    assert result["asks"] == ["agent-turn-000003"]
    if snapshot_pipeline:
        assert result["pipeline_preparations"] == 1
        restored = await SqlAcceptedDecisionStore(backend).lookup(RUN, "agent-turn-000002")
        assert restored == committed
        assert harness_input_snapshot(restored) == original_snapshot
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]  # zero duplicate effects
    assert len(backend.list_run_actions(SQL_RUN)) == 2


async def test_crash_after_turn1_action_recovers_mid_batch(database, tmp_path) -> None:
    """Hard crash after turn 1's action effect+outcome: replay continues."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "loop-effects.txt"

    crashed = _run_probe(_config(backend, owner), "crash-after-action", effect)
    assert crashed.returncode == 9, crashed.stderr
    assert _effects(effect) == ["effect-loop-a1"]
    assert len(backend.list_run_actions(SQL_RUN)) == 1

    recovered = _run_probe(_config(backend, owner), "recover", effect)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["state"] == "final_answer"
    assert result["asks"] == ["agent-turn-000002", "agent-turn-000003"]  # turn 1 resumed, not re-asked
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]
    assert len(backend.list_run_actions(SQL_RUN)) == 2


async def test_approval_wait_parks_and_wake_continues_same_chain(database, tmp_path) -> None:
    """The wait signal escapes the loop; the wake continues without rework."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "loop-effects.txt"

    parked = _run_probe(_config(backend, owner), "wait", effect)
    assert parked.returncode == 0, parked.stderr
    result = json.loads(parked.stdout)
    assert result["parked"] is True
    assert result["asks"] == ["agent-turn-000001", "agent-turn-000002"]
    assert _effects(effect) == ["effect-loop-a1"]
    assert len(backend.list_run_actions(SQL_RUN)) == 1  # no claim for the parked action

    woke = _run_probe(_config(backend, owner), "wake", effect)
    assert woke.returncode == 0, woke.stderr
    result = json.loads(woke.stdout)
    assert result["state"] == "final_answer" and result["final_text"] == "the loop answer"
    assert result["asks"] == ["agent-turn-000003"]  # turns 1-2 replayed from storage
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]  # the parked action dispatched once
    assert len(backend.list_run_actions(SQL_RUN)) == 2


async def test_unknown_outcome_stops_classified_and_stays_stopped(database, tmp_path) -> None:
    """A lost receipt after the effect stops the loop; recovery never retries."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "loop-effects.txt"

    first = _run_probe(_config(backend, owner), "receipt-loss", effect)
    assert first.returncode == 0, first.stderr
    result = json.loads(first.stdout)
    assert result["state"] == "stopped"
    assert result["reason"] in {"needs_review", "reconciliation_required"}
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]

    recovered = _run_probe(_config(backend, owner), "recover-unknown", effect)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["state"] == "stopped" and result["reason"] == "needs_review"
    assert result["asks"] == []  # committed decisions resume without model calls
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]  # unknown outcome never re-executes
    assert len(backend.list_run_actions(SQL_RUN)) == 2


async def test_crash_after_feedback_before_next_decision_recovers(database, tmp_path) -> None:
    """Rebuilding feedback cannot itself advance or duplicate business work."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "feedback-effects.txt"
    crashed = _run_probe(_config(backend, owner), "crash-after-feedback", effect)
    assert crashed.returncode == 9, crashed.stderr
    assert await SqlAcceptedDecisionStore(backend).lookup(RUN, "agent-turn-000002") is None
    assert _effects(effect) == ["effect-loop-a1"]
    recovered = _run_probe(_config(backend, owner), "recover", effect)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["state"] == "final_answer"
    assert result["asks"] == ["agent-turn-000002", "agent-turn-000003"]
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]


def _accept_input(backend, wake_id: str, content: dict) -> None:
    """The single wake writer: park, then accept the revision atomically.

    Mirrors the host sequence (claim → wait → wake) and fixes the causal
    boundary from the committed decision chain, exactly like the platform
    and runner wake paths do.
    """
    from hecate_durable.contracts.durable import TaskLifecycleState

    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING)
    backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.WAITING_INPUT,
        extra_update={"wait": {"wait_token": wake_id, "consumed": False}},
    )
    backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.QUEUED,
        extra_update={"wait": {"wait_token": wake_id, "consumed": True}},
        input_revision={
            "source": "provide_input",
            "source_ref": wake_id,
            "content": content,
            "wait_token": wake_id,
            "effective_turn": backend.count_accepted_decisions(SQL_RUN) + 1,
        },
    )


@pytest.mark.parametrize("snapshot_pipeline", [False, True], ids=["scripted", "snapshot"])
async def test_two_input_revisions_recover_in_true_causal_order(database, tmp_path, snapshot_pipeline) -> None:
    """Two waits, two wakes, three processes — order and effects preserved."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "input-effects.txt"
    config = json.loads(_config(backend, owner))
    config["snapshots"] = snapshot_pipeline
    serialized = json.dumps(config)

    # Process 1: turn 1 commits, then the run dies parked awaiting input.
    parked = _run_probe(serialized, "input-park-1", effect)
    assert parked.returncode == 9, parked.stderr
    assert _effects(effect) == ["effect-loop-a1"]
    assert await SqlAcceptedDecisionStore(backend).lookup(RUN, "agent-turn-000002") is None

    # Wake 1: the revision's boundary is one past the committed chain (turn 2).
    _accept_input(backend, "wake-1", {"region": "EU"})
    revisions = backend.list_input_revisions(SQL_TASK)
    assert [row["effective_turn"] for row in revisions] == [2]

    # Process 2: replays turn 1, decides turn 2 with the revision at the
    # causal tail, executes its action, dies parked again before turn 3.
    resumed = _run_probe(serialized, "input-resume", effect)
    assert resumed.returncode == 9, resumed.stderr
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]

    # Wake 2: the second revision lands after the now two-turn chain (turn 3).
    _accept_input(backend, "wake-2", {"tier": "gold"})
    revisions = backend.list_input_revisions(SQL_TASK)
    assert [row["effective_turn"] for row in revisions] == [2, 3]
    assert [row["content"] for row in revisions] == [{"region": "EU"}, {"tier": "gold"}]

    # Process 3: turns 1-2 replay with zero model calls and zero effects;
    # turn 3 answers with BOTH input values in their true causal order.
    finished = _run_probe(serialized, "input-resume-final", effect)
    assert finished.returncode == 0, finished.stderr
    result = json.loads(finished.stdout)
    assert result["state"] == "final_answer"
    assert result["final_text"] == "the loop answer region=EU tier=gold"
    assert result["asks"] == ["agent-turn-000003"]
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]
    assert len(backend.list_run_actions(SQL_RUN)) == 2
    if snapshot_pipeline:
        assert result["pipeline_preparations"] == 1
        store = SqlAcceptedDecisionStore(backend)
        for turn in range(1, 4):
            decision = await store.lookup(RUN, f"agent-turn-{turn:06d}")
            snapshot = harness_input_snapshot(decision)
            assert snapshot is not None
            inputs = [
                json.loads(message["content"])["provided_input"]
                for message in snapshot["messages"]
                if message.get("role") == "user" and "provided_input" in message["content"]
            ]
            assert inputs == [{"region": "EU"}, {"tier": "gold"}][: turn - 1]


async def test_duplicate_wake_command_keeps_one_revision(database, tmp_path) -> None:
    """Replaying the same wake command is idempotent at every boundary."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "dup-effects.txt"
    parked = _run_probe(_config(backend, owner), "input-park-1", effect)
    assert parked.returncode == 9, parked.stderr

    _accept_input(backend, "wake-dup", {"region": "EU"})
    from hecate_durable.contracts.durable import TaskLifecycleState

    # The same command replays after the wait is consumed: same revision,
    # no second row, no requeue loop (the transition itself stays valid).
    replay = backend.apply_task_state(
        SQL_TASK,
        TaskLifecycleState.QUEUED,
        extra_update={"wait": {"wait_token": "wake-dup", "consumed": True}},
        input_revision={
            "source": "provide_input",
            "source_ref": "wake-dup",
            "content": {"region": "EU"},
            "wait_token": "wake-dup",
            "effective_turn": 2,
        },
    )
    assert replay is not None
    revisions = backend.list_input_revisions(SQL_TASK)
    assert len(revisions) == 1 and revisions[0]["source_ref"] == "wake-dup"


def _acceptance_config(backend, owner, acceptance: dict) -> str:
    payload = json.loads(_config(backend, owner))
    payload["acceptance"] = acceptance
    return json.dumps(payload)


_DECLARED_STANDARD = {
    "version": 4,
    "required_checks": [
        {"kind": "content_contains", "value": "the loop answer"},
    ],
}


async def test_acceptance_recovers_across_the_crash_boundary(database, tmp_path) -> None:
    """Crash after the final decision commit: recovery re-derives acceptance.

    The final decision is durable, but the process dies before any delivery
    evaluation or terminal-state commit. A fresh process replays the whole
    chain with ZERO model calls and ZERO re-executed effects, then evaluates
    the delivery under the same frozen standard — the acceptance record
    binds the final decision's identity and digest.
    """
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "acceptance-effects.txt"

    crashed = _run_probe(_acceptance_config(backend, owner, _DECLARED_STANDARD), "acceptance-crash", effect)
    assert crashed.returncode == 9, crashed.stderr
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]
    final_decision = await SqlAcceptedDecisionStore(backend).lookup(RUN, "agent-turn-000003")
    assert final_decision is not None and final_decision.accepted_at

    recovered = _run_probe(_acceptance_config(backend, owner, _DECLARED_STANDARD), "acceptance-recover", effect)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["state"] == "final_answer"
    assert result["asks"] == []  # nothing re-asked; the chain replayed
    assert _effects(effect) == ["effect-loop-a1", "effect-loop-a2"]  # nothing redone
    record = result["acceptance"]
    assert record["status"] == "passed"
    assert record["version"] == 4
    assert record["final_decision"] == {
        "decision_step_id": "agent-turn-000003",
        "content_digest": final_decision.content_digest,
    }


async def test_same_version_standard_rewrite_conflicts_instead_of_reevaluating(database, tmp_path) -> None:
    """A committed chain never re-evaluates under a rewritten standard."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "tamper-effects.txt"

    crashed = _run_probe(_acceptance_config(backend, owner, _DECLARED_STANDARD), "acceptance-crash", effect)
    assert crashed.returncode == 9, crashed.stderr

    tampered = {
        "version": 4,  # same version, different check parameter
        "required_checks": [{"kind": "content_contains", "value": "a different answer"}],
    }
    recovered = _run_probe(_acceptance_config(backend, owner, tampered), "acceptance-tampered", effect)
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {"conflict": True}


# -- bounded-continuation trigger facts (durable consume-once boundary) ------


def _continuation_attempt(
    index: int = 1,
    candidate: str = "agent-turn-000002",
    digest: str = "a" * 64,
) -> dict:
    return {
        "attempt": index,
        "candidate_decision_step_id": candidate,
        "candidate_digest": digest,
        "effective_turn": 3,
        "standard_version": 2,
        "standard_sha256": "b" * 64,
        "status": "acceptance_failed",
        "policy_sha256": "c" * 64,
        "feedback": {"attempt": index, "max_continuations": 2, "failed_checks": []},
    }


async def test_continuation_trigger_persists_once_with_event(database) -> None:
    """The trigger lands with its event; a replayed candidate is a no-op."""
    from hecate_durable.contracts.durable import TaskLifecycleState

    backend, _clock, _url = database
    prepare_decision_run(backend, RUN)
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING)
    before = backend.get_task_state(SQL_TASK)
    assert before.lifecycle_state is TaskLifecycleState.RUNNING

    record = backend.record_continuation_trigger(
        SQL_TASK, _continuation_attempt(), policy_sha256="c" * 64, max_continuations=2
    )
    assert record.revision == before.revision + 1
    block = record.extra["continuation"]
    assert block["policy_sha256"] == "c" * 64 and block["max_continuations"] == 2
    assert [item["attempt"] for item in block["attempts"]] == [1]
    assert record.lifecycle_state is TaskLifecycleState.RUNNING  # never a state move
    events = backend.read_events(SQL_RUN, limit=100).events
    continuations = [event for event in events if event.payload.get("event_type") == "task_continuation"]
    assert len(continuations) == 1
    assert continuations[0].payload["candidate_decision_step_id"] == "agent-turn-000002"

    # A dispatch that died before its next model call redelivers onto the
    # same candidate: consume-once means the stored fact wins, no second
    # attempt, no revision bump, no duplicate event.
    replay = backend.record_continuation_trigger(
        SQL_TASK, _continuation_attempt(), policy_sha256="c" * 64, max_continuations=2
    )
    assert replay.revision == record.revision
    assert len(replay.extra["continuation"]["attempts"]) == 1
    events = backend.read_events(SQL_RUN, limit=100).events
    assert sum(event.payload.get("event_type") == "task_continuation" for event in events) == 1

    # A second, distinct candidate consumes the next position normally.
    second = backend.record_continuation_trigger(
        SQL_TASK,
        _continuation_attempt(index=2, candidate="agent-turn-000004", digest="d" * 64),
        policy_sha256="c" * 64,
        max_continuations=2,
    )
    assert [item["attempt"] for item in second.extra["continuation"]["attempts"]] == [1, 2]
    replay_at_bound = backend.record_continuation_trigger(
        SQL_TASK,
        _continuation_attempt(index=2, candidate="agent-turn-000004", digest="d" * 64),
        policy_sha256="c" * 64,
        max_continuations=2,
    )
    assert replay_at_bound.revision == second.revision


async def test_continuation_trigger_refuses_mismatched_facts(database) -> None:
    """Corruption, reordering, a reached bound, moved tasks and stale CAS all refuse."""
    from hecate_durable.contracts.durable import TaskLifecycleState

    backend, _clock, _url = database
    prepare_decision_run(backend, RUN)
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.RUNNING)
    # A stale revision CAS refuses before anything is read.
    with pytest.raises(ValueError, match="stale revision"):
        backend.record_continuation_trigger(
            SQL_TASK,
            _continuation_attempt(),
            policy_sha256="c" * 64,
            max_continuations=2,
            expected_revision=99,
        )
    backend.record_continuation_trigger(SQL_TASK, _continuation_attempt(), policy_sha256="c" * 64, max_continuations=2)

    # Same candidate, different facts: corruption, not a revision.
    with pytest.raises(ValueError, match="different facts"):
        backend.record_continuation_trigger(
            SQL_TASK,
            _continuation_attempt(digest="e" * 64),
            policy_sha256="c" * 64,
            max_continuations=2,
        )
    # A skipped or reused position.
    with pytest.raises(ValueError, match="next recorded position"):
        backend.record_continuation_trigger(
            SQL_TASK,
            _continuation_attempt(index=3, candidate="agent-turn-000006", digest="f" * 64),
            policy_sha256="c" * 64,
            max_continuations=2,
        )
    # A policy rewritten under a new digest cannot extend the chain.
    with pytest.raises(ValueError, match="policy changed"):
        backend.record_continuation_trigger(
            SQL_TASK,
            _continuation_attempt(index=2, candidate="agent-turn-000004", digest="d" * 64),
            policy_sha256="9" * 64,
            max_continuations=2,
        )
    # The persisted bound is enforced store-side.
    backend.record_continuation_trigger(
        SQL_TASK,
        _continuation_attempt(index=2, candidate="agent-turn-000004", digest="d" * 64),
        policy_sha256="c" * 64,
        max_continuations=2,
    )
    with pytest.raises(ValueError, match="bound is already reached"):
        backend.record_continuation_trigger(
            SQL_TASK,
            _continuation_attempt(index=3, candidate="agent-turn-000006", digest="f" * 64),
            policy_sha256="c" * 64,
            max_continuations=2,
        )
    # A terminal task never reactivates for continuation.
    backend.apply_task_state(SQL_TASK, TaskLifecycleState.FAILED)
    with pytest.raises(ValueError, match="running task"):
        backend.record_continuation_trigger(
            SQL_TASK,
            _continuation_attempt(),
            policy_sha256="c" * 64,
            max_continuations=2,
        )


@pytest.mark.parametrize(
    "window,remaining_asks",
    [
        ("candidate", [3, 4]),
        ("trigger", [3, 4]),
        ("next-decision", [4]),
        ("partial-action", [4]),
    ],
)
async def test_continuation_recovers_in_new_interpreter(database, tmp_path, window, remaining_asks) -> None:
    """Real process death preserves attempts, model decisions and completed effects."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "continuation-effects.txt"
    probe = Path(__file__).with_name("_controller_continuation_process.py")
    config = _config(backend, owner)

    def run(mode):
        return subprocess.run(  # noqa: S603 - fixed interpreter and local probe, no shell
            [sys.executable, "-I", str(probe), config, mode, str(effect)],
            capture_output=True,
            text=True,
            timeout=25,
        )

    crashed = run(window)
    assert crashed.returncode == 9, crashed.stderr
    recovered = run("recover")
    assert recovered.returncode == 0, recovered.stderr
    assert json.loads(recovered.stdout) == {"asks": remaining_asks, "text": "verified"}
    assert _effects(effect) == ["before", "correction-a", "correction-b"]
    state = backend.get_task_state(SQL_TASK)
    assert len(state.extra["continuation"]["attempts"]) == 1
    events = backend.read_events(SQL_RUN, limit=100).events
    assert sum(event.payload.get("event_type") == "task_continuation" for event in events) == 1
