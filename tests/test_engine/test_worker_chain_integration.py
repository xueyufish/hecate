"""ToolWorker + chain integration (T1.3).

The worker accepts ``middleware_chains`` as an optional constructor parameter;
when present, the legacy single-hook slots are bypassed and the chain runs
instead. This file pins that contract.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.engine.guardrail import GuardrailAction, GuardrailResult, PreToolHook
from hecate.engine.middleware import Chain, Phase, StageDecision
from hecate.engine.middleware_factory import build_tool_chain
from hecate.engine.workers.tool_worker import ToolWorker


def _tool_call_payload(call_id: str, name: str, args: dict) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
            },
        ]
    }


class _StubPort:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args))
        return {"executed": True, "name": name, "args": args}

    async def create_span(self, *args, **kwargs):
        class _CM:
            span_id = "test-span"

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


def _fake_event_store():
    s = MagicMock()
    s.append = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_chain_takes_precedence_over_legacy_pre_hook():
    """A configured TOOL_PRE_EXECUTE chain overrides the legacy ``pre_tool_hook`` slot."""
    invocations: list[str] = []

    class _Legacy(PreToolHook):
        matcher = None

        async def on_pre_tool_call(self, name, arguments, context):
            invocations.append(f"legacy:{name}")
            return GuardrailResult(action=GuardrailAction.BLOCK, reason="legacy-block")

    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)
    invocations_chain: list[str] = []

    async def chain_stage(data, call_next):
        invocations_chain.append(f"chain:{data.get('name')}")
        outcome = await call_next(data)
        if outcome.blocked:
            return outcome.decision, outcome
        return StageDecision.allow(stage_id="chain-stage"), outcome.result

    chain.add_stage("chain-stage", chain_stage)

    async def noop_handler(data):
        return data

    chain.set_handler(noop_handler)

    port = _StubPort()
    worker = ToolWorker(
        port=port,
        pre_tool_hook=_Legacy(),
        middleware_chains={Phase.TOOL_PRE_EXECUTE: chain},
        event_store=_fake_event_store(),
    )
    snapshot = _tool_call_payload("tc", "bash", {"command": "ls"})
    result = await worker.execute(
        node_id="n",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    # Chain ran ALLOW → tool executed → port called.
    assert "is_error" not in msg  # success path
    assert invocations_chain == ["chain:bash"]
    assert invocations == []  # legacy hook NOT invoked
    assert port.calls == [("bash", {"command": "ls"})]


@pytest.mark.asyncio
async def test_chain_block_short_circuits_tool_execution():
    """A BLOCK in the pre-tool chain prevents the tool from reaching the port."""
    chain = Chain(phase=Phase.TOOL_PRE_EXECUTE)

    async def blocker(data, call_next):
        return StageDecision.block(stage_id="chain-blocker", reason="policy"), None

    chain.add_stage("chain-blocker", blocker)

    async def noop_handler(data):
        return data

    chain.set_handler(noop_handler)

    port = _StubPort()
    worker = ToolWorker(
        port=port,
        middleware_chains={Phase.TOOL_PRE_EXECUTE: chain},
        event_store=_fake_event_store(),
    )
    snapshot = _tool_call_payload("tc", "bash", {"command": "rm -rf /"})
    result = await worker.execute(
        node_id="n",
        node_config={},
        channel_snapshot=snapshot,
        execution_context={"session_id": uuid.uuid4(), "superstep": 0},
    )
    msg = result.channel_updates["messages"][0]
    assert msg["is_error"] is True
    assert "policy" in msg["content"]
    assert port.calls == []  # tool never executed


@pytest.mark.asyncio
async def test_build_tool_chain_wraps_legacy_hook_as_single_stage():
    """``build_tool_chain`` produces chains equivalent to the legacy single-hook slots."""

    class _P(PreToolHook):
        matcher = None

        async def on_pre_tool_call(self, name, arguments, context):
            return GuardrailResult(action=GuardrailAction.ALLOW)

    chains = build_tool_chain(pre_hook=_P(), post_hook=None)
    assert Phase.TOOL_PRE_EXECUTE in chains
    assert Phase.TOOL_RESULT in chains
    assert len(chains[Phase.TOOL_PRE_EXECUTE].stages) == 1
    assert chains[Phase.TOOL_PRE_EXECUTE].stages[0].stage_id == "pre-tool"
