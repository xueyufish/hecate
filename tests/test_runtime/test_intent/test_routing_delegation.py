"""Tests for package-backed intent routing delegation (6.23 / 2.7c upgrade)."""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime import routing
from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.errors import GraphValidationError
from hecate.runtime.routing import evaluate_routing
from hecate.runtime.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType


@pytest.fixture(autouse=True)
def _fresh_shared_engine():
    """Isolate the module-level shared engine (its decision cache is
    process-global by design — production wants cross-turn hits, tests
    want isolation)."""
    routing._shared_engine = None
    yield
    routing._shared_engine = None


class StubLLMPort:
    """Canned-JSON LLM stub counting calls."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def llm_invoke(self, messages: list[dict], config: dict):
        self.calls += 1
        yield self.response


class StubEvidencePort:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = 0

    async def get_evidence(self, package_id, version_id=None, workspace_id=None):
        self.calls += 1
        if self.payload is None:
            raise LookupError("unpublished")
        return self.payload


def _evidence():
    from hecate.runtime.intent.types import EvidenceCategory, EvidencePayload

    return EvidencePayload(
        package_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_name="v1",
        categories=(
            EvidenceCategory(name="billing", description="Billing", samples=("refund my order",)),
            EvidenceCategory(name="tech", description="Tech", samples=("app crashes",)),
        ),
    )


def _package_config(**overrides) -> dict:
    base = {
        "intent_package": {"package_id": str(uuid.uuid4())},
        "category_targets": {"billing": "billing_agent", "tech": "tech_agent"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Legacy contract (byte-identical)
# ---------------------------------------------------------------------------


async def test_legacy_pattern_match_unchanged():
    result = await evaluate_routing(
        "intent",
        {"intent_patterns": [{"pattern": "billing|invoice", "target": "billing_agent"}]},
        "I have a billing question",
        {},
    )
    assert result == "billing_agent"


async def test_legacy_no_match_with_prompt_uses_llm():
    port = StubLLMPort("tech_support")
    result = await evaluate_routing(
        "intent",
        {
            "intent_patterns": [{"pattern": "billing", "target": "billing_agent"}],
            "routing_prompt": "Classify",
        },
        "reset my password",
        {},
        engine_port=port,
    )
    assert result == "tech_support"


async def test_legacy_no_match_no_prompt_returns_default():
    result = await evaluate_routing(
        "intent",
        {"intent_patterns": [{"pattern": "billing", "target": "billing_agent"}]},
        "Hello",
        {},
    )
    assert result == "default"


# ---------------------------------------------------------------------------
# Package-backed delegation
# ---------------------------------------------------------------------------


async def test_package_delegation_routes_via_engine():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    evidence_port = StubEvidencePort(_evidence())
    result = await evaluate_routing(
        "intent",
        _package_config(),
        "refund my order",
        {},
        engine_port=port,
        evidence_port=evidence_port,
    )
    assert result == "billing_agent"
    assert evidence_port.calls == 1
    assert port.calls == 1


async def test_package_delegation_caches_decisions():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    evidence_port = StubEvidencePort(_evidence())
    config = _package_config()
    first = await evaluate_routing(
        "intent", config, "refund my order", {}, engine_port=port, evidence_port=evidence_port
    )
    second = await evaluate_routing(
        "intent", config, "refund my order", {}, engine_port=port, evidence_port=evidence_port
    )
    assert first == second == "billing_agent"
    assert port.calls == 1  # cache hit on the second turn


async def test_package_delegation_unmapped_label_defaults():
    port = StubLLMPort('{"label": "other", "confidence": 0.9}')
    result = await evaluate_routing(
        "intent",
        _package_config(),
        "hello",
        {},
        engine_port=port,
        evidence_port=StubEvidencePort(_evidence()),
    )
    assert result == "default"


async def test_package_delegation_evidence_failure_degrades():
    port = StubLLMPort('{"label": "tech", "confidence": 0.7}')
    result = await evaluate_routing(
        "intent",
        _package_config(),
        "app crashes",
        {},
        engine_port=port,
        evidence_port=StubEvidencePort(None),
    )
    assert result == "tech_agent"


async def test_package_delegation_invalid_ref_defaults():
    result = await evaluate_routing(
        "intent",
        _package_config(intent_package={"package_id": "not-a-uuid"}),
        "hello",
        {},
        engine_port=StubLLMPort("{}"),
        evidence_port=StubEvidencePort(_evidence()),
    )
    assert result == "default"


# ---------------------------------------------------------------------------
# Compiler validation (modified intent requirement)
# ---------------------------------------------------------------------------


def _graph(routing_config: dict) -> GraphConfig:
    return GraphConfig(
        version="1.0",
        name="intent-graph",
        state={"messages": ChannelDef(type=ChannelType.TOPIC, default=[])},
        nodes={
            "route": NodeConfig(
                id="route",
                type=NodeType.CONDITION,
                config={"routing_mode": "intent", "routing_config": routing_config},
            ),
            "billing_agent": NodeConfig(id="billing_agent", type=NodeType.AGENT, config={}),
            "tech_agent": NodeConfig(id="tech_agent", type=NodeType.AGENT, config={}),
        },
        edges=[Edge(source="route", target={"billing": "billing_agent", "default": "tech_agent"})],
        entry="route",
    )


def test_compiler_accepts_package_backed_intent_config():
    compiler = GraphCompiler()
    compiled = compiler.compile(_graph(_package_config()))
    assert "route" in compiled.nodes


def test_compiler_rejects_intent_mode_without_patterns_or_package():
    compiler = GraphCompiler()
    with pytest.raises(GraphValidationError, match="intent_patterns or routing_config.intent_package"):
        compiler.compile(_graph({}))


def test_compiler_rejects_package_without_category_targets():
    compiler = GraphCompiler()
    with pytest.raises(GraphValidationError, match="category_targets"):
        compiler.compile(_graph({"intent_package": {"package_id": str(uuid.uuid4())}}))
