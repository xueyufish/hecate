"""Engine-convergence wiring and consistency tests (chat-loop-engine-convergence).

Covers the unified-chat-execution capability: guardrail-bundle parity on the
engine ToolWorker, tool-event emission through the chat graph (the events
HTTP / MCP / workflow all share), silent-first-iteration streaming, and the
HTTP fork honoring CHAT_TOOL_LOOP_ENGINE_ENABLED.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.tool_access import ToolAccessPolicy
from hecate.runtime.workers.tool_worker import ToolWorker
from hecate.studio.workflows.execution_service import WorkflowExecutionService


class _StubPort:
    """Port stub supporting both LLM streaming shapes and tool execution."""

    def __init__(self, *, llm_turns: list[dict] | None = None) -> None:
        # llm_turns: scripted responses for successive llm_invoke_structured
        # calls; each turn is {"content": str, "tool_calls": list | None}.
        self.llm_turns = llm_turns or []
        self.turn_index = 0
        self.tool_calls: list[tuple[str, dict]] = []
        self.event_store: InMemoryEventStore | None = None

    async def llm_invoke_structured(self, messages, config):
        turn = self.llm_turns[min(self.turn_index, len(self.llm_turns) - 1)]
        self.turn_index += 1
        yield {"content": turn.get("content"), "tool_calls": None}
        if turn.get("tool_calls"):
            yield {"content": None, "tool_calls": turn["tool_calls"]}

    async def tool_execute(self, name, args, context=None):
        self.tool_calls.append((name, args))
        return {"executed": True, "name": name}

    async def context_assemble(self, messages, tools=None, session_id="", model=""):
        return {"messages": messages, "tools": tools}

    async def create_span(self, *args, **kwargs):
        class _CM:
            span_id = "span"

            async def __aenter__(self_inner):  # noqa: N805
                return self_inner

            async def __aexit__(self_inner, *a):  # noqa: N805
                return None

        return _CM()

    async def end_span(self, *args, **kwargs):
        return None


def _assistant_tool_call(call_id: str, name: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"query": "x"})},
    }


def _tool_defs(name: str) -> list[dict]:
    return [{"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}]


def _initial_input() -> dict:
    return {"messages": [{"role": "user", "content": "go"}], "_session_id": None}


# ---------------------------------------------------------------------------
# Guardrail bundle parity on the engine ToolWorker
# ---------------------------------------------------------------------------


def test_engine_tool_worker_receives_full_guardrail_bundle():
    """middleware_chains / denial_tracker / event_store reach the ToolWorker
    the engine builds — gating parity with the direct loop."""
    store = InMemoryEventStore()
    chains = {"tool_pre_execute": MagicMock()}
    tracker = MagicMock()

    svc = WorkflowExecutionService(
        port=_StubPort(),
        db=MagicMock(),
        event_store=store,
        access_policy=ToolAccessPolicy(),
        middleware_chains=chains,
        denial_tracker=tracker,
    )
    composite = svc._create_composite_worker(tools=[])

    engine_tool_worker = composite._workers["tool_call"] if hasattr(composite, "_workers") else None
    if engine_tool_worker is None:
        # CompositeWorker internals: locate the ToolWorker by type.
        candidates = [v for v in vars(composite).values() if isinstance(v, ToolWorker)]
        assert candidates, "composite worker must own a ToolWorker"
        engine_tool_worker = candidates[0]

    assert engine_tool_worker._middleware_chains is chains
    assert engine_tool_worker._denial_tracker is tracker
    assert engine_tool_worker._event_store is store


# ---------------------------------------------------------------------------
# Tool events through the engine chat graph (shared by HTTP/MCP/workflow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_chat_graph_emits_paired_tool_events():
    """A tool round-trip through the engine produces TOOL_CALL → TOOL_RESULT
    with matching execution_id — the same event sequence every entry point
    shares (consistency is a property of the shared runtime path)."""
    store = InMemoryEventStore()
    session_id = uuid.uuid4()
    port = _StubPort(
        llm_turns=[
            {"content": None, "tool_calls": [_assistant_tool_call("call-1", "web_search")]},
            {"content": "final answer", "tool_calls": None},
        ]
    )
    port.event_store = store

    tool_defs = _tool_defs("web_search")
    tool_worker = ToolWorker(port=port, event_store=store)
    # Drive the worker the way the engine graph does: channel snapshot with
    # an assistant tool_calls message, then the loop continuation.
    exec_ctx = {"session_id": session_id, "superstep": 0, "trace_id": None}
    snapshot = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [_assistant_tool_call("call-1", "web_search")]},
        ]
    }
    await tool_worker.execute("tool_call", {"tools": tool_defs}, snapshot, exec_ctx)

    events = await store.get_events(session_id)
    tool_calls = [e for e in events if e.event_type is EventType.TOOL_CALL]
    tool_results = [e for e in events if e.event_type is EventType.TOOL_RESULT]
    assert len(tool_calls) == 1 and len(tool_results) == 1
    assert tool_calls[0].payload["execution_id"] == tool_results[0].payload["execution_id"]
    assert tool_results[0].payload["status"] == "succeeded"


# ---------------------------------------------------------------------------
# Silent first iteration (streaming semantics)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_tool_iteration_streams_nothing():
    """With tools configured and no tool results in the channel, the LLM's
    intermediate text must not stream — only the follow-up answer does."""
    from hecate.runtime.workers.llm_worker import LLMWorker

    turns = [
        {"content": "intermediate thinking", "tool_calls": [_assistant_tool_call("call-1", "web_search")]},
        {"content": "final answer", "tool_calls": None},
    ]
    port = _StubPort(llm_turns=turns)
    worker = LLMWorker(port=port)

    # Iteration 1: no tool messages in the channel → silent.
    streamed_first: list[str] = []
    async for item in worker.execute_stream(
        "llm",
        {"tools": _tool_defs("web_search")},
        {"messages": [{"role": "user", "content": "go"}]},
    ):
        if isinstance(item, dict) and item.get("content"):
            streamed_first.append(item["content"])
    assert streamed_first == [], "first tool iteration must stream nothing"

    # Iteration 2: tool results present → streams live.
    streamed_second: list[str] = []
    snapshot2 = {
        "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [_assistant_tool_call("call-1", "web_search")]},
            {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        ]
    }
    async for item in worker.execute_stream("llm", {"tools": _tool_defs("web_search")}, snapshot2):
        if isinstance(item, dict) and item.get("content"):
            streamed_second.append(item["content"])
    assert "".join(streamed_second) == "final answer"


# ---------------------------------------------------------------------------
# HTTP fork honors the flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_fork_routes_by_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag off → direct loop; flag on → WorkflowExecutionService. The fork
    is the single convergence point in the HTTP layer."""
    from hecate.core.config import settings

    # _process_chat needs: agent with tools, no kb/enhanced. Build minimal
    # fakes so the function runs to the fork.
    agent = MagicMock()
    agent.id = uuid.uuid4()
    agent.workspace_id = uuid.uuid4()
    agent.persona = None
    agent.model_config_db = {"model": "gpt-4o"}
    agent.tools = ["web_search"]
    agent.guardrail_config = None
    agent._resolved_ref_manifest = None

    from hecate.channel.api.v1.chat import ChatCompletionRequest, _process_chat

    request = ChatCompletionRequest(
        model="gpt-4o",
        messages=[{"role": "user", "content": "go"}],
    )

    db = MagicMock()

    def _bundle(**kwargs):
        b = MagicMock()
        b.access_policy = None
        b.rules = []
        b.approval_callback = None
        b.middleware_chains = {}
        b.denial_tracker = None
        return b

    with (
        patch("hecate.channel.api.v1.chat._load_agent_tools", new=AsyncMock(return_value=_tool_defs("web_search"))),
        patch("hecate.channel.api.v1.chat._build_tool_registry", return_value=MagicMock()),
        patch("hecate.channel.api.v1.chat._get_provider_config", new=AsyncMock(return_value={})),
        patch(
            "hecate.runtime.security.guardrail_assembly.assemble_guardrails",
            new=AsyncMock(side_effect=lambda *a, **kw: _bundle()),
        ),
    ):
        # Flag off: direct loop is invoked.
        monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", False)
        direct_mock = AsyncMock(
            return_value=MagicMock(
                model="gpt-4o",
                content="direct",
                tool_calls=None,
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
        )
        with patch("hecate.channel.api.v1.chat._chat_with_tools", new=direct_mock):
            result_off = await _process_chat(request, db, uuid.uuid4(), agent.workspace_id, preloaded_agent=agent)
            assert result_off["choices"][0]["message"]["content"] == "direct"
            assert direct_mock.await_count == 1

        # Flag on: engine service is constructed and executed.
        monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)
        with patch("hecate.studio.workflows.execution_service.WorkflowExecutionService") as mock_service_cls:
            mock_service = MagicMock()
            mock_service.execute = AsyncMock(
                return_value={"content": "engine", "model": "gpt-4o", "finish_reason": "stop"}
            )
            mock_service_cls.return_value = mock_service
            with patch("hecate.core.composition.runtime_port_adapter.create_runtime_port", return_value=MagicMock()):
                await _process_chat(request, db, uuid.uuid4(), agent.workspace_id, preloaded_agent=agent)
                assert mock_service.execute.await_count == 1
                _, execute_kwargs = mock_service.execute.call_args
                assert execute_kwargs["tools"]  # agent tools reached the engine
                # bundle reached the service constructor
                _, ctor_kwargs = mock_service_cls.call_args
                assert "middleware_chains" in ctor_kwargs and "denial_tracker" in ctor_kwargs
