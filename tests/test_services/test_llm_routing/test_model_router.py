"""Unit tests for ModelRouter."""

from __future__ import annotations

from hecate.services.llm.routing import ModelInfo, ModelRouter, RoutingStrategy


def _make_model(
    name: str,
    cost_in: float = 0.01,
    cost_out: float = 0.03,
    latency: float = 500.0,
    capabilities: list[str] | None = None,
) -> ModelInfo:
    return ModelInfo(
        name=name,
        provider="test",
        cost_per_1k_input=cost_in,
        cost_per_1k_output=cost_out,
        avg_latency_ms=latency,
        capabilities=capabilities or [],
        max_context_tokens=8192,
    )


class TestModelRouter:
    def test_register_and_list(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("gpt-4o"))
        router.register_model(_make_model("claude-3"))

        assert sorted(router.list_models()) == ["claude-3", "gpt-4o"]

    def test_unregister(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("gpt-4o"))
        router.unregister_model("gpt-4o")

        assert router.list_models() == []

    def test_get_model(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("gpt-4o"))

        assert router.get_model("gpt-4o") is not None
        assert router.get_model("missing") is None

    def test_select_by_cost(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("expensive", cost_in=0.05, cost_out=0.15))
        router.register_model(_make_model("cheap", cost_in=0.001, cost_out=0.002))

        result = router.select_model(RoutingStrategy.COST)
        assert result is not None
        assert result.name == "cheap"

    def test_select_by_latency(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("slow", latency=2000.0))
        router.register_model(_make_model("fast", latency=100.0))

        result = router.select_model(RoutingStrategy.LATENCY)
        assert result is not None
        assert result.name == "fast"

    def test_select_by_capability(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("model-a", capabilities=["search"]))
        router.register_model(_make_model("model-b", capabilities=["search", "code"]))

        result = router.select_model(RoutingStrategy.CAPABILITY, required_capabilities=["code"])
        assert result is not None
        assert result.name == "model-b"

    def test_select_balanced(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model(
            "balanced",
            cost_in=0.005,
            cost_out=0.015,
            latency=300.0,
            capabilities=["search", "code"],
        ))
        router.register_model(_make_model(
            "unbalanced",
            cost_in=0.05,
            cost_out=0.15,
            latency=2000.0,
            capabilities=["search"],
        ))

        result = router.select_model(
            RoutingStrategy.BALANCED,
            required_capabilities=["search"],
        )
        assert result is not None
        assert result.name == "balanced"

    def test_select_with_cost_constraint(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("cheap", cost_in=0.001, cost_out=0.002))
        router.register_model(_make_model("expensive", cost_in=0.05, cost_out=0.15))

        result = router.select_model(RoutingStrategy.LATENCY, max_cost_per_1k=0.01)
        assert result is not None
        assert result.name == "cheap"

    def test_select_with_latency_constraint(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("slow", latency=2000.0))
        router.register_model(_make_model("fast", latency=100.0))

        result = router.select_model(RoutingStrategy.COST, max_latency_ms=500.0)
        assert result is not None
        assert result.name == "fast"

    def test_select_no_candidates(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("only-model", capabilities=["read"]))

        result = router.select_model(RoutingStrategy.COST, required_capabilities=["fly"])
        assert result is None

    def test_select_empty_pool(self) -> None:
        router = ModelRouter()
        result = router.select_model(RoutingStrategy.COST)
        assert result is None

    def test_capability_filter_then_cost_sort(self) -> None:
        router = ModelRouter()
        router.register_model(_make_model("a", cost_in=0.01, capabilities=["search"]))
        router.register_model(_make_model("b", cost_in=0.001, capabilities=["search"]))
        router.register_model(_make_model("c", cost_in=0.001, capabilities=["write"]))

        result = router.select_model(RoutingStrategy.COST, required_capabilities=["search"])
        assert result is not None
        assert result.name == "b"
