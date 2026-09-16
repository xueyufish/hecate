"""Tests for the optimization loop — driven synchronously against the
request session (the background ``_execute`` needs ``async_session_factory``,
which unit tests do not have; ``_loop`` is the logic under test)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from hecate.models.prompt_optimization import PromptOptimizationCandidateModel
from hecate.models.trace import TraceModel
from hecate.ops.evaluation.types import EvalInput, EvalOutput, Score
from hecate.ops.prompt_optimization.runner import PromptOptimizationRunner
from hecate.ops.prompt_optimization.strategy import MutationProposal
from tests.test_services.test_prompt_optimization.helpers import (
    dataset_items,
    make_run_row,
    seed_agent,
    seed_dataset_version,
    seed_prompt,
    ws_id,
)


class StubEvaluator:
    """Deterministic exact-match stub scoring 1.0 when the rollout answered
    the item's expected answer."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def description(self) -> str:
        return "stub exact match"

    async def evaluate(self, input: EvalInput) -> EvalOutput:
        value = 1.0 if input.generated_answer == input.expected_answer else 0.0
        return EvalOutput(scores=[Score(metric_name=self.name, value=value, source="deterministic")], duration_ms=1.0)


BASE_TEMPLATE = "Baseline prompt for {{q}}."
IMPROVED_TEMPLATE = "IMPROVED prompt for {{q}}: always answer with the expected value."


def _answers_when_improved(query: str, override: str) -> tuple[str, dict]:
    """Fake agent behavior: the improved template answers the expected value
    (derived from the query), any other template answers wrong."""
    if "IMPROVED" in override:
        match = re.search(r"question (\d+)", query)
        return f"expected-{match.group(1)}" if match else "expected-0", {"total_tokens": 11}
    return "wrong answer", {"total_tokens": 5}


def _fake_agent(answers_by_override):
    async def _generate(query, agent_id, agent_definition=None, rollout_capture=None):
        override = getattr(agent_definition, "prompt_override", "") if agent_definition is not None else ""
        generated, usage = answers_by_override(query, override)
        if rollout_capture is not None:
            rollout_capture.append({"item_id": None, "generated": generated, "usage": usage})
        return generated

    return _generate


def _install_stubs(monkeypatch: pytest.MonkeyPatch, proposals: list) -> list[dict]:
    """Stub the strategy (queued proposals) and the rollout agent answers."""
    from hecate.ops import prompt_optimization as po

    proposals_iter = iter(proposals)
    strategy_calls: list[dict] = []

    class StubStrategy:
        name = "reflective_mutation"

        async def mutate(self, base_template, parent_template, evidence, reflection_model):
            strategy_calls.append({"parent": parent_template, "evidence": evidence, "model": reflection_model})
            proposal = next(proposals_iter)
            if isinstance(proposal, Exception):
                raise proposal
            return MutationProposal(template=proposal, summary="stub summary")

    monkeypatch.setattr(po.runner, "_default_strategies", lambda: {"reflective_mutation": StubStrategy()})
    monkeypatch.setattr(
        po.runner,
        "get_evaluator_class",
        lambda name: StubEvaluator if name == "stub" else None,
    )

    monkeypatch.setattr(
        po.runner.PromptOptimizationRunner,
        "_cancel_requested",
        lambda self, _run_id: _async_false(),
    )
    # staticmethod: class-attribute assignment would bind the engine
    # instance as the first positional argument.
    monkeypatch.setattr(
        po.runner.EvaluationEngine,
        "_generate_answer_via_agent",
        staticmethod(_fake_agent(_answers_when_improved)),
    )
    return strategy_calls


async def _seed_running_run(db_session, default_workspace, *, max_rounds: int = 2, rollout_item_limit: int = 100):
    ws = ws_id(default_workspace)
    prompt = seed_prompt(db_session, ws, BASE_TEMPLATE, ["q"])
    agent = seed_agent(db_session, ws)
    _, version = seed_dataset_version(db_session, ws, dataset_items(4))
    run = make_run_row(
        prompt,
        agent,
        version,
        ws,
        max_rounds=max_rounds,
        rollout_item_limit=rollout_item_limit,
    )
    run.status = "running"
    run.started_at = datetime.now(UTC)
    db_session.add(run)
    await db_session.flush()
    return run


async def _candidates(db_session, run_id) -> list[PromptOptimizationCandidateModel]:
    return list(
        (
            await db_session.execute(
                select(PromptOptimizationCandidateModel).where(PromptOptimizationCandidateModel.run_id == run_id)
            )
        ).scalars()
    )


async def test_loop_accepts_improving_candidate_and_awaits_review(
    db_session, default_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _seed_running_run(db_session, default_workspace, max_rounds=1)
    strategy_calls = _install_stubs(monkeypatch, [IMPROVED_TEMPLATE])

    runner = PromptOptimizationRunner(db_session)
    usage = {"rollout_items": 0, "mutation_calls": 0, "rollout_tokens": 0, "eval_runs": 0}
    stop = await runner._loop(run, db_session, usage)
    await runner._finalize(run, db_session)
    run.usage = usage

    candidates = await _candidates(db_session, run.id)
    assert len(candidates) == 1
    assert candidates[0].status == "pending_review"
    assert candidates[0].gate_report["accepted"] is True
    assert candidates[0].reflection_summary == "stub summary"
    per_item = candidates[0].per_item_results
    assert per_item and all(entry["generated"] is not None for entry in per_item)
    assert run.status == "awaiting_review"
    assert stop.value == "max_rounds"
    assert run.usage["baseline"]["validation"]["metric_averages"]["stub"] == 0.0
    assert run.usage["rollout_items"] == 8  # 4 baseline + 4 candidate (2 train + 2 val each)
    assert strategy_calls[0]["model"] == "test-model"

    traces = list(
        (await db_session.execute(select(TraceModel).where(TraceModel.type == "prompt_optimization"))).scalars()
    )
    assert len(traces) == 4  # 2 baseline passes + train + validation for the candidate
    meta = traces[0].metadata_
    assert meta["optimization_run_id"] == str(run.id)
    assert meta["prompt_id"] == str(run.prompt_id)
    assert meta["prompt_version"] == run.base_version


async def test_loop_records_rejected_mutation_for_invalid_template(
    db_session, default_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _seed_running_run(db_session, default_workspace, max_rounds=1)
    _install_stubs(monkeypatch, ["Template with {{extra_var}} and {{q}}."])

    runner = PromptOptimizationRunner(db_session)
    usage = {"rollout_items": 0, "mutation_calls": 0, "rollout_tokens": 0, "eval_runs": 0}
    stop = await runner._loop(run, db_session, usage)
    await runner._finalize(run, db_session)
    run.usage = usage

    candidates = await _candidates(db_session, run.id)
    assert len(candidates) == 1
    assert candidates[0].status == "rejected_mutation"
    assert "variable_set_mismatch" in (candidates[0].rejection_reason or "")
    assert run.round_count == 1
    assert stop.value == "max_rounds"
    assert run.status == "concluded"  # no pending candidates


async def test_loop_stops_on_no_progress(db_session, default_workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    run = await _seed_running_run(db_session, default_workspace, max_rounds=5)
    _install_stubs(monkeypatch, [BASE_TEMPLATE] * 3)

    runner = PromptOptimizationRunner(db_session)
    usage = {"rollout_items": 0, "mutation_calls": 0, "rollout_tokens": 0, "eval_runs": 0}
    stop = await runner._loop(run, db_session, usage)
    await runner._finalize(run, db_session)
    run.usage = usage

    assert stop.value == "no_progress"
    assert run.status == "concluded"  # no pending_review candidates
    candidates = await _candidates(db_session, run.id)
    assert all(c.status == "gate_rejected" for c in candidates)
    assert len(candidates) == 3


async def test_loop_stops_when_rollout_budget_exhausted(
    db_session, default_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = await _seed_running_run(db_session, default_workspace, rollout_item_limit=4)
    _install_stubs(monkeypatch, [IMPROVED_TEMPLATE])

    runner = PromptOptimizationRunner(db_session)
    usage = {"rollout_items": 0, "mutation_calls": 0, "rollout_tokens": 0, "eval_runs": 0}
    stop = await runner._loop(run, db_session, usage)
    await runner._finalize(run, db_session)

    assert stop.value == "budget_exhausted"
    assert usage["mutation_calls"] == 0  # budget checked before mutating
    assert run.status == "concluded"


async def test_loop_honors_cancel_flag(db_session, default_workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    run = await _seed_running_run(db_session, default_workspace, max_rounds=3)
    _install_stubs(monkeypatch, [IMPROVED_TEMPLATE] * 3)
    runner = PromptOptimizationRunner(db_session)

    async def _cancelled(_run_id: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(runner, "_cancel_requested", _cancelled)
    usage = {"rollout_items": 0, "mutation_calls": 0, "rollout_tokens": 0, "eval_runs": 0}
    stop = await runner._loop(run, db_session, usage)
    await runner._finalize(run, db_session)
    run.usage = usage
    assert stop.value == "cancelled"


async def _async_false() -> bool:
    return False
