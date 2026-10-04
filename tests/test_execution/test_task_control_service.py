"""Task control service tests (step6 platform track).

The service spans two engines by design — the async application ORM and
the synchronous durable-seam adapters — so these tests run both on one
file-backed SQLite database. Dispatch goes through the real lifecycle/
event/projection machinery with the entry-service boundary stubbed (the
entry chain has its own suites); command receipts, guard arbitration,
cursor pagination, and reconciliation run for real.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.contracts.execution.durable import CommandState, ControlCommandKind, TaskLifecycleState
from hecate.core.database import Base
from hecate.execution.governance_events import RUN_TERMINAL, TASK_STATE_CHANGED, TASK_SUBMITTED
from hecate.execution.platform_durable import PlatformDurableFactory
from hecate.execution.task_control import (
    SubmissionConflictError,
    TaskControlService,
    task_ref_of,
)
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel

# Isolated per-test identifiers (the file DB is per-test, but explicit
# constants keep cross-workspace assertions readable).
WS = uuid.UUID("00000000-0000-0000-0000-000000000010")
OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000011")
ORG = uuid.UUID("00000000-0000-0000-0000-000000000012")
OWNER = uuid.UUID("00000000-0000-0000-0000-000000000013")


class _EntryStub:
    """Records entry executions; returns a canned content string."""

    def __init__(self, content: str = "stub reply") -> None:
        self.calls: list[dict[str, Any]] = []
        self.content = content
        self.gate: asyncio.Event | None = None

    async def __call__(self, db, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        return self.content


class _GatedEntry:
    """Entry boundary that blocks until released (running-state tests)."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.calls = 0

    async def __call__(self, db, **kwargs: Any) -> str:
        self.calls += 1
        self.started.set()
        await self.gate.wait()
        return "late reply"


@pytest.fixture
async def harness(tmp_path):
    """File-backed SQLite with both engines plus seeded tenant and agent."""

    db_file = tmp_path / "task_control.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")

    # WAL lets the async sessions' open read transactions coexist with the
    # seam adapters' independent write transactions on the same file.
    @event.listens_for(async_engine.sync_engine, "connect")
    def _wal_async(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    factory = PlatformDurableFactory(f"sqlite:///{db_file}")

    @event.listens_for(factory.engine(), "connect")
    def _wal_sync(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    from hecate.core.composition.durable_platform import DurableSuite

    suite = DurableSuite(
        store=factory.task_store(),
        recorder=factory.command_recorder(),
        ledger=None,  # type: ignore[arg-type] — the ledger is not exercised here
        backend="postgres",
        ledger_source="stub",
    )

    async with session_factory() as db:
        db.add(OrganizationModel(id=ORG, name="org", slug=f"org-{ORG.hex[:12]}", owner_id=OWNER))
        db.add(WorkspaceModel(id=WS, org_id=ORG, name="ws", slug=f"ws-{WS.hex}"))
        db.add(WorkspaceModel(id=OTHER_WS, org_id=ORG, name="other", slug=f"ws-{OTHER_WS.hex}"))
        db.add(UserModel(id=OWNER, email="owner@example.com", hashed_password=uuid.uuid4().hex))
        agent = AgentModel(workspace_id=WS, name="task-agent")
        db.add(agent)
        await db.flush()
        version = AgentVersionModel(
            agent_id=agent.id, version=1, config_snapshot={"model": "stub"}, content_hash="a" * 64
        )
        db.add(version)
        await db.flush()
        db.add(
            AgentPrincipalModel(
                id=agent.id,
                agent_id=agent.id,
                workspace_id=WS,
                organization_id=ORG,
                owner_user_id=OWNER,
            )
        )
        db.add(
            AgentDeploymentModel(
                agent_id=agent.id,
                agent_version_id=version.id,
                workspace_id=WS,
                backend_type=BackendType.BUILTIN,
                access_mode=AccessMode.IN_PROCESS,
                issuer_domain="hecate",
                capability_snapshot={},
                axes_harness="hecate",
                axes_environment="none",
                axes_tool_execution="hecate_gateway",
                is_default=True,
            )
        )
        await db.commit()
        agent_id = agent.id

    yield {
        "session_factory": session_factory,
        "suite": suite,
        "agent_id": agent_id,
        "engine": async_engine,
        "factory": factory,
    }

    await async_engine.dispose()


def _service(harness, db: AsyncSession) -> TaskControlService:
    return TaskControlService(
        db,
        store=harness["suite"].store,
        recorder=harness["suite"].recorder,
        backend="postgres",
        ledger_source="stub",
        session_factory=harness["session_factory"],
    )


async def _submit(
    harness,
    db: AsyncSession,
    **overrides: Any,
):
    service = _service(harness, db)
    kwargs: dict[str, Any] = dict(
        workspace_id=WS,
        user_id=OWNER,
        goal="summarize the inventory",
        agent_id=harness["agent_id"],
        input={"messages": [{"role": "user", "content": "summarize"}]},
    )
    kwargs.update(overrides)
    return await service.submit(**kwargs)


async def test_submit_wait_reaches_terminal_with_events(harness, monkeypatch):
    stub = _EntryStub("final answer")
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", stub)

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, wait=True)
        assert result.lifecycle_state == "succeeded"
        assert result.result == {"status": "succeeded", "content": "final answer"}

        task_uuid = uuid.UUID(result.task_ref.id)
        detail = await _service(harness, db).get_task_detail(WS, task_uuid)
        assert detail["lifecycle_state"] == "succeeded"
        assert len(detail["runs"]) == 1
        assert detail["runs"][0]["projection"]["state"] == "succeeded"

        page = await _service(harness, db).read_run_events(WS, uuid.UUID(result.run_ref.id))
        schemas = [e.payload_schema_ref for e in page.events]
        assert schemas[0] == TASK_SUBMITTED
        assert schemas[-1] == RUN_TERMINAL
        assert TASK_STATE_CHANGED in schemas
        sequences = [e.source_sequence for e in page.events]
        assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
        for envelope in page.events:
            assert envelope.actor is not None and envelope.source is not None


async def test_submit_idempotent_replay_returns_original(harness, monkeypatch):
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", _EntryStub())

    async with harness["session_factory"]() as db:
        first = await _submit(harness, db, idempotency_key="key-1", wait=True)
        second = await _submit(harness, db, idempotency_key="key-1", wait=True)
        assert second.replayed is True
        assert second.task_ref == first.task_ref
        assert second.run_ref == first.run_ref

        detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(first.task_ref.id))
        assert len(detail["runs"]) == 1  # no second execution minted


async def test_submit_conflicting_replay_rejected(harness, monkeypatch):
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", _EntryStub())

    async with harness["session_factory"]() as db:
        await _submit(harness, db, idempotency_key="key-2", goal="first goal", wait=True)
        with pytest.raises(SubmissionConflictError):
            await _submit(harness, db, idempotency_key="key-2", goal="different goal", wait=True)


async def test_submit_background_completes_after_return(harness, monkeypatch):
    stub = _EntryStub("async reply")
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", stub)

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db)  # wait=False
        assert result.lifecycle_state == "queued"

    service_ref = _service  # poll with fresh sessions like a real client
    deadline = asyncio.get_running_loop().time() + 10
    state = "queued"
    while asyncio.get_running_loop().time() < deadline:
        async with harness["session_factory"]() as db:
            state = (await service_ref(harness, db).get_task_detail(WS, uuid.UUID(result.task_ref.id)))[
                "lifecycle_state"
            ]
        if state in ("succeeded", "failed", "cancelled"):
            break
        await asyncio.sleep(0.05)
    assert state == "succeeded"
    assert len(stub.calls) == 1


async def test_cancel_running_stays_requested(harness, monkeypatch):
    gated = _GatedEntry()
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", gated)

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db)  # background dispatch
        await gated.started.wait()

        issued = await _service(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(result.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer=str(OWNER),
        )
        # HTTP-visible success is only a recorded request — never applied.
        assert issued.record.state is CommandState.REQUESTED
        assert "no cooperative abort channel" in (issued.detail or "")

        gated.gate.set()
    deadline = asyncio.get_running_loop().time() + 10
    state = "running"
    while asyncio.get_running_loop().time() < deadline:
        async with harness["session_factory"]() as db:
            detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(result.task_ref.id))
            state = detail["lifecycle_state"]
        if state in ("succeeded", "failed", "cancelled"):
            break
        await asyncio.sleep(0.05)
    assert state == "succeeded"


async def test_cancel_queued_applies_without_execution(harness, monkeypatch):
    stub = _EntryStub("should not run")
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", stub)

    captured: list[Any] = []

    def hold_spawn(coro):
        captured.append(coro)

    import hecate.execution.task_control as task_control_module

    monkeypatch.setattr(task_control_module, "_spawn_background", hold_spawn)

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db)  # dispatch held, never started
        issued = await _service(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(result.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer=str(OWNER),
        )
        assert issued.record.state is CommandState.REQUESTED

        await captured[0]  # run the held dispatch: it must apply the cancel

        record = await _service(harness, db).get_command(WS, issued.record.command_id)
        assert record.state is CommandState.APPLIED
        detail = await _service(harness, db).get_task_detail(WS, uuid.UUID(result.task_ref.id))
        assert detail["lifecycle_state"] == "cancelled"
        assert stub.calls == []  # no execution ever started


async def test_cancel_terminal_task_rejected(harness, monkeypatch):
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", _EntryStub())

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, wait=True)
        issued = await _service(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(result.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer=str(OWNER),
        )
        assert issued.record.state is CommandState.REJECTED
        assert "terminal" in (issued.detail or "")


async def test_unsupported_command_kind_rejected(harness, monkeypatch):
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", _EntryStub())

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, wait=True)
        issued = await _service(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(result.task_ref.id),
            kind=ControlCommandKind.PAUSE,
            issuer=str(OWNER),
        )
        assert issued.record.state is CommandState.REJECTED


async def test_expired_command_converges_on_read(harness, monkeypatch):
    gated = _GatedEntry()
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", gated)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db)
        await gated.started.wait()
        issued = await _service(harness, db).issue_command(
            workspace_id=WS,
            task_id=uuid.UUID(result.task_ref.id),
            kind=ControlCommandKind.CANCEL,
            issuer=str(OWNER),
            expires_at=past,
        )
        record = await _service(harness, db).get_command(WS, issued.record.command_id)
        assert record.state is CommandState.EXPIRED
        gated.gate.set()


async def test_events_workspace_isolation(harness, monkeypatch):
    from hecate.execution.task_control import TaskControlNotFoundError

    monkeypatch.setattr(TaskControlService, "_execute_via_entry", _EntryStub())

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, wait=True)
        with pytest.raises(TaskControlNotFoundError):
            await _service(harness, db).read_run_events(OTHER_WS, uuid.UUID(result.run_ref.id))


async def test_events_pagination_resumes_by_cursor(harness, monkeypatch):
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", _EntryStub())

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, wait=True)
        service = _service(harness, db)
        run_id = uuid.UUID(result.run_ref.id)

        first = await service.read_run_events(WS, run_id, limit=2)
        assert first.has_more is True
        second = await service.read_run_events(WS, run_id, cursor=first.next_cursor, limit=100)
        ids_first = {e.event_id for e in first.events}
        ids_second = {e.event_id for e in second.events}
        assert not ids_first & ids_second
        assert second.events[-1].payload_schema_ref == RUN_TERMINAL


async def test_pending_reconciliation_lists_tasks_and_labels_ledger(harness, monkeypatch):
    gated = _GatedEntry()
    monkeypatch.setattr(TaskControlService, "_execute_via_entry", gated)

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db)  # background execution held running
        await gated.started.wait()
        t_ref = task_ref_of(uuid.UUID(result.task_ref.id))
        # Enter reconciliation from a non-terminal state (terminals absorb);
        # mirrors an unknown execution outcome during a live run.
        await asyncio.to_thread(
            harness["suite"].store.apply_task_state,
            t_ref,
            TaskLifecycleState.RECONCILIATION_REQUIRED,
            recorded_at=datetime.now(UTC).isoformat(),
        )
        gated.gate.set()

        view = await _service(harness, db).pending_reconciliation(WS)
        assert view["ledger_source"] == "stub"
        assert view["actions"] == []
        assert any(item["task_ref"]["id"] == t_ref.id for item in view["tasks"])
        other_view = await _service(harness, db).pending_reconciliation(OTHER_WS)
        assert other_view["tasks"] == []


async def test_stream_submit_persists_run_events(harness, monkeypatch):
    """Stream-mode dispatch persists mapped envelopes (durable event view)."""

    from hecate.execution.entry_service import CorrelationResult, EntryExecutionService, ExecutionOutcome

    async def _fake_engine_stream():
        for chunk in ("partial ", "answer", " done"):
            yield {"type": "message", "content": chunk}
        yield {"type": "values", "state": {"suggested_questions": ["q1"]}}

    async def _fake_execute(self, *, correlation=None, **kwargs):
        return ExecutionOutcome(
            result=_fake_engine_stream(),
            correlation=CorrelationResult(status="registered"),
        )

    monkeypatch.setattr(EntryExecutionService, "execute", _fake_execute)

    async with harness["session_factory"]() as db:
        result = await _submit(harness, db, wait=True, stream=True)
        assert result.lifecycle_state == "succeeded"
        assert result.result == {"status": "succeeded", "content": "partial answer done"}

    # A fresh session (new "request") reads the same persisted stream.
    async with harness["session_factory"]() as db:
        page = await _service(harness, db).read_run_events(WS, uuid.UUID(result.run_ref.id))
        payloads = [e.payload for e in page.events]
        message_contents = [p["content"] for p in payloads if p.get("type") == "message"]
        assert message_contents == ["partial ", "answer", " done"]
        assert any(p.get("type") == "values" for p in payloads)
        assert page.events[-1].payload_schema_ref == RUN_TERMINAL
        sequences = [e.source_sequence for e in page.events]
        assert sequences == list(range(len(sequences)))
