"""Unit tests for SyntheticEnvironmentGenerator."""

from __future__ import annotations

import pytest

from hecate.services.evolution.environment_generator import (
    EnvironmentConfig,
    SyntheticEnvironmentGenerator,
)


@pytest.fixture
def generator() -> SyntheticEnvironmentGenerator:
    return SyntheticEnvironmentGenerator()


def test_generate_environment_tool_selection(generator: SyntheticEnvironmentGenerator) -> None:
    env = generator.generate_environment("tool_selection", difficulty=0.4)
    assert "tool_selection" in env.name
    assert env.difficulty == 0.4
    assert len(env.tools_available) > 0
    assert len(env.constraints) > 0


def test_generate_environment_coding(generator: SyntheticEnvironmentGenerator) -> None:
    env = generator.generate_environment("coding", difficulty=0.3)
    assert "coding" in env.name
    assert any(t in env.tools_available for t in ["code_executor", "test_runner"])


def test_generate_environment_unknown_capability(generator: SyntheticEnvironmentGenerator) -> None:
    env = generator.generate_environment("unknown_cap", difficulty=0.5)
    assert "unknown_cap" in env.name
    assert "search" in env.tools_available


def test_validate_difficulty_valid(generator: SyntheticEnvironmentGenerator) -> None:
    env = EnvironmentConfig(name="test", difficulty=0.4)
    validation = generator.validate_difficulty(env)
    assert validation.is_valid is True
    assert validation.estimated_success_rate > 0.2


def test_validate_difficulty_too_high(generator: SyntheticEnvironmentGenerator) -> None:
    env = EnvironmentConfig(name="hard", difficulty=0.8)
    validation = generator.validate_difficulty(env)
    assert validation.is_valid is False
    assert len(validation.adjustments) > 0


def test_validate_difficulty_too_low(generator: SyntheticEnvironmentGenerator) -> None:
    env = EnvironmentConfig(name="easy", difficulty=0.1)
    validation = generator.validate_difficulty(env)
    assert validation.is_valid is False
    assert len(validation.adjustments) > 0


def test_generate_batch(generator: SyntheticEnvironmentGenerator) -> None:
    caps = ["tool_selection", "reasoning", "coding"]
    envs = generator.generate_batch(caps, difficulty_range=(0.3, 0.5))
    assert len(envs) == 3
    difficulties = [e.difficulty for e in envs]
    assert min(difficulties) >= 0.3
    assert max(difficulties) <= 0.5


def test_higher_difficulty_fewer_tools(generator: SyntheticEnvironmentGenerator) -> None:
    easy = generator.generate_environment("tool_selection", difficulty=0.2)
    hard = generator.generate_environment("tool_selection", difficulty=0.8)
    assert len(easy.tools_available) >= len(hard.tools_available)
