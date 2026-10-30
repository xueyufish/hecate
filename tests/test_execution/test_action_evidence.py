"""Action evidence receipts over a real SQL ledger.

A receipt is an authorized, read-only projection of what the platform
already persisted. These tests drive the real ledger, the real digest
algorithms and the real visibility gates, because the point of the
feature is precisely that a receipt proves *content equality* — never
that the business goal passed, and never for work that was never
dispatched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import AcceptedDecisionRow, ActionIntentRow
from hecate_runtime.accepted_action_step import execution_id_for_action, tool_call_id_for_action
from hecate_runtime.accepted_decision import (
    AcceptedAction,
    AcceptedDecisionRecord,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.action_ledger import tool_arguments_digest

from hecate.contracts.execution.durable import ActionIntent, ActionOutcome, ActionOutcomeRecord
from hecate.contracts.execution.identity import IdentityChain, WorkloadIdentity
from hecate.contracts.execution.references import BackendRef, RefKind, task_ref
from hecate.execution.action_evidence import EVIDENCE_DIGEST_ALGORITHM
from hecate.execution.task_control import (
    TaskControlError,
    TaskControlNotFoundError,
    TaskControlService,
    TaskControlValidationError,
)
from hecate.execution.task_dispatcher import run_row_ref
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.agent import AgentModel
from hecate.models.agent_deployment import AccessMode, AgentDeploymentModel, BackendType
from hecate.models.agent_principal import AgentPrincipalModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.workspace import WorkspaceModel
from tests.conftest import DEFAULT_WORKSPACE_ID

OTHER_WS = uuid.UUID("00000000-0000-0000-0000-000000000031")

RECEIPT_FIELDS = {
    "task_id",
    "run_id",
    "action_key",
    "execution_id",
    "tool_call_id",
    "tool_name",
    "side_effect_class",
    "arguments_digest",
    "recovery_state",
    "outcome",
    "result_digest",
    "pending_reconciliation",
    "outcome_recorded_at",
    "result_state",
    "arguments_verification",
    "result_verification",
    "decision_ref",
    "read_at",
    "ledger_source",
}


@pytest.fixture
async def records(db_session, default_workspace, test_user_id, tmp_path):
    """Seed real platform associations plus a real SQL action ledger."""
    db_session.add(
        WorkspaceModel(
            id=OTHER_WS,
            org_id=default_workspace.org_id,
            name="other",
            slug="evidence-other",
        )
    )
    agent = AgentModel(workspace_id=DEFAULT_WORKSPACE_ID, name="evidence-agent")
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
            workload_id="hecate:evidence-test",
        ),
        audience="hecate:test",
    )
    registry = TaskRunRegistry(db_session)
    task = await registry.create_task(
        goal="evidence receipts",
        initiator_ref=chain.to_dict(),
        workspace_id=DEFAULT_WORKSPACE_ID,
    )
    runs = [
        await registry.create_run(
            task_id=task.id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            deployment_id=deployment.id,
            identity_chain=chain,
            backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
        )
        for _ in range(2)
    ]
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'evidence.db'}", source="platform")
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


def _record_action(
    records,
    run,
    key,
    *,
    outcome: str | None = None,
    payload=None,
    arguments_digest: str = "arguments-digest",
    tool_call_id: str | None = None,
    execution_id: str | None = None,
    tool_name: str | None = None,
):
    """Persist one action the way the runtime ledger does."""
    store = records["store"]
    store.record_intent_ex(
        ActionIntent(key, tool_name or f"tool.{key}", arguments_digest, ToolSideEffectClass.NON_IDEMPOTENT_WRITE),
        task_ref=task_ref("hecate", str(records["task"].id)),
        run_ref=run_row_ref(run),
        execution_id=execution_id if execution_id is not None else key,
        tool_call_id=tool_call_id if tool_call_id is not None else f"tc-{key}",
        session_id="session-should-not-leak",
    )
    receipt, token = store.claim_ex(key, holder="test-holder")
    assert receipt.claimed
    if outcome is not None:
        store.record_outcome_ex(
            ActionOutcomeRecord(
                key,
                ActionOutcome(outcome),
                result_digest=tool_arguments_digest(payload) if payload is not None else None,
            ),
            claim_token=token,
            result_payload=payload,
        )
    return key


def _seed_decision(records, run, step_id: str, actions: list[tuple[str, str, dict]]):
    """Persist one accepted decision with its canonical content digest."""
    record = AcceptedDecisionRecord(
        run_ref=RunRef(issuer_domain="hecate", run_id=str(run.id)),
        decision_step_id=step_id,
        actions=tuple(
            AcceptedAction(action_key=key, tool=tool, arguments=arguments) for key, tool, arguments in actions
        ),
    )
    digest = canonical_decision_digest(record)
    with records["store"].session_factory() as session, session.begin():
        session.add(
            AcceptedDecisionRow(
                id=uuid.uuid4().hex,
                issuer_domain=record.run_ref.issuer_domain,
                run_id=record.run_ref.run_id,
                decision_step_id=step_id,
                schema_version=record.schema_version,
                input_refs={},
                accepted_output={},
                actions=[action.to_dict() for action in record.actions],
                content_digest=digest,
                fence_holder="test-holder",
                fence_token=1,
                accepted_at=datetime.now(UTC).isoformat(),
            )
        )
    return digest


async def test_receipt_reports_recorded_facts_and_verifies_the_stored_result(records):
    """The happy path: recorded facts, the digest that was checked, and its verdict."""
    run = records["runs"][0]
    payload = {"rows": 3, "ok": True}
    key = _record_action(records, run, "action-1", outcome="succeeded", payload=payload)

    view = await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key)
    assert view["supported"] is True
    receipt = view["receipt"]
    assert set(receipt) == RECEIPT_FIELDS
    assert receipt["task_id"] == str(records["task"].id)
    assert receipt["run_id"] == str(run.id)
    assert receipt["action_key"] == key
    assert receipt["tool_name"] == f"tool.{key}"
    assert receipt["side_effect_class"] == ToolSideEffectClass.NON_IDEMPOTENT_WRITE.value
    assert receipt["outcome"] == "succeeded"
    assert receipt["result_state"] == "recorded"
    assert receipt["pending_reconciliation"] is False
    assert receipt["outcome_recorded_at"]
    assert receipt["read_at"]
    assert receipt["ledger_source"] == "core"
    verification = receipt["result_verification"]
    assert verification["status"] == "verified"
    assert verification["algorithm"] == EVIDENCE_DIGEST_ALGORITHM
    assert verification["subject"] == "recorded_result_payload"
    assert verification["recorded_digest"] == verification["computed_digest"] == receipt["result_digest"]


async def test_tampered_result_digest_is_reported_as_a_mismatch(records):
    """A digest that no longer matches the stored content is a mismatch, not a pass."""
    run = records["runs"][0]
    key = _record_action(records, run, "tampered", outcome="succeeded", payload={"value": 1})
    records["store"].record_outcome_ex(
        ActionOutcomeRecord(key, ActionOutcome.SUCCEEDED, result_digest="0" * 64),
        result_payload={"value": 2},
    )

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["result_verification"]["status"] == "mismatch"
    assert receipt["result_verification"]["recorded_digest"] == "0" * 64
    assert receipt["result_verification"]["computed_digest"] != "0" * 64


async def test_missing_result_is_unverifiable_rather_than_verified(records):
    """A claimed action without a recorded result must not read as checked."""
    run = records["runs"][0]
    key = _record_action(records, run, "claimed-only")

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["outcome"] is None
    assert receipt["result_state"] == "missing"
    assert receipt["result_digest"] is None
    assert receipt["result_verification"]["status"] == "unverifiable"
    assert receipt["result_verification"]["computed_digest"] is None


async def test_unknown_outcome_is_distinguished_from_a_missing_result(records):
    """An explicitly unknown outcome is its own state, not an absent one."""
    run = records["runs"][0]
    key = _record_action(records, run, "unknown-outcome", outcome="unknown")

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["outcome"] == "unknown"
    assert receipt["result_state"] == "unknown"
    assert receipt["recovery_state"] == "outcome_unknown"
    assert receipt["pending_reconciliation"] is True


async def test_accepted_decision_reference_is_linked_when_the_identity_derives(records):
    """The dispatched identity derives from (run, decision step, action) — exactly."""
    run = records["runs"][0]
    step_id = "agent-turn-000001"
    arguments = {"city": "Berlin"}
    digest = _seed_decision(records, run, step_id, [("act-1", "weather.lookup", arguments)])
    runtime_run_ref = RunRef(issuer_domain="hecate", run_id=str(run.id))
    tool_call_id = tool_call_id_for_action(runtime_run_ref, step_id, "act-1")
    execution_id = execution_id_for_action(runtime_run_ref, step_id, "act-1")
    _record_action(
        records,
        run,
        execution_id,
        outcome="succeeded",
        payload={"temp": 21},
        arguments_digest=tool_arguments_digest(arguments),
        tool_name="weather.lookup",
        tool_call_id=tool_call_id,
        execution_id=execution_id,
    )

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, execution_id))["receipt"]
    assert receipt["decision_ref"]["status"] == "linked"
    assert receipt["decision_ref"]["decision_step_id"] == step_id
    assert receipt["decision_ref"]["content_digest"] == digest
    arguments_verification = receipt["arguments_verification"]
    assert arguments_verification["status"] == "verified"
    assert arguments_verification["subject"] == "accepted_decision_action_arguments"
    assert arguments_verification["recorded_digest"] == arguments_verification["computed_digest"]


async def test_action_without_a_decision_reports_no_reference(records):
    """An action outside the accepted-decision chain has no reference to claim."""
    run = records["runs"][0]
    key = _record_action(records, run, "no-decision", outcome="succeeded", payload={"x": 1})

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["decision_ref"] == {
        "status": "not_recorded",
        "decision_step_id": None,
        "content_digest": None,
    }
    assert receipt["arguments_verification"]["status"] == "unverifiable"


async def test_arguments_digest_mismatch_is_reported_against_the_decision(records):
    """Re-hashing the accepted arguments must not be able to fake a match."""
    run = records["runs"][0]
    step_id = "agent-turn-000001"
    arguments = {"city": "Berlin"}
    _seed_decision(records, run, step_id, [("act-1", "weather.lookup", arguments)])
    runtime_run_ref = RunRef(issuer_domain="hecate", run_id=str(run.id))
    tool_call_id = tool_call_id_for_action(runtime_run_ref, step_id, "act-1")
    execution_id = execution_id_for_action(runtime_run_ref, step_id, "act-1")
    _record_action(
        records,
        run,
        execution_id,
        outcome="succeeded",
        payload={"temp": 21},
        arguments_digest="f" * 64,
        tool_name="weather.lookup",
        tool_call_id=tool_call_id,
        execution_id=execution_id,
    )

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, execution_id))["receipt"]
    assert receipt["decision_ref"]["status"] == "linked"
    assert receipt["arguments_verification"]["status"] == "mismatch"
    assert receipt["arguments_verification"]["recorded_digest"] == "f" * 64


async def test_action_key_with_special_characters_is_addressable(records):
    """Caller-defined keys may hold characters a path segment could not carry."""
    run = records["runs"][0]
    key = "act/键 with space & ?#%"
    _record_action(records, run, key, outcome="succeeded", payload={"ok": True})

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["action_key"] == key


async def test_action_of_another_run_is_not_found(records):
    """A foreign action is indistinguishable from an absent one."""
    old, latest = records["runs"]
    _record_action(records, old, "belongs-to-old")
    _record_action(records, latest, "belongs-to-latest")

    with pytest.raises(TaskControlNotFoundError):
        await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, latest.id, "belongs-to-old")
    with pytest.raises(TaskControlNotFoundError):
        await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, latest.id, "never-existed")


async def test_foreign_run_is_not_found_before_any_ledger_read(records):
    """A run outside the workspace never reaches the ledger."""
    run = records["runs"][0]
    key = _record_action(records, run, "hidden")

    with pytest.raises(TaskControlNotFoundError):
        await _service(records).get_run_action_evidence(OTHER_WS, run.id, key)


async def test_reading_a_receipt_changes_no_ledger_or_event_state(records):
    """A read must not claim, complete, replay or emit anything."""
    run = records["runs"][0]
    key = _record_action(records, run, "read-only", outcome="succeeded", payload={"ok": True})
    before = records["store"].list_run_actions(run_row_ref(run))
    events_before = len(records["store"].read_events(run_row_ref(run)).events)

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["recovery_state"] == "claimed"

    after = records["store"].list_run_actions(run_row_ref(run))
    assert len(records["store"].read_events(run_row_ref(run)).events) == events_before
    assert [(row["action_key"], row["state"], row.get("active_claim")) for row in after] == [
        (row["action_key"], row["state"], row.get("active_claim")) for row in before
    ]
    assert after[0]["last_outcome"]["outcome"] == "succeeded"


async def test_receipt_payload_carries_no_sensitive_fields(records):
    """Raw content, sessions and claim internals never reach the response."""
    run = records["runs"][0]
    payload = {"secret": "sk-LEAK"}
    key = _record_action(records, run, "sensitive", outcome="succeeded", payload=payload)

    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    rendered = repr(receipt)
    assert "sk-LEAK" not in rendered
    assert "session-should-not-leak" not in rendered
    for forbidden in ("claim_token", "result_payload", "arguments", "result_ref", "session_id"):
        assert forbidden not in receipt


async def test_unsupported_store_is_labeled_instead_of_failing(records):
    """A store without the query capability says so; it never fakes an empty receipt."""

    class _StubStore:
        pass

    view = await _service(records, store=_StubStore()).get_run_action_evidence(
        DEFAULT_WORKSPACE_ID, records["runs"][0].id, "anything"
    )
    assert view["supported"] is False
    assert "receipt" not in view
    assert view["run_id"] == str(records["runs"][0].id)


async def test_query_failure_is_explicit_and_redacts_driver_detail(records, caplog):
    """A storage failure is an error, and its text never reaches the caller."""

    class _BrokenStore:
        def get_run_action(self, run_ref, action_key):
            raise RuntimeError("sqlite:///secret/path.db parameters=(id) SELECT payload")

    with pytest.raises(TaskControlError) as excinfo:
        await _service(records, store=_BrokenStore()).get_run_action_evidence(
            DEFAULT_WORKSPACE_ID, records["runs"][0].id, "anything"
        )
    message = str(excinfo.value)
    assert "secret/path.db" not in message
    assert "SELECT payload" not in message
    assert "action evidence query failed" in message


async def test_blank_action_key_is_a_validation_error(records):
    """An empty key never reaches the ledger."""
    with pytest.raises(TaskControlValidationError):
        await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, records["runs"][0].id, "")


@pytest.mark.parametrize("field", ["actions", "input_refs", "accepted_output", "schema_version", "content_digest"])
async def test_corrupt_accepted_decision_cannot_be_published_as_linked(records, field):
    """A derivable action ID does not authenticate a damaged decision."""
    run = records["runs"][0]
    step = "agent-turn-000001"
    arguments = {"city": "Berlin"}
    _seed_decision(records, run, step, [("act-1", "weather.lookup", arguments)])
    ref = RunRef("hecate", str(run.id))
    key = execution_id_for_action(ref, step, "act-1")
    _record_action(
        records,
        run,
        key,
        tool_name="weather.lookup",
        outcome="succeeded",
        payload={"temp": 21},
        arguments_digest=tool_arguments_digest(arguments),
        tool_call_id=tool_call_id_for_action(ref, step, "act-1"),
        execution_id=key,
    )
    with records["store"].session_factory() as db, db.begin():
        row = db.query(AcceptedDecisionRow).one()
        setattr(
            row,
            field,
            {
                "actions": [{"action_key": "act-1", "tool": "weather.lookup", "arguments": {"city": "changed"}}],
                "input_refs": {"tampered": True},
                "accepted_output": {"tampered": True},
                "schema_version": 999,
                "content_digest": "f" * 64,
            }[field],
        )
    with pytest.raises(TaskControlError):
        await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key)


@pytest.mark.parametrize("field", ["action_name", "execution_id", "action_key"])
async def test_decision_link_requires_the_full_dispatched_identity(records, field):
    """A tool-call ID alone cannot authenticate another tool or ledger key."""
    run = records["runs"][0]
    step = "agent-turn-000001"
    arguments = {"city": "Berlin"}
    _seed_decision(records, run, step, [("act-1", "weather.lookup", arguments)])
    ref = RunRef("hecate", str(run.id))
    key = execution_id_for_action(ref, step, "act-1")
    _record_action(
        records,
        run,
        key,
        tool_name="weather.lookup",
        outcome="succeeded",
        payload={},
        arguments_digest=tool_arguments_digest(arguments),
        tool_call_id=tool_call_id_for_action(ref, step, "act-1"),
        execution_id=key,
    )
    with records["store"].session_factory() as db, db.begin():
        row = db.get(ActionIntentRow, key)
        setattr(row, field, None if field == "execution_id" else "different")
    lookup_key = "different" if field == "action_key" else key
    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, lookup_key))["receipt"]
    assert receipt["decision_ref"]["status"] == "not_recorded"
    assert receipt["arguments_verification"]["status"] == "unverifiable"


async def test_foreign_task_issuer_cannot_publish_an_action_receipt(records):
    """Task UUID equality is insufficient across issuing domains."""
    run = records["runs"][0]
    key = _record_action(records, run, "foreign-issuer", outcome="succeeded", payload={})
    with records["store"].session_factory() as db, db.begin():
        db.get(ActionIntentRow, key).task_issuer = "another-domain"
    with pytest.raises(TaskControlNotFoundError):
        await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key)


async def test_reference_only_result_is_not_rehashed_as_json_null(records):
    """A digest of unavailable artifact content cannot be checked against None."""
    run = records["runs"][0]
    key = _record_action(records, run, "artifact-only")
    records["store"].record_outcome_ex(
        ActionOutcomeRecord(
            key,
            ActionOutcome.SUCCEEDED,
            result_digest="a" * 64,
            result_ref=BackendRef(RefKind.ARTIFACT, "object-store", "sensitive-location"),
        ),
    )
    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["result_state"] == "missing"
    assert receipt["result_verification"]["status"] == "unverifiable"
    assert receipt["result_verification"]["computed_digest"] is None
    assert "sensitive-location" not in repr(receipt)


@pytest.mark.parametrize("payload", [float("inf"), {"bad": float("nan")}])
async def test_noncanonical_stored_result_is_unsupported(records, payload):
    """Legacy nonfinite JSON must not accidentally display as verified."""
    run = records["runs"][0]
    key = _record_action(records, run, "legacy-shape")
    records["store"].record_outcome_ex(
        ActionOutcomeRecord(key, ActionOutcome.SUCCEEDED, result_digest="a" * 64), result_payload=payload
    )
    receipt = (await _service(records).get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert receipt["result_verification"]["status"] == "unsupported"
    assert receipt["result_verification"]["computed_digest"] is None


async def test_latest_outcome_is_read_again_without_rewriting_history(records):
    """A receipt is a new observation of the latest outcome, not a new ledger."""
    run = records["runs"][0]
    key = _record_action(records, run, "changing-result", outcome="succeeded", payload={"version": 1})
    service = _service(records)
    first = (await service.get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    payload = {"version": 2}
    records["store"].record_outcome_ex(
        ActionOutcomeRecord(key, ActionOutcome.SUCCEEDED, result_digest=tool_arguments_digest(payload)),
        result_payload=payload,
    )
    second = (await service.get_run_action_evidence(DEFAULT_WORKSPACE_ID, run.id, key))["receipt"]
    assert first["result_digest"] != second["result_digest"]
    assert first["outcome_recorded_at"] != second["outcome_recorded_at"]
    assert second["result_verification"]["status"] == "verified"
