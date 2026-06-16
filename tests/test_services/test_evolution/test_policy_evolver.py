"""Unit tests for PolicyEvolver."""

from __future__ import annotations

import pytest

from hecate.services.evolution.policy_evolver import (
    PolicyEvolver,
    PromptStrategy,
    ToolStrategy,
)
from hecate.services.evolution.trajectory_analyzer import AnalysisResult


@pytest.fixture
def evolver() -> PolicyEvolver:
    return PolicyEvolver()


def test_adjust_tool_strategy_boosts_on_success(evolver: PolicyEvolver) -> None:
    results = [
        AnalysisResult(
            trajectory_id="t1",
            pattern_type="success",
            key_factors=["Effective tools: search, calculator"],
        ),
    ]
    strategies = evolver.adjust_tool_strategy(results)
    assert "search" in strategies
    assert strategies["search"].priority > 0.5
    assert strategies["calculator"].priority > 0.5


def test_adjust_tool_strategy_penalizes_on_failure(evolver: PolicyEvolver) -> None:
    results = [
        AnalysisResult(
            trajectory_id="t2",
            pattern_type="failure",
            key_factors=["Effective tools: search"],
        ),
    ]
    strategies = evolver.adjust_tool_strategy(results)
    assert "search" in strategies
    assert strategies["search"].priority < 0.5


def test_tool_priority_clamped(evolver: PolicyEvolver) -> None:
    results = [
        AnalysisResult(
            trajectory_id="t3",
            pattern_type="success",
            key_factors=["Effective tools: search"],
        ),
    ] * 20
    evolver.adjust_tool_strategy(results)
    assert evolver._tool_strategies["search"].priority <= 1.0


def test_adjust_prompt_strategy_suggests_improvements(evolver: PolicyEvolver) -> None:
    results = [
        AnalysisResult(
            trajectory_id="t4",
            pattern_type="failure",
            key_factors=["Failure at step 2"],
            improvement_suggestions=["Increase execution timeout"],
        ),
    ]
    prompts = evolver.adjust_prompt_strategy(results)
    assert "t4" in prompts
    assert len(prompts["t4"].suggested_improvements) > 0


def test_evolve_returns_both(evolver: PolicyEvolver) -> None:
    results = [
        AnalysisResult(
            trajectory_id="t5",
            pattern_type="success",
            key_factors=["Effective tools: search"],
        ),
    ]
    tools, prompts = evolver.evolve(results)
    assert isinstance(tools, dict)
    assert isinstance(prompts, dict)


def test_get_tool_ranking(evolver: PolicyEvolver) -> None:
    evolver._tool_strategies = {
        "a": ToolStrategy(tool_name="a", priority=0.3),
        "b": ToolStrategy(tool_name="b", priority=0.9),
        "c": ToolStrategy(tool_name="c", priority=0.6),
    }
    ranking = evolver.get_tool_ranking()
    assert ranking[0][0] == "b"
    assert ranking[-1][0] == "a"


def test_get_prompt_recommendations(evolver: PolicyEvolver) -> None:
    evolver._prompt_strategies = {
        "p1": PromptStrategy(template_id="p1", effectiveness_score=0.3),
        "p2": PromptStrategy(template_id="p2", effectiveness_score=0.9),
    }
    recs = evolver.get_prompt_recommendations()
    assert recs[0].template_id == "p2"
    assert recs[-1].template_id == "p1"
