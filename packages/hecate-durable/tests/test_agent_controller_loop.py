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
from hecate_runtime.accepted_decision import RunRef

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


async def test_crash_after_turn2_decision_recovers_in_new_process(database, tmp_path) -> None:
    """Hard crash after turn 2's commit: recovery replays and finishes."""
    backend, _clock, _url = database
    owner = prepare_decision_run(backend, RUN)
    effect = tmp_path / "loop-effects.txt"

    crashed = _run_probe(_config(backend, owner), "crash-after-decision", effect)
    assert crashed.returncode == 9, crashed.stderr
    assert _effects(effect) == ["effect-loop-a1"]  # turn 1's action ran; turn 2 never dispatched
    committed = await SqlAcceptedDecisionStore(backend).lookup(RUN, "agent-turn-000002")
    assert committed is not None and committed.accepted_at  # the crash point is durable

    recovered = _run_probe(_config(backend, owner), "recover", effect)
    assert recovered.returncode == 0, recovered.stderr
    result = json.loads(recovered.stdout)
    assert result["state"] == "final_answer" and result["final_text"] == "the loop answer"
    # Turn 2's decision resumed from storage; only turn 3 was freshly asked.
    assert result["asks"] == ["agent-turn-000003"]
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
