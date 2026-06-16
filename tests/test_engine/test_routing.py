"""Tests for routing evaluation module."""

from __future__ import annotations

from typing import Any

from hecate.engine.routing import evaluate_routing


class MockEnginePort:
    """Stub EnginePort for LLM-dependent routing tests."""

    def __init__(self, response: str = "") -> None:
        self._response = response

    async def llm_invoke(self, prompt: str, **kwargs: Any) -> str:
        return self._response


class TestConditionRouting:
    async def test_condition_mode_returns_input_as_route(self) -> None:
        result = await evaluate_routing(
            routing_mode="condition",
            routing_config={},
            input_value="high",
            channel_snapshot={},
        )
        assert result == "high"

    async def test_condition_mode_default_returns_true(self) -> None:
        result = await evaluate_routing(
            routing_mode="",
            routing_config={},
            input_value=None,
            channel_snapshot={},
        )
        assert result == "true"


class TestIntentRouting:
    async def test_matching_pattern_returns_target(self) -> None:
        result = await evaluate_routing(
            routing_mode="intent",
            routing_config={
                "intent_patterns": [
                    {"pattern": "billing|invoice", "target": "billing_agent"},
                    {"pattern": "technical|bug", "target": "tech_support"},
                ],
            },
            input_value="I have a billing question",
            channel_snapshot={},
        )
        assert result == "billing_agent"

    async def test_no_match_with_llm_fallback(self) -> None:
        port = MockEnginePort(response="tech_support")
        result = await evaluate_routing(
            routing_mode="intent",
            routing_config={
                "intent_patterns": [{"pattern": "billing", "target": "billing_agent"}],
                "routing_prompt": "Classify intent",
            },
            input_value="How do I reset my password?",
            channel_snapshot={},
            engine_port=port,
        )
        assert result == "tech_support"

    async def test_no_match_no_prompt_returns_default(self) -> None:
        result = await evaluate_routing(
            routing_mode="intent",
            routing_config={
                "intent_patterns": [{"pattern": "billing", "target": "billing_agent"}],
            },
            input_value="Hello, how are you?",
            channel_snapshot={},
        )
        assert result == "default"


class TestDynamicRouting:
    async def test_returns_valid_agent(self) -> None:
        port = MockEnginePort(response="agent_b")
        result = await evaluate_routing(
            routing_mode="dynamic",
            routing_config={
                "candidate_agents": ["agent_a", "agent_b", "agent_c"],
                "routing_prompt": "Select the best agent",
            },
            input_value="User has a question",
            channel_snapshot={},
            engine_port=port,
        )
        assert result == "agent_b"

    async def test_invalid_response_falls_back_to_default(self) -> None:
        port = MockEnginePort(response="unknown_agent")
        result = await evaluate_routing(
            routing_mode="dynamic",
            routing_config={
                "candidate_agents": ["agent_a", "agent_b"],
                "routing_prompt": "Select the best agent",
            },
            input_value="Hello",
            channel_snapshot={},
            engine_port=port,
        )
        assert result == "default"

    async def test_excludes_last_speaker(self) -> None:
        port = MockEnginePort(response="agent_a")
        result = await evaluate_routing(
            routing_mode="dynamic",
            routing_config={
                "candidate_agents": ["agent_a", "agent_b"],
                "routing_prompt": "Select the best agent",
                "allow_repeated_speaker": False,
            },
            input_value="Continue",
            channel_snapshot={},
            engine_port=port,
            last_speaker="agent_a",
        )
        assert result == "agent_a"

    async def test_no_candidates_after_filter_returns_default(self) -> None:
        result = await evaluate_routing(
            routing_mode="dynamic",
            routing_config={
                "candidate_agents": ["agent_a"],
                "allow_repeated_speaker": False,
            },
            input_value="Continue",
            channel_snapshot={},
            last_speaker="agent_a",
        )
        assert result == "default"
