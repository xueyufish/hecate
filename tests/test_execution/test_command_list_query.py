"""Task-command service queries over platform fixtures and an isolated durable store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from sqlalchemy import event, select

from hecate.contracts.execution.durable import (
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
)
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, task_ref
from hecate.execution.task_control import (
    TaskControlError,
    TaskControlNotFoundError,
    TaskControlService,
    task_ref_of,
)
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID

OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000031")

COMMAND_STATES = [
    CommandState.REQUESTED,
    CommandState.ACKNOWLEDGED,
    CommandState.APPLIED,
    CommandState.REJECTED,
    CommandState.EXPIRED,
]


# --- service view over a real SQL store --------------------------------------


@pytest.fixture
async def records(db_session, default_workspace, test_user_id, tmp_path):
    """Seed real platform associations and a SQLite durable store."""
    db_session.add(
        WorkspaceModel(
            id=OTHER_WS,
            org_id=default_workspace.org_id,
            name="other",
            slug="command-query-other",
        )
    )
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="command-query-agent")
    db_session.add(agent)
    await db_session.flush()
    version = AgentVersionModel(
        agent_id=agent.id,
        version=1,
        config_snapshot={"model": "stub"},
        content_hash="a" * 64,
    )
    db_session.add(version)
    await db_session.flush()
    principal = AgentPrincipalModel(
        id=agent.id,
        agent_id=agent.id,
        workspace_id=DEFAULT_WORKSPACE_ID,
        organization_id=default_workspace.org_id,
        owner_user_id=test_user_id,
    )
    deployment = AgentDeploymentModel(
        agent_id=agent.id,
        agent_version_id=version.id,
        workspace_id=DEFAULT_WORKSPACE_ID,
        backend_type=BackendType.BUILTIN,
        access_mode=AccessMode.IN_PROCESS,
        issuer_domain="hecate",
        capability_snapshot={},
        axes_harness="hecate",
        axes_environment="none",
        axes_tool_execution="hecate_gateway",
        is_default=True,
    )
    db_session.add_all([principal, deployment])
    await db_session.flush()
    chain = IdentityChain(
        initiator=str(test_user_id),
        principal_id=str(principal.id),
        workload=WorkloadIdentity(
            deployment=BackendRef(RefKind.DEPLOYMENT, "hecate", str(deployment.id)),
            workload_id="hecate:test",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="query commands",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'commands.db'}", source="platform")
    store.create_schema()
    try:
        yield {"db": db_session, "task": task, "store": store}
    finally:
        store.dispose()


def _service(records, *, store=None, recorder=None):
    return TaskControlService(
        records["db"],
        store=records["store"] if store is None else store,
        recorder=records["store"] if recorder is None else recorder,
        backend="postgres",
        ledger_source="core",
    )


def _seed_command(records, command_id, state, *, issued_at=None, expires_at=None, task_ref_override=None) -> None:
    records["store"].record(
        ControlCommandRecord(
            command_id=command_id,
            kind=ControlCommandKind.CANCEL,
            issuer="user-1",
            task_ref=task_ref_override or task_ref_of(records["task"].id),
            issued_at=issued_at or datetime.now(UTC).isoformat(),
            expires_at=expires_at,
            state=state,
            extra={"workspace_id": str(DEFAULT_WORKSPACE_ID)},
        )
    )


async def test_service_returns_recorded_states_in_order(records):
    now = datetime.now(UTC)
    for index, state in enumerate(COMMAND_STATES):
        _seed_command(records, f"cmd-{index}", state, issued_at=(now - timedelta(minutes=10 - index)).isoformat())
    view = await _service(records).list_task_commands(DEFAULT_WORKSPACE_ID, records["task"].id)
    assert view["total"] == 5
    assert [record["command_id"] for record in view["items"]] == [f"cmd-{i}" for i in range(4, -1, -1)]
    assert [record["state"] for record in view["items"]] == list(reversed(COMMAND_STATES))


async def test_service_whitelists_task_visibility(records):
    task_id = records["task"].id
    for case in ("missing", "foreign_workspace", "deleted", "moved_out"):
        workspace, query_id = DEFAULT_WORKSPACE_ID, task_id
        if case == "missing":
            query_id = uuid.uuid4()
        elif case == "foreign_workspace":
            workspace = OTHER_WS
        elif case == "deleted":
            records["task"].deleted = True
        elif case == "moved_out":
            records["task"].workspace_id = OTHER_WS

        class StubNeverRead:
            calls = 0

            def list_task_commands(self, *args):
                """Any attempted lookup would cross the denied boundary."""
                self.calls += 1
                raise AssertionError("unauthorized command read")

        await records["db"].flush()
        store = StubNeverRead()
        with pytest.raises(TaskControlNotFoundError, match=f"task {query_id} not found in workspace"):
            await _service(records, recorder=store).list_task_commands(workspace, query_id)
        assert store.calls == 0
        records["task"].deleted = False
        records["task"].workspace_id = DEFAULT_WORKSPACE_ID
        await records["db"].flush()


async def test_service_storage_failure_is_explicit_and_redacted(records, caplog):
    class StubSecretFailure:
        def list_task_commands(self, *args):
            """Exercise a driver-shaped failure carrying diagnostics."""
            raise RuntimeError("postgresql://user:password@host/db SQL parameters: sk-SECRET")

    with pytest.raises(TaskControlError) as caught:
        await _service(records, recorder=StubSecretFailure()).list_task_commands(
            DEFAULT_WORKSPACE_ID, records["task"].id
        )
    assert str(caught.value) == f"command list query failed for task {records['task'].id}"
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert str(records["task"].id) in caplog.text
    assert "RuntimeError" in caplog.text
    for secret in ("password", "sk-SECRET", "SQL parameters"):
        assert secret not in str(caught.value)
        assert secret not in caplog.text


def _durable_snapshot(store):
    with store.engine.connect() as connection:
        return {
            table.name: [
                dict(row)
                for row in connection.execute(
                    select(table).order_by(*table.primary_key.columns),
                ).mappings()
            ]
            for table in DurableBase.metadata.sorted_tables
        }


async def test_service_list_is_observed_read_only(records):
    store = records["store"]
    now = datetime.now(UTC)
    _seed_command(
        records,
        "lapsed-requested",
        CommandState.REQUESTED,
        issued_at=(now - timedelta(days=2)).isoformat(),
        expires_at=(now - timedelta(days=1)).isoformat(),
    )
    _seed_command(records, "applied", CommandState.APPLIED, issued_at=(now - timedelta(days=1)).isoformat())
    before = _durable_snapshot(store)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(" ".join(statement.upper().split()))

    event.listen(store.engine, "before_cursor_execute", capture)
    try:
        for offset in range(2):
            await _service(records).list_task_commands(DEFAULT_WORKSPACE_ID, records["task"].id, limit=1, offset=offset)
    finally:
        event.remove(store.engine, "before_cursor_execute", capture)
    assert all(statement.startswith(("SELECT", "BEGIN")) for statement in statements)
    counts = [statement for statement in statements if "COUNT(" in statement]
    pages = [statement for statement in statements if "LIMIT" in statement]
    assert len(counts) == len(pages) == 2
    for statement in counts + pages:
        assert "WHERE" in statement
        assert all(column in statement for column in ("WORKSPACE_ID", "TASK_ISSUER", "TASK_ID"))
    assert all("ORDER BY" in statement and "OFFSET" in statement for statement in pages)
    assert _durable_snapshot(store) == before
    lapsed = store.get("lapsed-requested")
    assert lapsed is not None and lapsed.state is CommandState.REQUESTED


async def test_service_uses_the_visible_task_issuer(records):
    records["task"].issuer_domain = "registered.host"
    await records["db"].flush()
    _seed_command(
        records,
        "correct-issuer",
        CommandState.REQUESTED,
        task_ref_override=task_ref("registered.host", str(records["task"].id)),
    )
    _seed_command(records, "same-id-other-issuer", CommandState.REQUESTED)
    view = await _service(records).list_task_commands(DEFAULT_WORKSPACE_ID, records["task"].id)
    assert view["total"] == 1
    assert [record["command_id"] for record in view["items"]] == ["correct-issuer"]
