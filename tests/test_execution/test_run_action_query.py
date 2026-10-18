"""Run action-query contracts over the shared ORM fixture and a real SQL ledger."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import Base as DurableBase
from sqlalchemy import event, select

from hecate.contracts.execution.durable import (
    ActionIntent,
    ActionOutcome,
    ActionOutcomeRecord,
    CommandState,
    ControlCommandKind,
    ControlCommandRecord,
    TaskLifecycleState,
)
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, task_ref
from hecate.execution.task_control import TaskControlError, TaskControlNotFoundError, TaskControlService
from hecate.execution.task_dispatcher import run_row_ref
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.run import RunModel
from hecate.models.task import TaskModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID

OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000021")
WHITELIST = {
    "action_key",
    "execution_id",
    "tool_call_id",
    "tool_name",
    "arguments_digest",
    "side_effect_class",
    "state",
    "outcome",
    "result_digest",
    "pending_reconciliation",
}


@pytest.fixture
async def records(db_session, default_workspace, test_user_id, tmp_path):
    """Seed real platform associations without creating a second ORM engine."""
    db_session.add(
        WorkspaceModel(
            id=OTHER_WS,
            org_id=default_workspace.org_id,
            name="other",
            slug="run-query-other",
        )
    )
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="run-actions-agent")
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
        goal="query actions",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    runs = []
    for _ in range(2):
        runs.append(
            await registry.create_run(
                task_id=task.id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                deployment_id=deployment.id,
                identity_chain=chain,
                backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
            )
        )
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'ledger.db'}", source="platform")
    store.create_schema()
    try:
        yield {"db": db_session, "task": task, "runs": runs, "store": store}
    finally:
        store.dispose()


def _service(records, *, store=None, ledger_source="core"):
    return TaskControlService(
        records["db"],
        store=records["store"] if store is None else store,
        recorder=records["store"],
        backend="postgres",
        ledger_source=ledger_source,
    )


def _record_action(records, run, key, *, outcome=None, payload=None, run_reference=None):
    store = records["store"]
    store.record_intent_ex(
        ActionIntent(key, f"tool.{key}", "arguments-digest", ToolSideEffectClass.NON_IDEMPOTENT_WRITE),
        task_ref=task_ref("hecate", str(records["task"].id)),
        run_ref=run_row_ref(run) if run_reference is None else run_reference,
        execution_id=f"exec-{key}",
        tool_call_id=f"tc-{key}",
        session_id="session-should-not-leak",
    )
    receipt, token = store.claim_ex(key, holder="test-holder")
    assert receipt.claimed
    if outcome is not None:
        store.record_outcome_ex(
            ActionOutcomeRecord(key, ActionOutcome(outcome), result_digest=f"rd-{key}"),
            claim_token=token,
            result_payload=payload if payload is not None else {"secret": "sk-LEAK"},
        )


async def test_runs_are_isolated_and_states_match_ledger(records):
    old, latest = records["runs"]
    for name, outcome in (("succeeded", "succeeded"), ("unknown", "unknown"), ("claimed", None)):
        _record_action(records, old, name, outcome=outcome)
    _record_action(records, latest, "failed", outcome="failed")
    reference = task_ref("hecate", str(records["task"].id))
    for state in (TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING, TaskLifecycleState.SUCCEEDED):
        records["store"].apply_task_state(reference, state)
    view1 = await _service(records).list_run_actions(DEFAULT_WORKSPACE_ID, old.id)
    view2 = await _service(records).list_run_actions(DEFAULT_WORKSPACE_ID, latest.id)
    assert view1["supported"] is view2["supported"] is True
    by_key = {action["action_key"]: action for action in view1["actions"]}
    assert set(by_key) == {"succeeded", "unknown", "claimed"}
    assert by_key["succeeded"]["outcome"] == "succeeded"
    assert by_key["unknown"]["state"] == "outcome_unknown"
    assert by_key["unknown"]["outcome"] == "unknown"
    assert by_key["unknown"]["pending_reconciliation"] is True
    assert by_key["claimed"]["state"] == "claimed"
    assert by_key["claimed"]["outcome"] is None
    assert by_key["claimed"]["pending_reconciliation"] is True
    assert [action["action_key"] for action in view2["actions"]] == ["failed"]
    assert view2["actions"][0]["outcome"] == "failed"
    assert view2["actions"][0]["pending_reconciliation"] is True
    assert records["store"].get_task_state(reference).lifecycle_state is TaskLifecycleState.SUCCEEDED


async def test_historical_run_uses_platform_ledger_identity_not_backend_session(records):
    old, latest = records["runs"]
    assert old.backend_ref["id"] != str(old.id)
    _record_action(records, old, "old-action", outcome="succeeded")
    _record_action(records, latest, "new-action", outcome="succeeded")
    _record_action(
        records,
        old,
        "engine-session-decoy",
        outcome="succeeded",
        run_reference=BackendRef.from_dict(old.backend_ref),
    )
    view = await _service(records).list_run_actions(DEFAULT_WORKSPACE_ID, old.id)
    assert [action["action_key"] for action in view["actions"]] == ["old-action"]


@pytest.mark.parametrize("case", ["missing_run", "foreign_run", "deleted_run", "deleted_task", "foreign_task"])
async def test_run_and_parent_must_be_visible_before_any_ledger_lookup(records, case):
    run = records["runs"][0]
    query_workspace = DEFAULT_WORKSPACE_ID
    query_id = run.id
    if case == "missing_run":
        query_id = uuid.uuid4()
    elif case == "foreign_run":
        query_workspace = OTHER_WS
    elif case == "deleted_run":
        run.deleted = True
    elif case == "deleted_task":
        records["task"].deleted = True
    elif case == "foreign_task":
        records["task"].workspace_id = OTHER_WS
    await records["db"].flush()

    class StubNeverRead:
        calls = 0

        def list_run_actions(self, ref):
            """Any attempted lookup would cross the denied resource boundary."""
            self.calls += 1
            raise AssertionError("unauthorized ledger read")

    store = StubNeverRead()
    with pytest.raises(TaskControlNotFoundError, match=f"run {query_id} not found in workspace"):
        await _service(records, store=store).list_run_actions(query_workspace, query_id)
    assert store.calls == 0


async def test_whitelist_excludes_sensitive_and_internal_fields(records):
    run = records["runs"][0]
    _record_action(records, run, "secret-action", outcome="succeeded")
    view = await _service(records).list_run_actions(DEFAULT_WORKSPACE_ID, run.id)
    action = view["actions"][0]
    assert set(action) == WHITELIST
    assert action["tool_name"] == "tool.secret-action"
    assert action["arguments_digest"] == "arguments-digest"
    assert action["result_digest"] == "rd-secret-action"
    text = json.dumps(view)
    for forbidden in ("sk-LEAK", "session-should-not-leak", "claim_token", "result_payload", "active_claim"):
        assert forbidden not in text


async def test_real_empty_ledger_and_unsupported_are_distinct(records):
    run = records["runs"][0]
    supported = await _service(records).list_run_actions(DEFAULT_WORKSPACE_ID, run.id)

    class StubNoActionQuery:
        """A store without the optional action-query capability."""

    unsupported = await _service(records, store=StubNoActionQuery(), ledger_source="stub").list_run_actions(
        DEFAULT_WORKSPACE_ID,
        run.id,
    )
    assert supported["actions"] == unsupported["actions"] == []
    assert supported["supported"] is True
    assert supported["ledger_source"] == "core"
    assert unsupported["supported"] is False
    assert unsupported["ledger_source"] == "stub"


@pytest.mark.parametrize("exception_type", [RuntimeError, TaskControlError])
async def test_storage_failure_is_explicit_and_redacts_diagnostics(records, caplog, exception_type):
    class StubSecretFailure:
        def list_run_actions(self, ref):
            """Exercise both driver and application-shaped adapter errors."""
            raise exception_type("postgresql://user:password@host/db SQL parameters: sk-SECRET")

    run = records["runs"][0]
    with pytest.raises(TaskControlError) as caught:
        await _service(records, store=StubSecretFailure()).list_run_actions(DEFAULT_WORKSPACE_ID, run.id)
    assert str(caught.value) == f"action ledger query failed for run {run.id}"
    assert isinstance(caught.value.__cause__, exception_type)
    assert str(run.id) in caplog.text
    assert exception_type.__name__ in caplog.text
    for secret in ("password", "sk-SECRET", "SQL parameters"):
        assert secret not in str(caught.value)
        assert secret not in caplog.text


async def test_malformed_ledger_result_is_an_explicit_fault(records):
    class StubMalformedStore:
        def list_run_actions(self, ref):
            """A broken adapter must not masquerade as an empty successful query."""
            return ["secret malformed row"]

    with pytest.raises(TaskControlError, match="action ledger query failed"):
        await _service(records, store=StubMalformedStore()).list_run_actions(
            DEFAULT_WORKSPACE_ID,
            records["runs"][0].id,
        )


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


async def test_success_denial_and_failure_leave_existing_facts_unchanged(records):
    store, db = records["store"], records["db"]
    run = records["runs"][0]
    reference = task_ref("hecate", str(records["task"].id))
    store.apply_task_state(reference, TaskLifecycleState.QUEUED, event_run_ref=run_row_ref(run))
    store.apply_task_state(reference, TaskLifecycleState.RUNNING, event_run_ref=run_row_ref(run))
    _record_action(records, run, "claimed-action")
    _record_action(records, run, "completed-action", outcome="succeeded")
    command = ControlCommandRecord(
        command_id="expired-but-requested",
        kind=ControlCommandKind.CANCEL,
        issuer="test-user",
        task_ref=reference,
        run_ref=run_row_ref(run),
        issued_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
        expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        state=CommandState.REQUESTED,
        extra={"workspace_id": str(DEFAULT_WORKSPACE_ID)},
    )
    store.record(command)
    await db.flush()
    before_durable = _durable_snapshot(store)
    before_task = dict((await db.execute(select(TaskModel.__table__))).mappings().one())
    before_runs = [
        dict(row)
        for row in (
            await db.execute(
                select(RunModel.__table__).order_by(RunModel.attempt_no),
            )
        ).mappings()
    ]
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().split()[0].upper())

    engines = [db.bind.sync_engine, store.engine]
    for engine in engines:
        event.listen(engine, "before_cursor_execute", capture)
    try:
        await _service(records).list_run_actions(DEFAULT_WORKSPACE_ID, run.id)
        with pytest.raises(TaskControlNotFoundError):
            await _service(records).list_run_actions(OTHER_WS, run.id)

        class StubFailingQuery:
            def list_run_actions(self, ref):
                """Do not change any facts before reporting unavailable."""
                raise OSError("unavailable")

        with pytest.raises(TaskControlError):
            await _service(records, store=StubFailingQuery()).list_run_actions(DEFAULT_WORKSPACE_ID, run.id)
    finally:
        for engine in engines:
            event.remove(engine, "before_cursor_execute", capture)
    assert "SELECT" in statements
    assert set(statements) <= {"SELECT", "BEGIN"}
    assert _durable_snapshot(store) == before_durable
    assert dict((await db.execute(select(TaskModel.__table__))).mappings().one()) == before_task
    assert [
        dict(row)
        for row in (
            await db.execute(
                select(RunModel.__table__).order_by(RunModel.attempt_no),
            )
        ).mappings()
    ] == before_runs
    assert store.get_task_state(reference).lifecycle_state is TaskLifecycleState.RUNNING
    assert store.get(command.command_id).state is CommandState.REQUESTED
