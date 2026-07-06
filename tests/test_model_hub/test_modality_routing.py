"""Tests for modality-aware routing."""

from __future__ import annotations

import pytest

from hecate.services.llm.routing import (
    ModelInfo,
    ModelRouter,
    NoCapableModelError,
    RoutingStrategy,
)


@pytest.fixture
def router() -> ModelRouter:
    r = ModelRouter()
    r.register_model(
        ModelInfo(
            name="gpt-4o",
            provider="openai",
            cost_per_1k_input=0.005,
            cost_per_1k_output=0.015,
            avg_latency_ms=500,
            capabilities=["tool_call", "vision"],
            max_context_tokens=128000,
            modalities={"input": ["text", "image", "audio"], "output": ["text"]},
        )
    )
    r.register_model(
        ModelInfo(
            name="text-only-model",
            provider="openai",
            cost_per_1k_input=0.001,
            cost_per_1k_output=0.002,
            avg_latency_ms=200,
            capabilities=[],
            max_context_tokens=4096,
            modalities={"input": ["text"], "output": ["text"]},
        )
    )
    r.register_model(
        ModelInfo(
            name="embedding-model",
            provider="openai",
            cost_per_1k_input=0.0001,
            cost_per_1k_output=0.0,
            avg_latency_ms=50,
            capabilities=[],
            max_context_tokens=8192,
            modalities={"input": ["text"], "output": ["embedding"]},
        )
    )
    return r


def test_modality_filter_excludes_non_matching(router: ModelRouter) -> None:
    result = router.select_model(
        RoutingStrategy.COST,
        required_input_modalities=["image"],
    )
    assert result is not None
    assert result.name == "gpt-4o"


def test_modality_filter_allows_text_only(router: ModelRouter) -> None:
    result = router.select_model(
        RoutingStrategy.COST,
        required_input_modalities=["text"],
    )
    assert result is not None
    assert result.name in ("text-only-model", "embedding-model")


def test_modality_filter_no_capable_model_raises(router: ModelRouter) -> None:
    with pytest.raises(NoCapableModelError):
        router.select_model(
            RoutingStrategy.COST,
            required_input_modalities=["video"],
        )


def test_capability_filter_prefers_tool_call(router: ModelRouter) -> None:
    result = router.select_model(
        RoutingStrategy.CAPABILITY,
        required_capabilities=["tool_call"],
    )
    assert result is not None
    assert result.name == "gpt-4o"


def test_combined_modality_and_capability(router: ModelRouter) -> None:
    result = router.select_model(
        RoutingStrategy.COST,
        required_input_modalities=["image"],
        required_capabilities=["tool_call"],
    )
    assert result is not None
    assert result.name == "gpt-4o"


def test_no_modality_constraint_returns_any(router: ModelRouter) -> None:
    result = router.select_model(RoutingStrategy.COST)
    assert result is not None
    assert result.name == "embedding-model"
