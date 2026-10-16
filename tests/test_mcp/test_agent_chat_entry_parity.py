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

import asyncio
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
from tests.test_channel.test_g3_real_tcp_disconnect import (
    PG_URL,
    entry_stack,  # noqa: F401 - shared acceptance fixture
)

TOOL_NAME = "stub_calculator"
ZERO_WS = uuid.UUID(int=0)


class _StubLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.rounds = 1
        self.hold_answer = False
        self.answer_entered = asyncio.Event()
        self.release_answer = asyncio.Event()

    def _saw_tool_result(self, messages: list[dict]) -> bool:
        return sum(m.get("role") == "tool" for m in messages) >= self.rounds

    async def _answer_gate(self, messages):
        if self.hold_answer and self._saw_tool_result(messages):
            self.answer_entered.set()
            await self.release_answer.wait()

    async def chat(self, *, messages, model=None, tools=None, **_):
        await self._answer_gate(messages)
        self.calls.append(list(messages))
        if self._saw_tool_result(messages):
            return SimpleNamespace(content="done", model=model, tool_calls=None, finish_reason="stop", usage={})
        return SimpleNamespace(
            content="",
            model=model,
            tool_calls=[
                {
                    "id": f"call_{1 + sum(m.get('role') == 'tool' for m in messages)}",
                    "type": "function",
                    "function": {"name": TOOL_NAME, "arguments": '{"a": 1, "b": 2}'},
                }
            ],
            finish_reason="tool_calls",
            usage={},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **_):
        await self._answer_gate(messages)
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
                        id=f"call_{1 + sum(m.get('role') == 'tool' for m in messages)}",
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

    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS
    from hecate.tools.tool.registry import ToolRegistry

    monkeypatch.setitem(
        BUILTIN_TOOL_DEFINITIONS,
        TOOL_NAME,
        {
            "risk_level": "LOW",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
            "returns": {"type": "string"},
        },
    )

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


async def test_mcp_agent_chat_multi_turn_resumes_real_receipt(mcp_env, db_session: AsyncSession) -> None:
    """G3 engine-path evidence via the MCP entry: a second turn on the same
    session continues the conversation with the FIRST turn's tool receipt
    backfilled as real content (not the reconciliation placeholder), and
    the succeeded tool is not re-executed."""
    import hecate.tools.mcp.server as mcp_server

    session = (
        (await db_session.execute(select(SessionModel).where(SessionModel.agent_id == mcp_env.agent.id)))
        .scalars()
        .first()
    )
    session_id = str(session.id)
    ctx = AuthContext(
        user_id=uuid.UUID(int=1),
        org_id=ZERO_WS,
        workspace_id=ZERO_WS,
        role=WorkspaceRole.ADMIN,
        auth_method="api_key",
        api_key_scope=None,
    )

    with patch("hecate_llm.service.llm_service", mcp_env.llm):
        first = json.loads(
            await mcp_server._chat_via_entry(
                db_session, session_id=session_id, message="add 1 and 2", agent=mcp_env.agent, ctx=ctx
            )
        )
        assert "error" not in first, first
        assert len(mcp_env.executor.calls) == 1
        first_tool_content = mcp_env.executor.calls[0]

        second = json.loads(
            await mcp_server._chat_via_entry(
                db_session, session_id=session_id, message="and again", agent=mcp_env.agent, ctx=ctx
            )
        )

    assert "error" not in second, second
    assert second["response"] == "done"
    # Replay protection: the first turn's succeeded tool never re-ran.
    assert len(mcp_env.executor.calls) == 1
    # The second turn's model input carried the first turn's tool receipt
    # with REAL content — the log backfill, not the placeholder marker.
    second_model_messages = mcp_env.llm.calls[-1]
    tool_msgs = [m for m in second_model_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert tool_msgs, "second turn saw the first turn's tool receipt"
    assert "reconciliation" not in json.dumps(tool_msgs), tool_msgs
    assert (
        str(first_tool_content[1].get("a") + first_tool_content[1].get("b")) in tool_msgs[-1].get("content", "")
        or tool_msgs[-1].get("content") == "3"
    )


@pytest.fixture
async def mcp_client(mcp_env, entry_stack, monkeypatch):  # noqa: F811 - pytest fixture re-export
    """Exercise the registered MCP tool through an initialized protocol session."""
    from fastmcp import Client

    import hecate.tools.mcp.server as server_module
    from tests.test_channel.test_chat_engine_g3_entry import _agent_with_tool
    from tests.test_execution.test_entry_contract_pg_e2e import _seed_workspace_parents

    await _seed_workspace_parents(entry_stack.server_factory)
    async with entry_stack.server_factory() as db:
        agent = await _agent_with_tool(db, ZERO_WS, with_deployment=True)
        await db.commit()
    mcp_env.agent = agent
    mcp_env.event_store = entry_stack.reader_store
    mcp_env.state_store = entry_stack.session_state
    mcp_env.stack = entry_stack
    monkeypatch.setattr(server_module, "async_session_factory", entry_stack.server_factory)
    monkeypatch.setattr(
        server_module,
        "_auth",
        lambda: AuthContext(
            user_id=uuid.UUID(int=1),
            org_id=ZERO_WS,
            workspace_id=ZERO_WS,
            role=WorkspaceRole.ADMIN,
            auth_method="api_key",
            api_key_scope=None,
        ),
    )
    monkeypatch.setattr("hecate_llm.service.llm_service", mcp_env.llm)
    async with Client(server_module.create_mcp_server(gateway_enabled=False)) as client:
        created = await client.call_tool("session_create", {"agent_id": str(agent.id)})
        session_id = json.loads(created.content[0].text)["session_id"]
        yield client, session_id


async def _protocol_chat(client, session_id, message="add 1 and 2"):
    result = await client.call_tool("agent_chat", {"session_id": session_id, "message": message})
    return json.loads(result.content[0].text)


def _assert_tool_pairs(events, call_ids):
    calls = [e.payload["tool_call_id"] for e in events if e.event_type is EventType.TOOL_CALL]
    results = [e.payload["tool_call_id"] for e in events if e.event_type is EventType.TOOL_RESULT]
    assert calls == results == call_ids


async def test_mcp_protocol_multi_round_and_receipt_resume(mcp_client, mcp_env):
    client, session_id = mcp_client
    mcp_env.llm.rounds = 2
    assert (await _protocol_chat(client, session_id))["response"] == "done"
    assert len(mcp_env.executor.calls) == 2
    _assert_tool_pairs(await mcp_env.event_store.get_events(uuid.UUID(session_id)), ["call_1", "call_2"])
    assert (await _protocol_chat(client, session_id, "continue"))["response"] == "done"
    assert len(mcp_env.executor.calls) == 2
    receipts = [m for m in mcp_env.llm.calls[-1] if m.get("role") == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in receipts] == [("call_1", "3"), ("call_2", "3")]


@pytest.mark.parametrize("action", ["deny", "ask"])
@pytest.mark.skipif(not PG_URL, reason="MCP denial audit plus independent terminal writes require PostgreSQL")
async def test_mcp_protocol_denial_has_no_business_dispatch(mcp_client, mcp_env, action):
    from hecate.models.run import RunModel
    from hecate.models.tool_policy import ToolPolicyRuleModel

    async with mcp_env.stack.server_factory() as db:
        db.add(ToolPolicyRuleModel(workspace_id=ZERO_WS, tool_pattern=TOOL_NAME, action=action))
        await db.commit()
    client, session_id = mcp_client
    result = await _protocol_chat(client, session_id)
    assert result["response"] == "done"
    assert not mcp_env.executor.calls
    receipts = [m for m in mcp_env.llm.calls[-1] if m.get("role") == "tool"]
    assert receipts and receipts[-1].get("is_error")
    assert any(word in receipts[-1]["content"].lower() for word in ("denied", "rejected", "approval", "policy")), (
        receipts
    )
    events = await mcp_env.event_store.get_events(uuid.UUID(session_id))
    kinds = [event.event_type for event in events]
    assert (EventType.APPROVAL_ASKED in kinds) == (action == "ask")
    assert (EventType.APPROVAL_DECIDED in kinds) == (action == "ask")
    if action == "ask":
        decisions = [event.payload for event in events if event.event_type is EventType.APPROVAL_DECIDED]
        assert len(decisions) == 1 and decisions[0]["approved"] is False
    assert any(
        event.event_type is EventType.CHANNEL_WRITE
        and event.payload.get("channel") == "messages"
        and any(message == receipts[-1] for message in event.payload.get("value", []))
        for event in events
    ), "denial receipt must be queryable from the persistent session log"
    async with mcp_env.stack.reader_factory() as db:
        runs = (await db.execute(select(RunModel))).scalars().all()
    assert len(runs) == 1 and runs[0].projection["state"] == "succeeded"


async def test_mcp_protocol_cancel_and_same_session_recovery(mcp_client, mcp_env):
    from hecate.contracts.execution.events import EventKind
    from hecate.contracts.execution.references import run_ref, task_ref
    from hecate.execution.entry_events import RunEventMapper
    from hecate.models.run import RunModel

    client, session_id = mcp_client
    provider = mcp_env.llm
    provider.hold_answer = True
    pending = asyncio.create_task(_protocol_chat(client, session_id))
    await asyncio.wait_for(provider.answer_entered.wait(), 10)
    assert len(mcp_env.executor.calls) == 1
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    for _ in range(100):
        async with mcp_env.stack.reader_factory() as db:
            runs = (await db.execute(select(RunModel))).scalars().all()
        if runs and runs[0].projection.get("state") == "cancelled":
            break
        await asyncio.sleep(0.05)
    assert runs[0].projection["state"] == "cancelled"
    assert runs[0].projection["error_category"] == "cancelled"
    mapper = RunEventMapper(
        task_ref("hecate", str(runs[0].task_id)), run_ref("hecate", str(runs[0].id)), event_store=mcp_env.event_store
    )
    page = await mapper.read_events(limit=1000)
    terminals = [event for event in page.events if event.kind is EventKind.TERMINAL]
    assert len(terminals) == 1 and terminals[0].payload["stop_category"] == "cancelled"
    provider.hold_answer = False
    provider.release_answer.set()
    assert (await _protocol_chat(client, session_id, "continue"))["response"] == "done"
    assert len(mcp_env.executor.calls) == 1
    receipts = [m for m in provider.calls[-1] if m.get("role") == "tool"]
    assert receipts[-1]["content"] == "3"
