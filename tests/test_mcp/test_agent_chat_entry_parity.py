"""MCP entry parity: agent_chat through the shared entry helper (step5d).

The MCP ``agent_chat`` tool and the HTTP chat entry both delegate to the
same platform entry execution service. This test drives the MCP helper
directly (the MCP transport auth context is a transport concern covered
by the auth middleware tests) and asserts engine-event parity with the
HTTP entry: paired TOOL_CALL/TOOL_RESULT in the process event store and
Task/Run correlation — the properties the bare
``WorkflowExecutionService(port, db)`` wiring used to lose.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.execution.task_run_registry import TaskNotFoundError, TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.organization import OrganizationModel
from hecate.models.session import SessionModel
from hecate.models.task import TaskModel
from hecate.models.tool import ToolModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceRole
from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.session_state import InMemorySessionStateStore

TOOL_NAME = "stub_calculator"
ZERO_WS = uuid.UUID(int=0)


class _StubLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    def _saw_tool_result(self, messages: list[dict]) -> bool:
        return any(m.get("role") == "tool" for m in messages)

    async def chat(self, *, messages, model=None, tools=None, **_):
        self.calls.append(list(messages))
        if self._saw_tool_result(messages):
            return SimpleNamespace(content="done", model=model, tool_calls=None, finish_reason="stop", usage={})
        return SimpleNamespace(
            content="",
            model=model,
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": TOOL_NAME, "arguments": '{"a": 1, "b": 2}'},
                }
            ],
            finish_reason="tool_calls",
            usage={},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_):
        self.calls.append(list(messages))
        if self._saw_tool_result(messages):
            yield {"content": "done", "tool_calls": None}
            yield {"content": "", "finish_reason": "stop", "tool_calls": None}
        else:
            yield {
                "content": "",
                "tool_calls": [
                    SimpleNamespace(
                        index=0,
                        id="call_1",
                        type="function",
                        function=SimpleNamespace(name=TOOL_NAME, arguments='{"a": 1, "b": 2}'),
                    )
                ],
            }
            yield {"content": None, "tool_calls": None}


class _StubExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, name, args, context=None):
        self.calls.append((name, dict(args or {})))
        return "3"


@pytest.fixture
async def mcp_env(monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession, auth_context: AuthContext):
    """Deterministic provider/tool boundary + process-wide stores + auth."""
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_AUTH_TYPE", "none")

    import hecate.core.composition.entry_assembly as entry_assembly
    import hecate.tools.mcp.server as mcp_server

    stub_llm = _StubLLM()
    stub_executor = _StubExecutor()

    event_store = InMemoryEventStore()
    state_store = InMemorySessionStateStore()
    monkeypatch.setattr(entry_assembly, "_shared_event_store", event_store)
    monkeypatch.setattr(entry_assembly, "_shared_session_state_store", state_store)

    from hecate.tools.tool.registry import ToolRegistry

    def _build(db, skill_ref_manifest=None, *, workspace_id=None):
        registry = ToolRegistry(db=db, builtin_executor=stub_executor, workspace_id=workspace_id)
        registry._builtin_names = {TOOL_NAME}
        return registry

    def _fake_auth():
        return auth_context

    agent = AgentModel(
        workspace_id=ZERO_WS,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        tools=[TOOL_NAME],
        model_config_db={"model": "stub-model"},
    )
    db_session.add(agent)
    await db_session.flush()
    # Default builtin deployment so the entry correlation opens a run.
    from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
    from hecate.models.agent_principal import AgentPrincipalModel
    from hecate.models.agent_version import AgentVersionModel
    from hecate.models.user import UserModel

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
            workspace_id=ZERO_WS,
            organization_id=ZERO_WS,
            owner_user_id=user.id,
        )
    )
    await db_session.flush()
    db_session.add(
        AgentDeploymentModel(
            agent_id=agent.id,
            agent_version_id=version.id,
            workspace_id=ZERO_WS,
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
    db_session.add(
        ToolModel(
            name=TOOL_NAME,
            description="stub",
            parameters={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            source="builtin",
        )
    )
    db_session.add(SessionModel(id=uuid.uuid4(), agent_id=agent.id, status="active", workspace_id=ZERO_WS))
    # The bundled zero workspace row backs registry workspace checks.
    if await db_session.get(OrganizationModel, ZERO_WS) is None:
        db_session.add(OrganizationModel(id=ZERO_WS, name="zero-org"))
    await db_session.flush()
    if await db_session.get(WorkspaceModel, ZERO_WS) is None:
        db_session.add(WorkspaceModel(id=ZERO_WS, org_id=ZERO_WS, name="bundled", slug="bundled"))
        await db_session.flush()

    monkeypatch.setattr(mcp_server, "_auth", _fake_auth)
    monkeypatch.setattr(entry_assembly, "build_tool_registry", _build)
    return SimpleNamespace(
        llm=stub_llm,
        executor=stub_executor,
        event_store=event_store,
        state_store=state_store,
        agent=agent,
    )


async def test_mcp_agent_chat_produces_engine_events_and_correlation(mcp_env, db_session: AsyncSession) -> None:
    """Parity with HTTP: paired tool events, single execution, Task/Run rows."""
    import hecate.tools.mcp.server as mcp_server

    session = (
        (await db_session.execute(select(SessionModel).where(SessionModel.agent_id == mcp_env.agent.id)))
        .scalars()
        .first()
    )
    session_id = str(session.id)

    with patch("hecate_llm.service.llm_service", mcp_env.llm):
        result_json = await mcp_server._chat_via_entry(
            db_session,
            session_id=session_id,
            message="add 1 and 2",
            agent=mcp_env.agent,
            ctx=AuthContext(
                user_id=uuid.UUID(int=1),
                org_id=ZERO_WS,
                workspace_id=ZERO_WS,
                role=WorkspaceRole.ADMIN,
                auth_method="api_key",
                api_key_scope=None,
            ),
        )

    result = json.loads(result_json)
    assert "error" not in result, result
    assert result["response"] == "done"

    # Engine parity: paired tool receipts landed in the process event store
    # (the bare port+db wiring produced no events at all).
    events = [event for events in mcp_env.event_store._store.values() for event in events]
    kinds = [event.event_type for event in events]
    assert EventType.TOOL_CALL in kinds
    assert EventType.TOOL_RESULT in kinds
    # The stub tool ran exactly once — no replay.
    assert len(mcp_env.executor.calls) == 1

    # Correlation parity: a task and a run recorded for this execution.
    registry = TaskRunRegistry(db_session)
    tasks = (await db_session.execute(select(TaskModel))).scalars().all()
    assert tasks, "MCP entry recorded a task"
    run_found = False
    for task in tasks:
        try:
            runs = await registry.list_runs_for_task(task.id, task.workspace_id)
        except TaskNotFoundError:
            continue
        if runs:
            run_found = True
    assert run_found, "MCP entry opened a run"
