"""PostgreSQL end-to-end entry-contract acceptance (step5d completion).

The four entry-contract behaviors (sync failure terminal envelope,
registration surviving a caller rollback, declared-artifact mapping, and
terminal/projection consistency) are proven here against a real
PostgreSQL on BOTH persistence engines: the platform ORM
(Task/Run/projection rows) and the event store (envelope table) — driven
through the actual HTTP chat entry with the real execution service and
Pregel graph; only the provider boundary is stubbed (same doubles as the
G3 entry tests). Facts are asserted from FRESH database sessions and a
freshly constructed ``RunEventMapper`` whose in-memory retention is
empty, so every envelope read must come from the persisted store — the
in-memory fallback is excluded by construction.

Parameterized on ``HECATE_STEP6_POSTGRES_URL`` (same variable as the
step6 harness). Without the variable this module reports an explicit
skip — a skip is never counted as a pass; the SQLite-tier regressions
live in ``test_entry_contract_closure.py`` and
``test_entry_registration_durability.py``.

ASGI-level HTTP satisfies this acceptance; real-TCP mid-stream
disconnects belong to the separate G3 gate and are not covered here.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.events import EventKind
from hecate.contracts.execution.references import run_ref as make_run_ref
from hecate.contracts.execution.references import task_ref as make_task_ref
from hecate.core.config import settings
from hecate.execution.entry_events import RunEventMapper
from hecate.execution.task_run_registry import TaskRunRegistry

PG_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="HECATE_STEP6_POSTGRES_URL not configured; PostgreSQL entry e2e reports skip (not pass)",
)

_WORKSPACE = uuid.UUID(int=0)


def _schema_name(tmp_path) -> str:
    return "e2e_" + re.sub(r"[^0-9a-zA-Z_]", "_", tmp_path.name)[:40] + "_" + uuid.uuid4().hex[:8]


class _PgStack:
    """Per-test PostgreSQL stack: ORM engine + factory + event store."""

    def __init__(self, engine, factory, event_store, schema: str) -> None:
        self.engine = engine
        self.factory = factory
        self.event_store = event_store
        self.schema = schema


@pytest.fixture
async def pg_stack(tmp_path, monkeypatch) -> AsyncGenerator[_PgStack, None]:
    from sqlalchemy import text

    from hecate.core.composition import entry_assembly
    from hecate.core.composition.entry_assembly import (
        register_shared_event_store,
        register_shared_registry_session_factory,
    )
    from hecate.main import app
    from hecate.studio.event_state.postgres_store import PostgresEventStore
    from tests.conftest import Base

    schema = _schema_name(tmp_path)
    async_url = PG_URL.replace("+psycopg", "+asyncpg")
    admin = create_async_engine(async_url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    await admin.dispose()

    engine = create_async_engine(
        async_url,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    store = PostgresEventStore(async_session_factory=factory)

    # The HTTP entry resolves the store from app.state and entries built
    # outside request context resolve it through entry_assembly; the
    # registry factory routes registration/terminal writes onto this PG.
    for name in ("_shared_event_store", "_shared_session_state_store", "_shared_registry_session_factory"):
        monkeypatch.setattr(entry_assembly, name, getattr(entry_assembly, name))
    monkeypatch.setattr(app.state, "event_store", store, raising=False)
    register_shared_event_store(store)
    register_shared_registry_session_factory(factory)

    from hecate.runtime.session_state import InMemorySessionStateStore

    monkeypatch.setattr(app.state, "session_state_store", InMemorySessionStateStore(), raising=False)

    from hecate.core.composition.entry_assembly import register_shared_session_state_store

    register_shared_session_state_store(app.state.session_state_store)

    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)
    try:
        yield _PgStack(engine, factory, store, schema)
    finally:
        await engine.dispose()
        drop = create_async_engine(async_url)
        async with drop.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await drop.dispose()


@pytest.fixture
async def pg_client(pg_stack: _PgStack, test_user_id, monkeypatch) -> AsyncGenerator[AsyncClient, None]:
    from hecate.core.database import get_db
    from hecate.core.deps_workspace import AuthContext, get_auth_context
    from hecate.main import app
    from hecate.models.workspace_member import WorkspaceRole

    auth = AuthContext(
        user_id=test_user_id,
        org_id=_WORKSPACE,
        workspace_id=_WORKSPACE,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with pg_stack.factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setitem(app.dependency_overrides, get_auth_context, lambda: auth)
    monkeypatch.setattr(settings, "HECATE_API_KEYS", "test-api-key-123")
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _seed_workspace_parents(factory) -> None:
    import sqlalchemy as sa

    from hecate.models.organization import OrganizationModel
    from hecate.models.user import UserModel
    from hecate.models.workspace import WorkspaceModel

    async with factory() as session:
        if (
            await session.execute(sa.select(WorkspaceModel).where(WorkspaceModel.id == _WORKSPACE))
        ).scalar_one_or_none():
            return
        org = OrganizationModel(id=_WORKSPACE, name="e2e-org", slug=f"e2e-{uuid.uuid4().hex[:8]}", owner_id=_WORKSPACE)
        owner = UserModel(id=_WORKSPACE, email=f"e2e-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x")
        session.add(owner)
        await session.flush()
        session.add(org)
        await session.flush()
        session.add(WorkspaceModel(id=_WORKSPACE, org_id=org.id, name="e2e-ws", slug=f"ws-{uuid.uuid4().hex[:8]}"))
        await session.commit()


@pytest.fixture
async def pg_agent(pg_stack: _PgStack) -> uuid.UUID:
    from tests.test_channel.test_chat_engine_g3_entry import _agent_with_tool

    await _seed_workspace_parents(pg_stack.factory)
    async with pg_stack.factory() as session:
        agent = await _agent_with_tool(session, _WORKSPACE, with_deployment=True)
        await session.commit()
        return agent.id


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch):
    """Deterministic provider/tool doubles at the engine boundary."""
    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS
    from tests.test_channel.test_chat_engine_g3_entry import StubLLMService, StubToolExecutor

    monkeypatch.setitem(
        BUILTIN_TOOL_DEFINITIONS,
        "stub_calculator",
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

    llm = StubLLMService()
    executor = StubToolExecutor()
    llm.tool_executor = executor
    import hecate.channel.api.v1.chat as chat_module
    from hecate.tools.tool.registry import ToolRegistry

    monkeypatch.setattr(chat_module, "llm_service", llm)

    def _build(db, skill_ref_manifest=None, *, workspace_id=None):
        registry = ToolRegistry(db=db, builtin_executor=executor, workspace_id=workspace_id)
        registry._builtin_names = {"stub_calculator"}
        return registry

    monkeypatch.setattr(chat_module, "_build_tool_registry", _build)
    return llm


async def _fresh_terminal(pg_stack: _PgStack, correlation: dict[str, Any]) -> tuple[Any, Any]:
    """Read run + envelopes from fresh sessions (no request-process state)."""
    async with pg_stack.factory() as fresh:
        run = await TaskRunRegistry(fresh).get_run(uuid.UUID(correlation["run_ref"]["id"]), _WORKSPACE)
    reader = RunEventMapper(
        make_task_ref(correlation["task_ref"]["issuer_domain"], correlation["task_ref"]["id"]),
        make_run_ref(correlation["run_ref"]["issuer_domain"], correlation["run_ref"]["id"]),
        event_store=pg_stack.event_store,
    )
    page = await reader.read_events()
    return run, page


async def test_pg_entry_contract_success_rejection_exception_stop(
    pg_stack: _PgStack, pg_client: AsyncClient, pg_agent: uuid.UUID, stub_provider, monkeypatch
) -> None:
    payload: dict[str, Any] = {"messages": [{"role": "user", "content": "add 2 and 3"}]}

    # --- success: full tool loop reaches a succeeded terminal on PG ---
    ok = await pg_client.post(f"/v1/agents/{pg_agent}/chat/completions", json=payload)
    assert ok.status_code == 200
    correlation = ok.json().get("hecate_correlation") or ok.json()["correlation"]
    assert correlation["status"] == "registered"
    run, page = await _fresh_terminal(pg_stack, correlation)
    assert run.projection["state"] == "succeeded"
    assert page.events[-1].kind is EventKind.TERMINAL
    assert page.events[-1].payload["state"] == "succeeded"
    assert stub_provider.tool_executor.calls == [("stub_calculator", {"a": 2, "b": 3})]

    # --- rejection: unknown agent is refused without fabricated facts ---
    denied = await pg_client.post(f"/v1/agents/{uuid.uuid4()}/chat/completions", json=payload)
    assert denied.status_code in (404, 422)
    assert "correlation" not in denied.json()

    # --- exception: engine failure leaves terminal evidence on PG even
    # though the caller's request transaction rolled back; the client sees
    # a plain 500 (no internals), and the run is recovered from persistence ---
    async def explode_stream(**_kwargs):
        yield {"content": "partial", "tool_calls": None, "finish_reason": None}
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(stub_provider, "chat_stream", explode_stream)
    broken = await pg_client.post(f"/v1/agents/{pg_agent}/chat/completions", json=payload)
    assert broken.status_code >= 500
    assert "provider exploded" not in broken.text

    import sqlalchemy as sa

    from hecate.models.run import RunModel

    async with pg_stack.factory() as fresh:
        failed_run = (
            (
                await fresh.execute(
                    sa.select(RunModel)
                    .where(RunModel.workspace_id == _WORKSPACE)
                    .order_by(RunModel.created_at.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        failed_run = next(r for r in failed_run if (r.projection or {}).get("state") == "failed")
        failed_run_id = str(failed_run.id)
        failed_task_id = str(failed_run.task_id)
    assert failed_run.projection["error_category"] == "execution_failed"

    reader = RunEventMapper(
        make_task_ref("hecate", failed_task_id), make_run_ref("hecate", failed_run_id), event_store=pg_stack.event_store
    )
    page = await reader.read_events()
    assert page.events[-1].kind is EventKind.TERMINAL
    assert page.events[-1].payload == {
        "state": "failed",
        "stop_category": "execution_failed",
        "error": {"type": "RuntimeError"},
    }
    # The provider chunk that never completed a superstep produces no
    # runtime-level event; the failed run's persisted view is exactly the
    # terminal envelope (partial-event mapping is proven at the adapter
    # seam in the SQLite-tier closure tests).
    assert [e.kind for e in page.events] == [EventKind.TERMINAL]

    # --- stop: invalid structured output stops with an explicit category ---
    from tests.test_channel.test_chat_engine_g3_entry import StubLLMService as _Scripted

    scripted = _Scripted()
    monkeypatch.setattr(stub_provider, "chat", scripted.chat)
    monkeypatch.setattr(stub_provider, "chat_stream", scripted.chat_stream)
    stop = await pg_client.post(
        f"/v1/agents/{pg_agent}/chat/completions",
        json={
            "messages": [{"role": "user", "content": "add 2 and 3"}],
            "output_schema": {
                "type": "object",
                "properties": {"answer": {"type": "number"}},
                "required": ["answer"],
            },
        },
    )
    assert stop.status_code == 409
    assert stop.json()["error"]["reason_category"] == "invalid_output"
    correlation = stop.json().get("hecate_correlation") or stop.json()["correlation"]
    run, page = await _fresh_terminal(pg_stack, correlation)
    assert run.projection["error_category"] == "invalid_output"
    assert page.events[-1].payload["stop_category"] == "invalid_output"

    # Cross-cutting: client-visible error payloads never carry internals.
    assert "provider exploded" not in broken.text
    assert "Traceback" not in broken.text and "Traceback" not in stop.text


def _http_correlation(response, stream):
    if not stream:
        return response.json()["hecate_correlation"]
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    return next(frame["hecate_correlation"] for frame in frames if "hecate_correlation" in frame)


@pytest.mark.parametrize("stream", [False, True])
async def test_pg_entry_maps_owned_artifacts_and_rejects_foreign_refs(
    pg_stack, pg_client, pg_agent, stub_provider, monkeypatch, stream
):
    from hecate.models.evidence import EvidenceModel

    own = EvidenceModel(session_id=uuid.uuid4(), workspace_id=_WORKSPACE, tool_name="report")
    foreign = EvidenceModel(session_id=uuid.uuid4(), workspace_id=uuid.uuid4(), tool_name="foreign-report")
    deleted = EvidenceModel(session_id=uuid.uuid4(), workspace_id=_WORKSPACE, tool_name="deleted-report", deleted=True)
    missing_id = str(uuid.uuid4())
    async with pg_stack.factory() as session:
        session.add_all([own, foreign, deleted])
        await session.commit()
    references = [
        {"kind": "artifact", "issuer_domain": "hecate", "id": str(value)}
        for value in (own.id, foreign.id, missing_id, deleted.id)
    ]

    async def report_tool(name, args, context=None):
        return json.dumps({"value": 5, "declared_artifacts": references})

    monkeypatch.setattr(stub_provider.tool_executor, "execute", report_tool)
    response = await pg_client.post(
        f"/v1/agents/{pg_agent}/chat/completions",
        json={"messages": [{"role": "user", "content": "produce a report"}], "stream": stream},
    )
    assert response.status_code == 200
    correlation = _http_correlation(response, stream)
    run, page = await _fresh_terminal(pg_stack, correlation)
    assert run.projection["artifacts"] == [references[0]]
    terminals = [event for event in page.events if event.kind is EventKind.TERMINAL]
    assert len(terminals) == 1
    assert terminals[0].payload["artifacts"] == [references[0]]
    assert len(terminals[0].extra["rejected_artifacts"]) == 3
    assert "declared_artifacts" not in response.text
    detail = await pg_client.get(f"/api/runs/{run.id}")
    assert detail.status_code == 200
    assert detail.json()["projection"]["artifacts"] == [references[0]]
    public_events = []
    cursor = None
    while True:
        params = {"limit": 1}
        if cursor is not None:
            params["cursor"] = cursor
        public_page = await pg_client.get(f"/api/runs/{run.id}/events", params=params)
        assert public_page.status_code == 200
        body = public_page.json()
        public_events.extend(body["events"])
        cursor = body.get("next_cursor")
        if not body["has_more"]:
            break
    assert public_events and public_events[-1]["kind"] == "terminal"
    assert public_events[-1]["payload"]["artifacts"] == [references[0]]
    assert all("backend_detail" not in event for event in public_events)
    public_stream = await pg_client.get(f"/api/runs/{run.id}/events/stream")
    assert public_stream.status_code == 200
    assert '"kind": "terminal"' in public_stream.text
    assert "backend_detail" not in public_stream.text
    after_terminal = await pg_client.get(
        f"/api/runs/{run.id}/events/stream", params={"cursor": public_events[-1]["source_sequence"]}
    )
    assert after_terminal.status_code == 200
    assert '"kind": "terminal"' not in after_terminal.text
    from dataclasses import replace

    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    existing_auth = app.dependency_overrides[get_auth_context]()
    other_auth = replace(existing_auth, workspace_id=uuid.uuid4())
    monkeypatch.setitem(app.dependency_overrides, get_auth_context, lambda: other_auth)
    for suffix in ("", "/events", "/events/stream"):
        assert (await pg_client.get(f"/api/runs/{run.id}{suffix}")).status_code == 404
    # step14: the SSE gate completes before the stream is created — a denied
    # subscriber gets the plain HTTP 404 like every other entry, never a
    # 200 stream carrying error frames.
    denied_stream = await pg_client.get(f"/api/runs/{run.id}/events/stream")
    assert denied_stream.status_code == 404
    assert denied_stream.headers.get("content-type", "") != "text/event-stream"
    assert "event: error" not in denied_stream.text
    assert str(own.id) not in denied_stream.text


@pytest.mark.parametrize("fail", [False, True])
async def test_pg_request_rollback_preserves_entry_without_committing_business_writes(
    pg_stack, pg_client, pg_agent, stub_provider, monkeypatch, fail
):
    import sqlalchemy as sa

    from hecate.core.database import get_db
    from hecate.main import app
    from hecate.models.agent import AgentModel
    from hecate.models.run import RunModel

    ghost_id = uuid.uuid4()

    async def rollback_request():
        async with pg_stack.factory() as session:
            session.add(AgentModel(id=ghost_id, workspace_id=_WORKSPACE, name="uncommitted-business-row"))
            await session.flush()
            try:
                yield session
            finally:
                await session.rollback()

    monkeypatch.setitem(app.dependency_overrides, get_db, rollback_request)
    if fail:

        async def explode(**kwargs):
            yield {"content": "partial", "tool_calls": None}
            raise RuntimeError("private provider failure")

        monkeypatch.setattr(stub_provider, "chat_stream", explode)
    response = await pg_client.post(
        f"/v1/agents/{pg_agent}/chat/completions",
        json={"messages": [{"role": "user", "content": "add 2 and 3"}]},
    )
    assert response.status_code == (500 if fail else 200)
    async with pg_stack.factory() as fresh:
        assert await fresh.get(AgentModel, ghost_id) is None
        run = (await fresh.scalars(sa.select(RunModel).where(RunModel.workspace_id == _WORKSPACE))).one()
        assert run.projection["state"] == ("failed" if fail else "succeeded")
        correlation = {
            "task_ref": make_task_ref(run.issuer_domain, str(run.task_id)).to_dict(),
            "run_ref": make_run_ref(run.issuer_domain, str(run.id)).to_dict(),
        }
    persisted_run, page = await _fresh_terminal(pg_stack, correlation)
    assert page.events[-1].payload["state"] == persisted_run.projection["state"]


@pytest.mark.parametrize("stream", [False, True])
async def test_pg_policy_rejection_has_persisted_run_evidence(pg_stack, pg_client, pg_agent, stub_provider, stream):
    from hecate.models.tool_policy import ToolPolicyRuleModel

    async with pg_stack.factory() as session:
        session.add(ToolPolicyRuleModel(workspace_id=_WORKSPACE, tool_pattern="stub_calculator", action="deny"))
        await session.commit()
    response = await pg_client.post(
        f"/v1/agents/{pg_agent}/chat/completions",
        json={"messages": [{"role": "user", "content": "add 2 and 3"}], "stream": stream},
    )
    assert response.status_code == 200
    correlation = _http_correlation(response, stream)
    run, page = await _fresh_terminal(pg_stack, correlation)
    assert run.projection["state"] == "succeeded"
    assert page.events[-1].payload["state"] == "succeeded"
    assert not stub_provider.tool_executor.calls
    final_messages = (stub_provider.chat_calls or stub_provider.stream_calls)[-1]
    assert any(
        message.get("role") == "tool" and "den" in str(message.get("content", "")).lower() for message in final_messages
    )
