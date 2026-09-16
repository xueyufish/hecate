"""Tests for 4.13 chain policy resolution, validation, and canonical hash."""

from __future__ import annotations

import pytest

from hecate.runtime.context_policy import (
    ChainPolicyError,
    ContextChainFactory,
    canonical_policy_hash,
    model_default_budget,
    model_supports_prompt_cache,
    validate_policy_spec,
)


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
