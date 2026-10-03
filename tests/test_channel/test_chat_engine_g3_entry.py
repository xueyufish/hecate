"""G3 real-entry evidence: engine chat path driven through the HTTP entry.

These tests are the step5d G3 gate evidence (``unified-chat-execution``
spec, "引擎路径具备真实入口证据"): the HTTP endpoint runs end to end with
the real execution service, Pregel chat graph, guardrail assembly, event
store, and tool worker. Only the provider boundary is stubbed — a
deterministic in-process LLM double and a stub tool executor. The
execution service itself is never patched.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.main import app
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.feature_flag import FeatureFlagModel
from hecate.models.tool import ToolModel
from hecate.models.user import UserModel

TOOL_NAME = "stub_calculator"
TOOL_ARGS = '{"a": 2, "b": 3}'
TOOL_RESULT = "5"


class StubLLMService:
    """Deterministic LLM double at the provider boundary.

    First call proposes a tool call; once a tool result is present the
    call returns the final answer. Streaming mirrors the same script.
    """

    def __init__(self) -> None:
        self.chat_calls: list[list[dict]] = []
        self.stream_calls: list[list[dict]] = []

    def _saw_tool_result(self, messages: list[dict]) -> bool:
        return any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        self.chat_calls.append(list(messages))
        if self._saw_tool_result(messages):
            return SimpleNamespace(
                content=f"The answer is {TOOL_RESULT}",
                model=model or "stub-model",
                tool_calls=None,
                finish_reason="stop",
                usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            )
        return SimpleNamespace(
            content="",
            model=model or "stub-model",
            tool_calls=[_tool_call_delta("call_1")],
            finish_reason="tool_calls",
            usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_kwargs):
        self.stream_calls.append(list(messages))
        if self._saw_tool_result(messages):
            for token in ("The answer", f" is {TOOL_RESULT}"):
                yield {"content": token, "tool_calls": None}
            yield {"content": "", "finish_reason": "stop", "tool_calls": None}
        else:
            yield {"content": "", "tool_calls": [_stream_tool_delta("call_1")]}
            yield {"content": None, "tool_calls": None}


class StubToolExecutor:
    """Deterministic builtin executor recording its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, args, context=None):
        self.calls.append((name, dict(args or {})))
        return TOOL_RESULT


def _tool_call_delta(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": TOOL_NAME, "arguments": TOOL_ARGS},
    }


def _stream_tool_delta(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        index=0,
        id=call_id,
        type="function",
        function=SimpleNamespace(name=TOOL_NAME, arguments=TOOL_ARGS),
    )


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> StubLLMService:
    stub = StubLLMService()
    import hecate.channel.api.v1.chat as chat_module

    monkeypatch.setattr(chat_module, "llm_service", stub)
    return stub


@pytest.fixture
def tool_executor(monkeypatch: pytest.MonkeyPatch) -> StubToolExecutor:
    """Swap the builtin executor behind the real ToolRegistry."""
    from hecate.tools.tool.registry import ToolRegistry

    executor = StubToolExecutor()

    def _build(db, skill_ref_manifest=None):
        registry = ToolRegistry(db=db, builtin_executor=executor)
        registry._builtin_names = {TOOL_NAME}
        return registry

    import hecate.channel.api.v1.chat as chat_module

    monkeypatch.setattr(chat_module, "_build_tool_registry", _build)
    return executor


@pytest.fixture(autouse=True)
def engine_path(monkeypatch: pytest.MonkeyPatch):
    """Global flag on; no flag row → every workspace resolves to engine."""
    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)
    yield


@pytest.fixture(autouse=True)
def wired_stores():
    """Pin the event/checkpoint store singletons the HTTP path resolves."""
    from hecate.runtime.eventstore import InMemoryEventStore
    from hecate.runtime.session_state import InMemorySessionStateStore

    app.state.event_store = InMemoryEventStore()
    app.state.session_state_store = InMemorySessionStateStore()
    yield
    for attr in ("event_store", "session_state_store"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


async def _agent_with_tool(
    db_session: AsyncSession, workspace_id: uuid.UUID, *, with_deployment: bool = False
) -> AgentModel:
    agent = AgentModel(
        workspace_id=workspace_id,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        tools=[TOOL_NAME],
        model_config_db={"model": "stub-model"},
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add(
        ToolModel(
            name=TOOL_NAME,
            description="Deterministic stub calculator",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
            source="builtin",
        )
    )
    if with_deployment:
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub-model"}, content_hash="a" * 64
        )
        db_session.add(version)
        await db_session.flush()
        user = UserModel(email=f"owner-{agent.id}@example.com", hashed_password=uuid.uuid4().hex)
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=workspace_id,
                organization_id=workspace_id,
                owner_user_id=user.id,
            )
        )
        await db_session.flush()
        db_session.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=workspace_id,
                backend_type=BackendType.BUILTIN,
                access_mode=AccessMode.IN_PROCESS,
                issuer_domain="hecate",
                is_default=True,
                capability_snapshot={},
                axes_harness="hecate",
                axes_environment="none",
                axes_tool_execution="hecate_gateway",
            )
        )
        await db_session.flush()
    await db_session.flush()
    return agent


def _parse_sse(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line.removeprefix("data: ")))
    return events


def _tool_events(store):
    from hecate_runtime.eventstore import EventType

    return [
        event
        for session_events in store._store.values()
        for event in session_events
        if event.event_type in (EventType.TOOL_CALL, EventType.TOOL_RESULT)
    ]


# --- G3 6.1: real HTTP streaming / non-streaming multi-round ----------------


async def test_real_http_non_streaming_multi_round_tool_call(client, db_session, llm, tool_executor):
    agent = await _agent_with_tool(db_session, uuid.UUID(int=0), with_deployment=True)
    response = await client.post(
        f"/v1/agents/{agent.id}/chat/completions",
        json={"messages": [{"role": "user", "content": "add 2 and 3"}]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["choices"][0]["message"]["content"] == f"The answer is {TOOL_RESULT}"
    assert data["choices"][0]["finish_reason"] == "stop"
    # Two LLM rounds: proposal then final answer; the stub tool ran once.
    # The engine's tool-loop rounds may flow through the structured stream
    # invocation, so count both provider channels.
    total_llm_calls = len(llm.chat_calls) + len(llm.stream_calls)
    assert total_llm_calls == 2
    assert tool_executor.calls == [(TOOL_NAME, {"a": 2, "b": 3})]
    # The engine log has the paired receipt events (real ToolWorker).
    paired = _tool_events(app.state.event_store)
    kinds = [event.event_type.value for event in paired]
    assert "TOOL_CALL" in kinds and "TOOL_RESULT" in kinds


async def test_real_http_streaming_multi_round_silent_intermediates(client, db_session, llm, tool_executor):
    agent = await _agent_with_tool(db_session, uuid.UUID(int=0))
    response = await client.post(
        f"/v1/agents/{agent.id}/chat/completions",
        json={"messages": [{"role": "user", "content": "add 2 and 3"}], "stream": True},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    chunks = _parse_sse(response.text)
    deltas = [c["choices"][0]["delta"].get("content") for c in chunks if c["choices"][0].get("delta")]
    content = "".join(d for d in deltas if d)
    # Only the final answer streams; intermediate tool iteration is silent.
    assert content == f"The answer is {TOOL_RESULT}"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    # Engine-side pairing still happened for the streamed turn.
    kinds = [event.event_type.value for event in _tool_events(app.state.event_store)]
    assert "TOOL_CALL" in kinds and "TOOL_RESULT" in kinds


# --- G3 6.2: approval denial through the real entry --------------------------


async def test_real_http_approval_denial_surfaces(client, db_session, llm, tool_executor):
    from hecate.models.tool_policy import ToolPolicyRuleModel

    agent = await _agent_with_tool(db_session, uuid.UUID(int=0))
    db_session.add(
        ToolPolicyRuleModel(
            workspace_id=uuid.UUID(int=0),
            agent_id=None,
            tool_pattern=TOOL_NAME,
            action="ask",
        )
    )
    await db_session.flush()

    session_id = str(uuid.uuid4())
    response = await client.post(
        f"/v1/agents/{agent.id}/chat/completions",
        json={
            "messages": [{"role": "user", "content": "add 2 and 3"}],
            "session_id": session_id,
        },
    )
    assert response.status_code == 200
    # The tool never executed; the final LLM round received the denial as
    # the tool result (the stub answer text itself is scripted).
    assert tool_executor.calls == []
    final_messages = llm.chat_calls[-1] if llm.chat_calls else llm.stream_calls[-1]
    tool_messages = [m for m in final_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert tool_messages, "denied dispatch still closes the loop with a tool message"
    denial_text = " ".join(str(m.get("content", "")) for m in tool_messages).lower()
    assert "approval" in denial_text or "denied" in denial_text or "rejected" in denial_text
    # The durable approval pair was recorded on the engine path.
    from hecate_runtime.eventstore import EventType

    all_events = [e for events in app.state.event_store._store.values() for e in events]
    kinds = [e.event_type for e in all_events]
    assert EventType.APPROVAL_ASKED in kinds
    assert EventType.APPROVAL_DECIDED in kinds


# --- G3 6.3: session affinity keeps the established path ---------------------


async def test_session_keeps_engine_path_after_override_flip(client, db_session, llm, tool_executor):
    agent = await _agent_with_tool(db_session, uuid.UUID(int=0))
    session_id = str(uuid.uuid4())

    first = await client.post(
        f"/v1/agents/{agent.id}/chat/completions",
        json={
            "messages": [{"role": "user", "content": "turn one"}],
            "session_id": session_id,
        },
    )
    assert first.status_code == 200
    assert tool_executor.calls, "first turn ran on the engine path"

    # Flip: workspace dropped from the engine rollout surface.
    db_session.add(
        FeatureFlagModel(
            key="chat_tool_loop_engine_enabled",
            status="active",
            enabled=True,
            targeting_rules={"tenant_allowlist": [str(uuid.uuid4())]},
        )
    )
    await db_session.flush()

    second = await client.post(
        f"/v1/agents/{agent.id}/chat/completions",
        json={
            "messages": [{"role": "user", "content": "turn two"}],
            "session_id": session_id,
        },
    )
    assert second.status_code == 200
    # Session affinity: the continuation kept the engine path even though
    # the workspace is no longer targeted — the direct loop (which never
    # writes engine events or runs the real tool worker) did not serve it.
    assert len(tool_executor.calls) == 2, (
        "turn two must execute its own tool call exactly once (engine path, no replay of turn one's dispatch)"
    )
    from hecate.models.session import SessionModel

    session = await db_session.get(SessionModel, uuid.UUID(session_id))
    if session is not None:
        assert session.metadata_.get("chat_execution_path") == "engine"


# --- G3 6.5 / correlation: entries land Task/Run records ---------------------


async def test_http_entry_correlates_task_and_run(client, db_session, llm, tool_executor, default_workspace):
    agent = await _agent_with_tool(db_session, default_workspace.id, with_deployment=True)
    session_id = str(uuid.uuid4())
    response = await client.post(
        f"/v1/agents/{agent.id}/chat/completions",
        json={
            "messages": [{"role": "user", "content": "record me"}],
            "session_id": session_id,
        },
    )
    assert response.status_code == 200

    registry = TaskRunRegistry(db_session)
    from hecate.execution.task_run_registry import TaskNotFoundError

    tasks = (
        (await db_session.execute(select(__import__("hecate.models.task", fromlist=["TaskModel"]).TaskModel)))
        .scalars()
        .all()
    )
    assert tasks, "entry execution recorded a task"
    run = None
    for task in tasks:
        try:
            runs = await registry.list_runs_for_task(task.id, default_workspace.id)
        except TaskNotFoundError:
            continue
        if runs:
            run = runs[0]
            break
    assert run is not None, "entry execution opened a run"
    assert run.backend_ref["id"] == session_id
