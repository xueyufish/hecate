"""Real-TCP mid-stream disconnect evidence (step5d G3).

The prior G3 disconnect evidence ran on ASGITransport, which buffers the
entire server response before the client reads — it proves a
post-completion snapshot and resume, NOT a mid-stream disconnect. This
module serves the real application through uvicorn on a real socket,
disconnects the client's TCP connection while the engine is mid-run
(a tool has executed, the second model call is deliberately held), and
asserts from fresh database sessions that:

- the disconnected run converges to a cancelled terminal with persisted
  events;
- a same-session recovery request does not re-execute the succeeded tool
  (zero duplicate side effects) and backfills the real receipt;
- when the recorded result content is made unavailable, recovery of a
  side-effecting call stops with ``reconciliation_required`` instead of
  handing the model a placeholder receipt.

The tier is selected by ``HECATE_STEP6_POSTGRES_URL`` (same convention
as the step6/#257 harnesses): with the variable both the platform ORM
and the event store run on PostgreSQL; without it the module runs the
file-backed SQLite tier. The server event loop and the test event loop
use separate engines (separate pools) over the SAME database so pooled
connections never cross loops.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import struct
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from hecate.core.config import settings

PG_URL = os.environ.get("HECATE_STEP6_POSTGRES_URL")
_WORKSPACE = uuid.UUID(int=0)
_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
_POLL_TIMEOUT_S = 40.0


def _schema_name(tmp_path) -> str:
    return "g3_" + re.sub(r"[^0-9a-zA-Z_]", "_", tmp_path.name)[:40] + "_" + uuid.uuid4().hex[:8]


class _EntryStack:
    """Server-side and test-reader session factories over one database."""

    def __init__(self, server_factory, reader_factory, engines, cleanup) -> None:
        self.server_factory = server_factory
        self.reader_factory = reader_factory
        self.session_state: Any = None
        self._engines = engines
        self._cleanup = cleanup

    async def aclose(self) -> None:
        for engine in self._engines:
            await engine.dispose()
        await self._cleanup()


@pytest.fixture
async def entry_stack(tmp_path, monkeypatch) -> AsyncGenerator[_EntryStack, None]:
    from hecate.core.composition import entry_assembly
    from hecate.core.composition.entry_assembly import (
        register_shared_event_store,
        register_shared_registry_session_factory,
        register_shared_session_state_store,
    )
    from hecate.main import app
    from hecate.runtime.session_state import InMemorySessionStateStore
    from tests.conftest import Base

    cleanup: Any = None
    if PG_URL:
        from sqlalchemy import text

        from hecate.studio.event_state.postgres_store import PostgresEventStore
        from hecate.studio.session_state.postgres_store import PostgresSessionStateStore

        schema = _schema_name(tmp_path)
        async_url = PG_URL.replace("+psycopg", "+asyncpg")
        admin = create_async_engine(async_url)
        async with admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await admin.dispose()
        kwargs = {"connect_args": {"server_settings": {"search_path": f"{schema},public"}}}
        server_engine = create_async_engine(async_url, poolclass=NullPool, **kwargs)
        reader_engine = create_async_engine(async_url, **kwargs)
        # NullPool on the request engine: a connection torn down by a
        # client disconnect must not poison later requests (observed as
        # "no active connection" on pool reuse).
        # Dedicated pool for registry/terminal writes: request connections
        # torn down by a client disconnect must never poison the terminal
        # projection write (observed as "no active connection" on reuse).
        registry_engine = create_async_engine(async_url, poolclass=NullPool, **kwargs)
        async with reader_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store: Any = PostgresEventStore(async_session_factory=async_sessionmaker(server_engine))

        async def _drop() -> None:
            drop = create_async_engine(async_url)
            async with drop.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await drop.dispose()

        cleanup = _drop
    else:
        db_file = tmp_path / "g3-tier.db"
        from sqlalchemy import event as sa_event

        def _wal(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")

        server_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)
        reader_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        # See the PG branch: a dedicated pool isolates terminal writes from
        # request-connection teardown on disconnect.
        registry_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)
        for engine in (server_engine, reader_engine, registry_engine):
            sa_event.listens_for(engine.sync_engine, "connect")(_wal)
        async with reader_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def _noop() -> None:
            return None

        cleanup = _noop
        from hecate.runtime.eventstore import InMemoryEventStore

        store = InMemoryEventStore()

    server_factory = async_sessionmaker(server_engine, class_=AsyncSession, expire_on_commit=False)
    reader_factory = async_sessionmaker(reader_engine, class_=AsyncSession, expire_on_commit=False)
    if PG_URL:
        session_state = PostgresSessionStateStore(async_sessionmaker(registry_engine, expire_on_commit=False))
        reader_store = PostgresEventStore(async_session_factory=reader_factory)
    else:
        session_state = InMemorySessionStateStore()
        reader_store = store

    for name in ("_shared_event_store", "_shared_session_state_store", "_shared_registry_session_factory"):
        monkeypatch.setattr(entry_assembly, name, getattr(entry_assembly, name))
    monkeypatch.setattr(app.state, "event_store", store, raising=False)
    monkeypatch.setattr(app.state, "session_state_store", session_state, raising=False)
    register_shared_event_store(store)
    register_shared_session_state_store(session_state)
    register_shared_registry_session_factory(
        async_sessionmaker(registry_engine, class_=AsyncSession, expire_on_commit=False)
    )
    monkeypatch.setattr(settings, "CHAT_TOOL_LOOP_ENGINE_ENABLED", True)

    stack = _EntryStack(server_factory, reader_factory, [server_engine, reader_engine, registry_engine], cleanup)
    stack.session_state = session_state
    stack.store = store
    stack.reader_store = reader_store
    try:
        yield stack
    finally:
        await stack.aclose()


class _ScriptedProvider:
    """Deterministic two-round provider double with an answer-round hold.

    Round 1 and 2 propose a tool call each (fresh ids); once two tool
    results are visible the next call ANSWERS — and while ``hold_answer``
    is set that answer call parks until released, giving the test a
    deterministic point to abort the TCP connection with the run
    mid-flight (both tools done, answer pending)."""

    def __init__(self) -> None:
        self.max_tool_rounds = 2
        self.model_calls = 0
        self.inputs: list[list[dict]] = []
        self.hold_answer = threading.Event()
        self.release_answer = threading.Event()
        self.answer_entered = threading.Event()

    @staticmethod
    def _tool_count(messages: list) -> int:
        return sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "tool")

    async def _maybe_hold_answer(self, messages: list) -> None:
        if self._tool_count(messages) >= self.max_tool_rounds and self.hold_answer.is_set():
            self.answer_entered.set()
            while not self.release_answer.is_set():
                await asyncio.sleep(0.05)

    async def chat(self, *, messages, model=None, tools=None, **_kwargs):
        from types import SimpleNamespace

        await self._maybe_hold_answer(messages)
        self.inputs.append([dict(message) for message in messages])
        self.model_calls += 1
        if self._tool_count(messages) < self.max_tool_rounds:
            # Round-stable call ids: a replayed turn re-proposes the same
            # logical call, which is what the receipt-replay protection (and
            # real deterministic flows) key on.
            call_id = f"call_{self._tool_count(messages) + 1}"
            return SimpleNamespace(
                content="",
                model=model or "stub-model",
                tool_calls=[
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "stub_calculator", "arguments": '{"a": 2, "b": 3}'},
                    }
                ],
                finish_reason="tool_calls",
                usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            )
        return SimpleNamespace(
            content="The answer is " + ", ".join(str(m["content"]) for m in messages if m.get("role") == "tool"),
            model=model or "stub-model",
            tool_calls=None,
            finish_reason="stop",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )

    async def chat_stream(self, *, messages, model=None, tools=None, **kwargs):
        from tests.test_channel.test_chat_engine_g3_entry import _stream_tool_delta

        result = await self.chat(messages=messages, model=model, tools=tools, **kwargs)
        if result.tool_calls is None:
            yield {"content": result.content, "tool_calls": None, "finish_reason": "stop"}
            yield {"content": "", "tool_calls": None, "finish_reason": None}
            return
        yield {"content": "", "tool_calls": [_stream_tool_delta(tc["id"]) for tc in result.tool_calls]}


class _GatedExecutor:
    """Tool double that parks the SECOND execution until released.

    The park is the disconnect window: while parked, the run is genuinely
    mid-flight (first tool succeeded, second in progress). After the
    client disconnects and the gate is released, the engine reaches the
    next model boundary where the cooperative cancel check observes the
    disconnected transport and cancels the run."""

    def __init__(self) -> None:
        from tests.test_channel.test_chat_engine_g3_entry import StubToolExecutor

        self._inner = StubToolExecutor()
        self.hold_second = threading.Event()
        self.release = threading.Event()
        self.answer_now = threading.Event()
        self.second_entered = threading.Event()

    @property
    def calls(self) -> list:
        return self._inner.calls

    async def execute(self, name, args, context=None):
        if self.hold_second.is_set() and len(self._inner.calls) >= 1 and not self.answer_now.is_set():
            self.second_entered.set()
            while not self.release.is_set():
                await asyncio.sleep(0.05)
        return await self._inner.execute(name, args, context)


@pytest.fixture
def gated_provider(monkeypatch: pytest.MonkeyPatch) -> tuple[_ScriptedProvider, _GatedExecutor]:
    import hecate.channel.api.v1.chat as chat_module
    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS
    from hecate.tools.tool.registry import ToolRegistry

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
    provider = _ScriptedProvider()
    executor = _GatedExecutor()
    monkeypatch.setattr(chat_module, "llm_service", provider)

    def _build(db, skill_ref_manifest=None, *, workspace_id=None):
        registry = ToolRegistry(db=db, builtin_executor=executor, workspace_id=workspace_id)
        registry._builtin_names = {"stub_calculator"}
        return registry

    monkeypatch.setattr(chat_module, "_build_tool_registry", _build)
    return provider, executor


@pytest.fixture
async def real_server(entry_stack, gated_provider, test_user_id, monkeypatch) -> AsyncGenerator[Any, None]:
    """uvicorn on a real socket; yields (base_url, agent_id, stack)."""
    from hecate.core.database import get_db
    from hecate.core.deps_workspace import AuthContext, get_auth_context
    from hecate.main import app
    from hecate.models.workspace_member import WorkspaceRole
    from tests.test_channel.test_chat_engine_g3_entry import _agent_with_tool
    from tests.test_execution.test_entry_contract_pg_e2e import _seed_workspace_parents

    await _seed_workspace_parents(entry_stack.server_factory)
    async with entry_stack.server_factory() as session:
        agent = await _agent_with_tool(session, _WORKSPACE, with_deployment=True)
        await session.commit()
        agent_id = agent.id

    auth = AuthContext(
        user_id=test_user_id,
        org_id=_WORKSPACE,
        workspace_id=_WORKSPACE,
        role=WorkspaceRole.ADMIN,
        auth_method="jwt",
        api_key_scope=None,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with entry_stack.server_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setitem(app.dependency_overrides, get_db, override_get_db)
    monkeypatch.setitem(app.dependency_overrides, get_auth_context, lambda: auth)
    monkeypatch.setattr(settings, "HECATE_API_KEYS", "test-api-key-123")

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        loop="asyncio",
        lifespan="off",
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if server.started and server.servers:
            break
        if not thread.is_alive():
            raise RuntimeError("uvicorn thread died during startup")
        await asyncio.sleep(0.05)
    assert server.started and server.servers, "uvicorn startup timed out"
    port = server.servers[0].sockets[0].getsockname()[1]

    class _Handle:
        def __init__(self) -> None:
            self.base_url = f"http://127.0.0.1:{port}"
            self.agent_id = agent_id

    try:
        yield _Handle()
    finally:
        provider, executor = gated_provider
        provider.release_answer.set()
        executor.release.set()
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        assert not thread.is_alive(), "uvicorn did not shut down"


class _RawSseClient:
    """Raw-socket SSE client with a deferred abortive close.

    httpx's graceful response close does not surface as a server-side
    disconnect on this stack; an abortive close (SO_LINGER 0 -> RST) does
    - the same abortive teardown browsers apply when leaving an SSE page.
    The client parks after reading the correlation frame; the test then
    drives the run to the desired mid-flight point and calls
    ``rst_close()`` to sever the connection for real."""

    def __init__(self, base_url: str, path: str, payload: dict) -> None:
        import socket as _socket

        host, _, port = base_url.removeprefix("http://").partition(":")
        self._sock = _socket.create_connection((host, int(port)), timeout=30)
        self._closed = False
        body = json.dumps(payload).encode()
        request = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + body
        self._sock.sendall(request)
        self.correlation: dict[str, Any] | None = None
        buffer = b""
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and self.correlation is None:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            for line in buffer.decode("utf-8", errors="replace").splitlines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    frame = json.loads(line.removeprefix("data: "))
                except ValueError:
                    continue
                candidate = frame.get("hecate_correlation") or frame.get("correlation")
                if isinstance(candidate, dict):
                    self.correlation = candidate
                    break
        assert self.correlation is not None, "no correlation frame read"

    def rst_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        linger = struct.pack("HH" if os.name == "nt" else "ii", 1, 0)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        self._sock.close()


async def _await_terminal(entry_stack, run_id: uuid.UUID) -> dict[str, Any]:
    from hecate.execution.task_run_registry import TaskRunRegistry

    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        async with entry_stack.reader_factory() as fresh:
            run = await TaskRunRegistry(fresh).get_run(run_id, _WORKSPACE)
            projection = dict(run.projection or {})
            if projection.get("state") in _TERMINAL_STATES:
                return projection
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} did not reach a terminal state within {_POLL_TIMEOUT_S}s")


async def _assert_terminal_evidence(entry_stack, correlation, category):
    from hecate.contracts.execution.events import EventKind
    from hecate.contracts.execution.references import run_ref, task_ref
    from hecate.execution.entry_events import RunEventMapper

    projection = await _await_terminal(entry_stack, uuid.UUID(correlation["run_ref"]["id"]))
    assert projection["error_category"] == category
    assert projection["state"] == ("cancelled" if category == "cancelled" else "failed")
    reader = RunEventMapper(
        task_ref("hecate", correlation["task_ref"]["id"]),
        run_ref("hecate", correlation["run_ref"]["id"]),
        event_store=entry_stack.reader_store,
    )
    page = await reader.read_events(limit=1000)
    terminals = [event for event in page.events if event.kind is EventKind.TERMINAL]
    assert len(terminals) == 1
    assert terminals[0].payload["stop_category"] == category
    assert terminals[0].payload["state"] == projection["state"]


async def test_real_tcp_midstream_disconnect_zero_duplicate_and_recovery(
    real_server, entry_stack, gated_provider, test_user_id, request
) -> None:
    provider, executor = gated_provider
    session_id = uuid.uuid4()
    provider.hold_answer.set()
    executor.hold_second.set()
    payload = {
        "messages": [{"role": "user", "content": "add 2 and 3"}],
        "session_id": str(session_id),
        "stream": True,
    }

    raw = _RawSseClient(real_server.base_url, f"/v1/agents/{real_server.agent_id}/chat/completions", payload)
    request.addfinalizer(raw.rst_close)
    correlation = raw.correlation
    assert correlation is not None
    executor.release.set()
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline and not provider.answer_entered.is_set():
        await asyncio.sleep(0.05)
    assert provider.answer_entered.is_set(), "answer call never parked"
    assert len(executor.calls) == 2, "both tool rounds must have executed exactly once"
    raw.rst_close()  # sever the connection with the run mid-flight
    # Release the parked answer: the engine produces it, the SSE write to
    # the dead socket fails, and the run cancels instead of finishing.
    provider.release_answer.set()
    executor.answer_now.set()

    assert correlation["status"] == "registered"
    await _assert_terminal_evidence(entry_stack, correlation, "cancelled")

    # Recovery on the same session: receipts backfilled from persisted
    # facts, no re-execution, the answer arrives normally.
    async with httpx.AsyncClient(base_url=real_server.base_url, timeout=60) as client:
        recovery = await client.post(
            f"/v1/agents/{real_server.agent_id}/chat/completions",
            json={
                "messages": [{"role": "user", "content": "add 2 and 3"}],
                "session_id": str(session_id),
                "stream": False,
            },
        )
    assert recovery.status_code == 200
    assert len(executor.calls) == 2, "succeeded tools must not re-execute on recovery"
    assert "5" in recovery.json()["choices"][0]["message"]["content"]
    receipts = [m for m in provider.inputs[-1] if m.get("role") == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in receipts] == [("call_1", "5"), ("call_2", "5")]
    assert "reconciliation" not in json.dumps(provider.inputs)


async def test_real_tcp_disconnect_unrecoverable_content_stops_for_reconciliation(
    real_server, entry_stack, gated_provider, test_user_id, request
) -> None:
    """Disconnect mid-run, then make the recorded result content
    unavailable (drop the tool messages from the persisted session state
    while the event log keeps only digests): recovery must stop with an
    explicit reconciliation category - no re-execution, no placeholder
    receipt for the model to reason on."""
    provider, executor = gated_provider
    session_id = uuid.uuid4()
    provider.hold_answer.set()
    executor.hold_second.set()
    payload = {
        "messages": [{"role": "user", "content": "add 2 and 3"}],
        "session_id": str(session_id),
        "stream": True,
    }

    raw = _RawSseClient(real_server.base_url, f"/v1/agents/{real_server.agent_id}/chat/completions", payload)
    request.addfinalizer(raw.rst_close)
    correlation = raw.correlation
    assert correlation is not None
    executor.release.set()
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline and not provider.answer_entered.is_set():
        await asyncio.sleep(0.05)
    assert provider.answer_entered.is_set()
    raw.rst_close()
    provider.release_answer.set()
    executor.answer_now.set()

    await _assert_terminal_evidence(entry_stack, correlation, "cancelled")

    # Injection: make the recorded receipts unrecoverable for real. The
    # receipt replay can now backfill real content from the session log's
    # messages-channel writes, so removing the tool messages from the
    # checkpoint alone no longer creates the gap. Delete just the channel
    # writes containing the content; keep TOOL_CALL/TOOL_RESULT receipts.
    from hecate.runtime.eventstore import EventType

    original_events = await entry_stack.store.get_events(session_id)
    dropped_calls = {"call_1", "call_2"}

    def keep(event):
        event_payload = event.payload or {}
        return not (
            event.event_type is EventType.CHANNEL_WRITE
            and event_payload.get("channel") == "messages"
            and isinstance(event_payload.get("value"), list)
            and any(
                isinstance(item, dict) and item.get("role") == "tool" and item.get("tool_call_id") in dropped_calls
                for item in event_payload["value"]
            )
        )

    store = entry_stack.store
    if PG_URL:
        from sqlalchemy import delete

        from hecate.studio.event_state.models import EventModel

        removed = [event.version for event in original_events if not keep(event)]
        async with entry_stack.reader_factory() as fresh:
            await fresh.execute(
                delete(EventModel).where(EventModel.session_id == session_id, EventModel.version.in_(removed))
            )
            await fresh.commit()
    else:
        store._store[session_id] = [event for event in original_events if keep(event)]

    # Also remove checkpoint copies so only the persisted success receipts
    # survive. Keep the same backend: a PG fault test must still use PG.
    if PG_URL:
        from hecate.studio.session_state.models import SessionStateModel

        async with entry_stack.reader_factory() as fresh:
            await fresh.execute(delete(SessionStateModel).where(SessionStateModel.session_id == session_id))
            await fresh.commit()
    else:
        for org_bucket in entry_stack.session_state._storage.values():
            for user_bucket in org_bucket.values():
                user_bucket.pop(session_id, None)

    async with httpx.AsyncClient(base_url=real_server.base_url, timeout=60) as client:
        recovery = await client.post(
            f"/v1/agents/{real_server.agent_id}/chat/completions",
            json={
                "messages": [{"role": "user", "content": "add 2 and 3"}],
                "session_id": str(session_id),
                "stream": False,
            },
        )

    payload_out = recovery.json()
    reason = payload_out.get("error", {}).get("reason_category")
    assert reason == "reconciliation_required", f"expected reconciliation stop, got {payload_out}"
    assert len(executor.calls) == 2, "unrecoverable writes must not re-execute"
    assert "reconciliation required" in json.dumps(payload_out)
    assert recovery.status_code == 409
    await _assert_terminal_evidence(entry_stack, payload_out["correlation"], "reconciliation_required")
    assert "reconciliation" not in json.dumps(provider.inputs)
