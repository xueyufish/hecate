"""Tests for the CONTROLLER node and ControllerWorker (2.6a)."""

from __future__ import annotations

import uuid

import pytest

from hecate.runtime.compiler import GraphCompiler
from hecate.runtime.errors import GraphValidationError
from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.intent.types import EvidencePayload
from hecate.runtime.types import ChannelDef, ChannelType, Edge, GraphConfig, NodeConfig, NodeType
from hecate.runtime.workers.controller_worker import INTENT_STATE_KEY, ControllerWorker

SESSION_ID = uuid.uuid4()


class StubLLMPort:
    """Canned-JSON LLM stub."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    async def llm_invoke(self, messages: list[dict], config: dict):
        self.calls += 1
        yield self.response


class StubEvidencePort:
    """Evidence provider stub returning one fixed payload."""

    def __init__(self, payload: EvidencePayload | None, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.calls = 0

    async def get_evidence(self, package_id, version_id=None, workspace_id=None) -> EvidencePayload:
        self.calls += 1
        if self.fail or self.payload is None:
            raise LookupError("no published version")
        return self.payload


def _evidence() -> EvidencePayload:
    from hecate.runtime.intent.types import EvidenceCategory

    return EvidencePayload(
        package_id=uuid.uuid4(),
        version_id=uuid.uuid4(),
        version_name="v1",
        categories=(
            EvidenceCategory(
                name="billing",
                description="Billing",
                domain="finance",
                samples=("refund my order",),
            ),
            EvidenceCategory(name="tech", description="Tech", samples=("app crashes",)),
        ),
    )


def _config(**overrides) -> dict:
    base = {
        "intent_package": {"package_id": str(uuid.uuid4())},
        "category_targets": {"billing": "billing_agent", "tech": "tech_agent"},
        "default_workflow": "fallback_agent",
        "global_intent": {"enabled": False},
    }
    base.update(overrides)
    return base


def _snapshot(utterance: str = "refund my order", prior_state: dict | None = None) -> dict:
    return {"messages": [{"role": "user", "content": utterance}], INTENT_STATE_KEY: prior_state}


async def _execute(worker: ControllerWorker, config: dict, snapshot: dict):
    store = InMemoryEventStore()
    ctx = {"event_store": store, "session_id": SESSION_ID, "superstep": 0}
    result = await worker.execute("controller", config, snapshot, execution_context=ctx)
    events = await store.get_events(SESSION_ID)
    return result, events


async def test_atomic_intent_routes_to_mapped_target():
    port = StubLLMPort('{"label": "billing", "confidence": 0.9}')
    worker = ControllerWorker(port=port, evidence_port=StubEvidencePort(_evidence()))
    result, events = await _execute(worker, _config(), _snapshot())

    assert result.error is None
    assert result.channel_updates["_route"] == "billing_agent"
    assert result.channel_updates[INTENT_STATE_KEY]["goal"] == "billing"
    types = [event.event_type for event in events]
    assert types == [EventType.INTENT_RECOGNIZED, EventType.CONTROLLER_ROUTED]
    routed = events[1].payload
    assert routed["target"] == "billing_agent"
    assert routed["level"] == "atomic"


async def test_no_match_routes_to_default():
    port = StubLLMPort('{"label": null, "confidence": 0.0}')
    worker = ControllerWorker(port=port, evidence_port=StubEvidencePort(_evidence()))
    result, events = await _execute(worker, _config(), _snapshot("hello there"))

    assert result.channel_updates["_route"] == "fallback_agent"
    assert events[1].payload["level"] == "default"


async def test_start_workflow_overrides_first_turn():
    worker = ControllerWorker(
        port=StubLLMPort('{"label": "billing", "confidence": 0.9}'),
        evidence_port=StubEvidencePort(_evidence()),
    )
    config = _config(start_workflow="welcome_agent")
    result, events = await _execute(worker, config, _snapshot(prior_state={}))

    assert result.channel_updates["_route"] == "welcome_agent"
    assert events[1].payload["level"] == "start"
    # Not first turn anymore → atomic routing wins.
    prior = result.channel_updates[INTENT_STATE_KEY]
    second, events2 = await _execute(
        worker,
        config,
        _snapshot("refund my order", prior_state=prior),
    )
    assert second.channel_updates["_route"] == "billing_agent"
    assert events2[1].payload["level"] == "atomic"


async def test_sticky_goal_does_not_thrash_routing():
    port = StubLLMPort('{"label": "tech", "confidence": 0.8, "goal_shift": false}')
    worker = ControllerWorker(port=port, evidence_port=StubEvidencePort(_evidence()))
    prior = {"goal": "billing", "turn_labels": ["billing"], "workflow_label": None}
    result, _ = await _execute(worker, _config(), _snapshot("app crashes", prior_state=prior))

    # Single mismatch without shift cue stays sticky — but the *atomic*
    # mapping still routes this turn to the tech agent; the session goal
    # remains billing.
    assert result.channel_updates["_route"] == "tech_agent"
    assert result.channel_updates[INTENT_STATE_KEY]["goal"] == "billing"


async def test_explicit_shift_updates_goal():
    port = StubLLMPort('{"label": "tech", "confidence": 0.9, "goal_shift": true}')
    worker = ControllerWorker(port=port, evidence_port=StubEvidencePort(_evidence()))
    prior = {"goal": "billing", "turn_labels": ["billing"], "workflow_label": None}
    result, _ = await _execute(worker, _config(), _snapshot("fix the app now", prior_state=prior))

    assert result.channel_updates["_route"] == "tech_agent"
    assert result.channel_updates[INTENT_STATE_KEY]["goal"] == "tech"


async def test_workflow_intent_routes_when_active():
    prior = {
        "goal": "billing",
        "turn_labels": ["billing", "billing"],
        "workflow_label": "billing",
    }
    # Null label on this turn — the active workflow intent keeps routing.
    port = StubLLMPort('{"label": null, "confidence": 0.0}')
    worker2 = ControllerWorker(port=port, evidence_port=StubEvidencePort(_evidence()))
    result, events = await _execute(worker2, _config(), _snapshot("continue", prior_state=prior))

    assert result.channel_updates["_route"] == "billing_agent"
    assert events[1].payload["level"] == "workflow"


async def test_evidence_failure_degrades_but_routes():
    worker = ControllerWorker(
        port=StubLLMPort('{"label": "billing", "confidence": 0.7}'),
        evidence_port=StubEvidencePort(None, fail=True),
    )
    result, events = await _execute(worker, _config(), _snapshot())

    assert result.error is None
    assert result.channel_updates["_route"] == "billing_agent"  # fallback labels
    recognized = events[0].payload
    assert recognized["evidence_available"] is False


async def test_missing_default_workflow_rejected_at_runtime():
    worker = ControllerWorker(port=StubLLMPort("{}"), evidence_port=StubEvidencePort(_evidence()))
    config = _config()
    del config["default_workflow"]
    result, _ = await _execute(worker, config, _snapshot())
    assert result.error is not None
    assert "default_workflow" in str(result.error)


async def test_empty_category_targets_rejected_at_runtime():
    worker = ControllerWorker(port=StubLLMPort("{}"), evidence_port=StubEvidencePort(_evidence()))
    result, _ = await _execute(worker, _config(category_targets={}), _snapshot())
    assert result.error is not None
    assert "category_targets" in str(result.error)


async def test_invalid_package_reference_rejected_at_runtime():
    worker = ControllerWorker(port=StubLLMPort("{}"), evidence_port=StubEvidencePort(_evidence()))
    config = _config(intent_package={"package_id": "not-a-uuid"})
    result, _ = await _execute(worker, config, _snapshot())
    assert result.error is not None
    assert "intent_package" in str(result.error)


# ---------------------------------------------------------------------------
# Compiler shape validation (4.1)
# ---------------------------------------------------------------------------


def _graph(controller_config: dict) -> GraphConfig:
    return GraphConfig(
        version="1.0",
        name="controller-graph",
        state={
            "messages": ChannelDef(type=ChannelType.TOPIC, default=[]),
            "_route": ChannelDef(type=ChannelType.LAST_VALUE, default=""),
            "_intent_state": ChannelDef(type=ChannelType.LAST_VALUE, default=None),
        },
        nodes={
            "controller": NodeConfig(id="controller", type=NodeType.CONTROLLER, config=controller_config),
            "billing_agent": NodeConfig(id="billing_agent", type=NodeType.AGENT, config={}),
            "tech_agent": NodeConfig(id="tech_agent", type=NodeType.AGENT, config={}),
            "fallback_agent": NodeConfig(id="fallback_agent", type=NodeType.AGENT, config={}),
        },
        edges=[
            Edge(
                source="controller",
                target={"billing": "billing_agent", "default": "fallback_agent"},
            ),
        ],
        entry="controller",
    )


def test_compiler_accepts_valid_controller_graph():
    compiler = GraphCompiler()
    compiled = compiler.compile(_graph(_config()))
    assert "controller" in compiled.nodes


def test_compiler_rejects_missing_default():
    compiler = GraphCompiler()
    config = _config()
    del config["default_workflow"]
    with pytest.raises(GraphValidationError, match="default_workflow"):
        compiler.compile(_graph(config))


def test_compiler_rejects_undeclared_target():
    compiler = GraphCompiler()
    with pytest.raises(GraphValidationError, match="not a declared node"):
        compiler.compile(_graph(_config(category_targets={"billing": "ghost_node"})))


def test_compiler_rejects_undeclared_start_workflow():
    compiler = GraphCompiler()
    with pytest.raises(GraphValidationError, match="not a declared node"):
        compiler.compile(_graph(_config(start_workflow="ghost_start")))


def test_compiler_rejects_missing_package_reference():
    compiler = GraphCompiler()
    config = _config()
    del config["intent_package"]
    with pytest.raises(GraphValidationError, match="intent_package"):
        compiler.compile(_graph(config))


def test_controller_node_type_parses_in_dsl_shape():
    assert NodeType("controller") == NodeType.CONTROLLER
