"""Tests for 4.13 chain policy resolution, validation, and canonical hash."""

from __future__ import annotations

import pytest

from hecate.runtime.context_policy import (
    ChainPolicyError,
    ContextChainFactory,
    canonical_policy_hash,
    model_default_budget,
    model_default_window,
    model_supports_prompt_cache,
    uses_surface_replacement,
    validate_compaction_exclusivity,
    validate_policy_spec,
)
from hecate.runtime.eviction import NoEviction, SizeBasedEviction


class TestModelCapability:
    def test_cache_capable_families(self) -> None:
        assert model_supports_prompt_cache("claude-sonnet-4") is True
        assert model_supports_prompt_cache("anthropic/claude-3-haiku") is True
        assert model_supports_prompt_cache("gpt-4o") is True
        assert model_supports_prompt_cache("deepseek-v3") is True

    def test_non_cache_family(self) -> None:
        assert model_supports_prompt_cache("qwen-72b") is False
        assert model_supports_prompt_cache(None) is False
        assert model_supports_prompt_cache("") is False

    def test_model_default_budget(self) -> None:
        assert model_default_budget("gpt-4o") == max(8000, 128_000 // 8)
        assert model_default_budget("unknown-model") is None


class TestValidation:
    def test_string_entries_valid(self) -> None:
        normalized = validate_policy_spec(["round_window", "offload"])
        assert [e["type"] for e in normalized] == ["round_window", "offload"]

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="unknown processor type"):
            validate_policy_spec([{"type": "magic_compressor"}])

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="unknown field"):
            validate_policy_spec([{"type": "round_window", "foo": 1}])

    def test_unknown_param_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="unknown param"):
            validate_policy_spec([{"type": "round_window", "params": {"nope": 1}}])

    def test_bad_param_type_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="invalid type"):
            validate_policy_spec([{"type": "kv_cache_aware", "params": {"window_units": "big"}}])

    def test_buffer_below_warn_constraint(self) -> None:
        ok = [
            {"type": "budget_warn", "params": {"threshold": 0.8}},
            {"type": "hint", "params": {"usage_buffer_ratio": 0.7}},
        ]
        assert validate_policy_spec(ok) is not None
        with pytest.raises(ChainPolicyError, match="strictly below"):
            validate_policy_spec(
                [
                    {"type": "budget_warn", "params": {"threshold": 0.6}},
                    {"type": "hint", "params": {"usage_buffer_ratio": 0.7}},
                ]
            )

    def test_not_a_list_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="must be a list"):
            validate_policy_spec("round_window")


class TestResolution:
    def test_node_overrides_platform_default(self) -> None:
        factory = ContextChainFactory()
        policy = factory.resolve(node_config={"context_processors": ["round_window"]})
        assert policy.names == ["round_window"]

    def test_agent_overrides_platform_default(self) -> None:
        factory = ContextChainFactory(agent_policy=[{"type": "round_window", "params": {"ranking": "recency"}}])
        policy = factory.resolve(node_config={})
        assert policy.names == ["round_window"]

    def test_node_overrides_agent(self) -> None:
        factory = ContextChainFactory(agent_policy=["round_window"])
        policy = factory.resolve(
            node_config={"context_processors": ["tool_result_truncation", "round_window"]},
        )
        assert policy.names == ["tool_result_truncation", "round_window"]

    def test_cache_capable_model_gets_kv_guard(self) -> None:
        factory = ContextChainFactory()
        policy = factory.resolve(node_config={}, model="claude-sonnet-4")
        assert "kv_cache_aware" in policy.names
        assert policy.metadata_source() == "platform"

    def test_non_cache_model_drops_kv_guard(self) -> None:
        factory = ContextChainFactory()
        policy = factory.resolve(node_config={}, model="qwen-72b")
        assert "kv_cache_aware" not in policy.names
        assert policy.metadata_source() == "model_capability"

    def test_default_policy_matches_legacy_ladder(self) -> None:
        factory = ContextChainFactory()
        policy = factory.resolve(node_config={}, model="gpt-4o")
        assert policy.names == [
            "tool_result_truncation",
            "kv_cache_aware",
            "round_window",
            "offload",
            "compression",
            "terminate",
        ]

    def test_invalid_node_config_raises(self) -> None:
        factory = ContextChainFactory()
        with pytest.raises(ChainPolicyError):
            factory.resolve(node_config={"context_processors": [{"type": "bogus"}]})


class TestCanonicalHash:
    def test_identical_policies_same_hash(self) -> None:
        factory = ContextChainFactory()
        p1 = factory.resolve(node_config={"context_processors": ["round_window"]})
        p2 = factory.resolve(node_config={"context_processors": ["round_window"]})
        assert p1.canonical_hash == p2.canonical_hash

    def test_different_policies_different_hash(self) -> None:
        factory = ContextChainFactory()
        p1 = factory.resolve(node_config={"context_processors": ["round_window"]})
        p2 = factory.resolve(node_config={"context_processors": ["offload", "round_window"]})
        assert p1.canonical_hash != p2.canonical_hash

    def test_hash_function_is_stable(self) -> None:
        h1 = canonical_policy_hash(["round_window"], [{}], {"source": "node"})
        h2 = canonical_policy_hash(["round_window"], [{}], {"source": "node"})
        assert h1 == h2
        assert len(h1) == 16


class TestChainFactory:
    def test_chains_cached_per_hash(self) -> None:
        factory = ContextChainFactory()
        c1 = factory.chain_for_node({"context_processors": ["round_window"]})
        c2 = factory.chain_for_node({"context_processors": ["round_window"]})
        assert c1 is c2

    def test_session_state_shared_across_calls(self) -> None:
        factory = ContextChainFactory()
        c1 = factory.chain_for_node({"context_processors": ["round_window"]})
        c1.session_state["warn_latched:s1"] = True
        c2 = factory.chain_for_node({"context_processors": ["round_window"]})
        assert c2.session_state.get("warn_latched:s1") is True

    def test_different_policies_different_chains(self) -> None:
        factory = ContextChainFactory()
        c1 = factory.chain_for_node({"context_processors": ["round_window"]})
        c2 = factory.chain_for_node({"context_processors": ["round_window", "offload"]})
        assert c1 is not c2

    async def test_chain_from_factory_applies(self) -> None:
        factory = ContextChainFactory()
        chain = factory.chain_for_node({"context_processors": ["round_window", "terminate"]})
        report = await chain.apply(
            [{"role": "user", "content": "x" * 400} for _ in range(10)],
            {},
            {"context_budget": 100, "session_id": "s1"},
            "n1",
        )
        assert report.levels


class TestCompressionBackendValidation:
    """ADR-033: compression parameter validation and compaction exclusivity."""

    def test_ratio_params_accepted(self) -> None:
        normalized = validate_policy_spec(
            [
                {
                    "type": "compression",
                    "params": {"backend": "surface_replacement", "trigger_ratio": 0.8, "retain_ratio": 0.16},
                }
            ]
        )
        assert normalized[0]["params"] == {"backend": "surface_replacement", "trigger_ratio": 0.8, "retain_ratio": 0.16}

    def test_trigger_ratio_out_of_range_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="trigger_ratio"):
            validate_policy_spec([{"type": "compression", "params": {"trigger_ratio": 1.5}}])
        with pytest.raises(ChainPolicyError, match="trigger_ratio"):
            validate_policy_spec([{"type": "compression", "params": {"trigger_ratio": 0}}])

    def test_retain_ratio_out_of_range_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="retain_ratio"):
            validate_policy_spec([{"type": "compression", "params": {"retain_ratio": 1.0}}])
        with pytest.raises(ChainPolicyError, match="retain_ratio"):
            validate_policy_spec([{"type": "compression", "params": {"retain_ratio": -0.1}}])

    def test_unknown_backend_rejected(self) -> None:
        with pytest.raises(ChainPolicyError, match="backend"):
            validate_policy_spec([{"type": "compression", "params": {"backend": "in_place"}}])


class TestModelDefaultWindow:
    def test_known_family(self) -> None:
        assert model_default_window("glm-5") == 200_000
        assert model_default_window("claude-sonnet-4") == 200_000

    def test_unknown_family(self) -> None:
        assert model_default_window("totally-unknown-model") is None
        assert model_default_window(None) is None


class TestCompactionExclusivity:
    def test_surface_backend_spec_detected(self) -> None:
        assert uses_surface_replacement([{"type": "compression", "params": {"backend": "surface_replacement"}}])
        assert not uses_surface_replacement(["compression"])
        assert not uses_surface_replacement([{"type": "compression", "params": {"backend": "projection"}}])
        assert not uses_surface_replacement(None)

    def test_noop_eviction_allows_surface_backend(self) -> None:
        specs = [[{"type": "compression", "params": {"backend": "surface_replacement"}}]]
        validate_compaction_exclusivity(NoEviction(), specs)
        validate_compaction_exclusivity(None, specs)

    def test_active_eviction_with_surface_backend_rejected(self) -> None:
        specs = [[{"type": "compression", "params": {"backend": "surface_replacement"}}]]
        with pytest.raises(ChainPolicyError, match="eviction"):
            validate_compaction_exclusivity(SizeBasedEviction(max_size=10), specs)

    def test_active_eviction_with_projection_backend_allowed(self) -> None:
        validate_compaction_exclusivity(SizeBasedEviction(max_size=10), [["compression"]])
