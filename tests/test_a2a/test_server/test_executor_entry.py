"""A2A executor entry evidence: execution goes through the entry service.

The A2A SendMessage path is driven through the real handler (database
task store included) with the real entry execution service, chat graph
engine and tool worker. Only the provider boundary is stubbed — a
deterministic LLM double and a stub tool executor, mirroring the G3
HTTP entry tests. The execution service itself is never patched.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import hecate.channel.api.v1.chat as chat_module
import hecate.core.composition.im_entry as im_entry_module
from hecate.channel.a2a.server.handler import A2ARequestHandler
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.tool import ToolModel
from hecate.models.user import UserModel

TOOL_NAME = "stub_calculator"
TOOL_ARGS = '{"a": 2, "b": 3}'
TOOL_RESULT = "5"
# Protocol shape pinned from the pre-migration handler (_task_to_dict).
TASK_DICT_KEYS = {"id", "contextId", "status", "artifacts", "history", "metadata"}


class StubLLMService:
    """Deterministic LLM double at the provider boundary.

    First call proposes a tool call; once a tool result is present the
    call returns the final answer. Streaming mirrors the same script
    (the engine may route tool-loop rounds through either channel).
    """

    def __init__(self) -> None:
        self.chat_calls: list[list[dict]] = []
        self.stream_calls: list[list[dict]] = []

    def _saw_tool_result(self, messages: list[dict]) -> bool:
        return any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)

    async def chat(self, *, messages: Any, model: str | None = None, tools: Any = None, **_kwargs: Any):
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

    async def chat_stream(self, *, messages: Any, model: str | None = None, tools: Any = None, **_kwargs: Any):
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

    async def execute(self, name: str, args: Any, context: Any = None) -> str:
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
    """Swap the provider-seam llm_service the executor resolves lazily."""
    import hecate_llm.service

    stub = StubLLMService()
    monkeypatch.setattr(hecate_llm.service, "llm_service", stub)
    return stub


@pytest.fixture
def tool_executor(monkeypatch: pytest.MonkeyPatch) -> StubToolExecutor:
    """Swap the builtin executor behind the real ToolRegistry."""
    from hecate.tools.tool.registry import ToolRegistry

    executor = StubToolExecutor()

    def _build(db: Any, skill_ref_manifest: Any = None) -> ToolRegistry:
        registry = ToolRegistry(db=db, builtin_executor=executor)
        registry._builtin_names = {TOOL_NAME}
        return registry

    monkeypatch.setattr(chat_module, "_build_tool_registry", _build)
    return executor


@pytest.fixture
def shared_event_store(monkeypatch: pytest.MonkeyPatch):
    """Pin the process-level event store singleton the executor reuses."""
    from hecate.runtime.eventstore import InMemoryEventStore

    store = InMemoryEventStore()
    monkeypatch.setattr(im_entry_module, "_shared_event_store", store)
    return store


async def _agent_with_tool(
    db_session: AsyncSession, workspace_id: uuid.UUID, *, with_deployment: bool = False
) -> AgentModel:
    agent = AgentModel(
        workspace_id=workspace_id,
        name=f"a2a-agent-{uuid.uuid4().hex[:8]}",
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


def _tool_events(store: Any) -> list[Any]:
    from hecate_runtime.eventstore import EventType

    return [
        event
        for session_events in store._store.values()
        for event in session_events
        if event.event_type in (EventType.TOOL_CALL, EventType.TOOL_RESULT)
    ]


async def _send_message(db_session: AsyncSession, text: str) -> dict:
    handler = A2ARequestHandler(db_session)
    return await handler.handle_send_message({"message": {"role": "user", "parts": [{"text": text}]}})


async def test_send_message_runs_through_entry_service_with_tools(
    db_session: AsyncSession,
    default_workspace,
    llm: StubLLMService,
    tool_executor: StubToolExecutor,
    shared_event_store,
) -> None:
    """Real handler + real entry service: tools, guardrail wiring, events, correlation."""
    await _agent_with_tool(db_session, default_workspace.id, with_deployment=True)

    result = await _send_message(db_session, "add 2 and 3")

    task = result["task"]
    assert task["status"]["state"] == "completed"
    assert task["status"]["message"]["parts"][0]["text"] == f"The answer is {TOOL_RESULT}"
    assert task["artifacts"][0]["name"] == "response"
    assert task["artifacts"][0]["parts"][0]["text"] == f"The answer is {TOOL_RESULT}"
    # Tool loop ran through the real tool worker exactly once.
    assert tool_executor.calls == [(TOOL_NAME, {"a": 2, "b": 3})]
    # The engine log has the paired receipt events (real ToolWorker).
    kinds = [event.event_type.value for event in _tool_events(shared_event_store)]
    assert "TOOL_CALL" in kinds and "TOOL_RESULT" in kinds
    # Correlation: Task and Run registered under the agent's workspace.
    tasks = (
        (await db_session.execute(select(TaskModel).where(TaskModel.workspace_id == default_workspace.id)))
        .scalars()
        .all()
    )
    assert len(tasks) == 1
    runs = (
        (await db_session.execute(select(RunModel).where(RunModel.workspace_id == default_workspace.id)))
        .scalars()
        .all()
    )
    assert len(runs) == 1


async def test_send_message_protocol_shape_is_pinned(
    db_session: AsyncSession, default_workspace, llm: StubLLMService
) -> None:
    """The A2A protocol response keeps its pre-migration field set."""
    await _agent_with_tool(db_session, default_workspace.id, with_deployment=False)

    result = await _send_message(db_session, "hello")

    assert set(result["task"].keys()) == TASK_DICT_KEYS
    assert set(result["task"]["status"].keys()) == {"state", "message"}
    assert result["task"]["history"] == []  # pre-migration baseline: executor returns a fresh Task


async def test_send_message_registers_task_without_deployment(
    db_session: AsyncSession, default_workspace, llm: StubLLMService
) -> None:
    """No default deployment: the Task still registers; the run gap is explicit."""
    await _agent_with_tool(db_session, default_workspace.id, with_deployment=False)

    result = await _send_message(db_session, "hello")

    assert result["task"]["status"]["state"] == "completed"
    tasks = (
        (await db_session.execute(select(TaskModel).where(TaskModel.workspace_id == default_workspace.id)))
        .scalars()
        .all()
    )
    assert len(tasks) == 1
    runs = (
        (await db_session.execute(select(RunModel).where(RunModel.workspace_id == default_workspace.id)))
        .scalars()
        .all()
    )
    assert runs == []


async def test_no_agent_configured_fails_without_execution(
    db_session: AsyncSession, default_workspace, llm: StubLLMService
) -> None:
    result = await _send_message(db_session, "hello")

    assert result["task"]["status"]["state"] == "failed"
    assert result["task"]["status"]["message"]["parts"][0]["text"] == "No agent configured in Hecate"
    assert llm.chat_calls == []


async def test_provider_failure_maps_to_failed_task(
    db_session: AsyncSession, default_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hecate_llm.service

    class _Broken:
        async def chat(self, **_kwargs: Any):
            raise RuntimeError("llm down")

        async def chat_stream(self, **_kwargs: Any):
            raise RuntimeError("llm down")
            yield  # pragma: no cover

    monkeypatch.setattr(hecate_llm.service, "llm_service", _Broken())
    await _agent_with_tool(db_session, default_workspace.id, with_deployment=False)

    result = await _send_message(db_session, "hello")

    assert result["task"]["status"]["state"] == "failed"
    assert "Execution failed" in result["task"]["status"]["message"]["parts"][0]["text"]
