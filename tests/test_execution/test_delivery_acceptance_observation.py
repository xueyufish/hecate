"""Run-level delivery/acceptance observation on the platform query surface.

The acceptance record is committed once with the durable terminal state
(shared evaluation, shared verdict). These tests cover the observation
closure on top of that fact: the whitelisted per-Run view surfaced by
Task/Run queries (all four recorded statuses, the honest no-record case,
truncation marking), run-level attribution across attempts (a historical
run never inherits the task's latest result), the dispatcher projection and
the outbox relay landing on the same run facts, relay recovery of a lost
dispatcher projection write, and the read side refusing to interpret a
corrupt or foreign-version record as a verdict.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

import pytest
from hecate_runtime.accepted_decision import canonical_json
from sqlalchemy import select

from hecate.contracts.execution.durable import TaskLifecycleState
from hecate.contracts.execution.identity import IdentityChain
from hecate.contracts.execution.references import BackendRef, RefKind
from hecate.execution.delivery_acceptance_view import acceptance_record_of, read_acceptance_view
from hecate.execution.task_control import TaskControlService
from hecate.execution.task_run_registry import TaskRunRegistry
from hecate.models.run import RunModel
from tests.test_execution.test_controller_loop_dispatch import (  # noqa: F401 - fixture registration
    OUTPUT_SCHEMA,
    OWNER,
    WS,
    ScriptedLoopPort,
    _dispatch_to_completion,
    harness,
)

_DECLARED = {
    "version": 3,
    "required_checks": [
        {"kind": "output_schema", "schema": OUTPUT_SCHEMA},
        {"kind": "content_contains", "value": "three items"},
    ],
}

_VIEW_FIELDS = {
    "status",
    "version",
    "declaration_sha256",
    "checker_version",
    "content_sha256",
    "record_sha256",
    "checks",
    "final_decision",
    "evaluated_by",
}


async def _task_detail(harness, record) -> dict:  # noqa: F811
    async with harness["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=harness["suite"].store,
            recorder=harness["suite"].recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=harness["session_factory"],
            dispatcher=None,
            worker=None,
        )
        return await service.get_task_detail(WS, uuid.UUID(record.task_ref.id))


async def _sole_run_id(harness) -> uuid.UUID:  # noqa: F811
    async with harness["session_factory"]() as db:
        rows = (await db.execute(select(RunModel).where(RunModel.workspace_id == WS))).scalars().all()
    (run,) = rows
    return run.id


async def _projection_of(harness, run_id: uuid.UUID) -> dict:  # noqa: F811
    async with harness["session_factory"]() as db:
        run = await db.get(RunModel, run_id)
    return dict(run.projection or {})


async def test_run_query_returns_whitelisted_view_of_recorded_verdict(harness) -> None:  # noqa: F811
    """The recorded verdict surfaces as a whitelisted per-Run view."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED

    detail = await _task_detail(harness, record)
    (run,) = detail["runs"]
    view = run["delivery_acceptance"]
    assert set(view) <= _VIEW_FIELDS
    assert view["status"] == "passed" and view["version"] == 3
    assert view["checker_version"] == "delivery-acceptance/1"
    assert view["evaluated_by"] == {"host": "platform", "workspace_id": str(WS), "submitted_by": str(OWNER)}
    assert [check["status"] for check in view["checks"]] == ["passed", "passed"]
    assert view["final_decision"]["decision_step_id"] == "agent-turn-000002"
    # The durable record's namespace references stay internal to the record.
    assert "task_ref" not in view and "run_ref" not in view
    # The raw record is consumed by the read-validated field, not
    # double-exposed inside the projection payload.
    assert "delivery_acceptance" not in run["projection"]
    # Delivery stays visible alongside the verdict.
    assert run["projection"]["result_preview"] == json.dumps({"answer": "three items in stock"})


@pytest.mark.parametrize(
    ("final_text", "acceptance", "expected"),
    [
        (json.dumps({"answer": "three items in stock"}), _DECLARED, "passed"),
        (json.dumps({"answer": "nothing relevant"}), _DECLARED, "acceptance_failed"),
        ("done", None, "not_declared"),
    ],
)
async def test_run_query_reports_each_recorded_status(harness, final_text, acceptance, expected) -> None:  # noqa: F811
    """passed / acceptance_failed / not_declared surface as recorded."""
    port = ScriptedLoopPort(final_text=final_text)
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=acceptance)
    expected_state = {"passed": "succeeded", "acceptance_failed": "failed", "not_declared": "succeeded"}[expected]
    assert record.lifecycle_state.value == expected_state
    detail = await _task_detail(harness, record)
    (run,) = detail["runs"]
    assert run["delivery_acceptance"]["status"] == expected
    if expected == "not_declared":
        # None fields are omitted from the whitelist, not echoed as null.
        assert "version" not in run["delivery_acceptance"]
        assert run["delivery_acceptance"]["checks"] == []


async def test_checker_outage_records_unverified_view(harness, monkeypatch) -> None:  # noqa: F811
    """A checker outage keeps the delivery and reports acceptance_unverified."""
    import hecate_runtime.delivery_acceptance as acceptance_module

    def unavailable(*args):
        raise RuntimeError("checker unavailable after admission")

    class CheckerOutagePort(ScriptedLoopPort):
        async def llm_invoke_structured(self, *args, **kwargs):
            async for answer in super().llm_invoke_structured(*args, **kwargs):
                if answer.get("content"):
                    monkeypatch.setattr(acceptance_module, "_validate_check_strict", unavailable)
                yield answer

    port = CheckerOutagePort(final_text="verified")
    record, _task_ref, _port = await _dispatch_to_completion(
        harness,
        port=port,
        acceptance={"required_checks": [{"kind": "content_contains", "value": "verified"}]},
    )
    assert record.lifecycle_state is TaskLifecycleState.FAILED
    detail = await _task_detail(harness, record)
    (run,) = detail["runs"]
    assert run["delivery_acceptance"]["status"] == "acceptance_unverified"
    assert run["projection"]["result_preview"] == "verified"  # delivery kept visible


async def test_truncated_delivery_preview_is_explicitly_marked(harness) -> None:  # noqa: F811
    """A long delivery marks the preview truncated; the digest stays whole."""
    long_text = json.dumps({"answer": "x" * 5000})
    port = ScriptedLoopPort(final_text=long_text)
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, output_schema=OUTPUT_SCHEMA)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    detail = await _task_detail(harness, record)
    (run,) = detail["runs"]
    assert run["projection"]["result_preview_truncated"] is True
    assert run["projection"]["result_preview"] == long_text[:2000]
    assert run["delivery_acceptance"]["content_sha256"] == hashlib.sha256(long_text.encode()).hexdigest()


async def test_runs_keep_their_own_verdicts_across_attempts(harness) -> None:  # noqa: F811
    """A historical run never inherits the task's latest recorded result."""
    # Attempt 1 (real dispatch): the content check fails and the verdict is
    # recorded on this run's projection by the real writer.
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "nothing relevant"}))
    first, t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert first.lifecycle_state is TaskLifecycleState.FAILED

    # Attempt 2 (a later retry of the same task): scaffolded with its own
    # recorded outcome the way the dispatcher's terminal write stores it.
    # The durable task result keeps only the LATEST fact.
    second_record = {
        "checker_version": "delivery-acceptance/1",
        "version": 3,
        "declaration_sha256": "a" * 64,
        "status": "passed",
        "checks": [{"kind": "content_contains", "status": "passed"}],
        "content_sha256": "b" * 64,
        "final_decision": {"decision_step_id": "agent-turn-000002", "content_digest": "c" * 64},
        "evaluated_by": {"host": "platform", "workspace_id": str(WS), "submitted_by": str(OWNER)},
        "task_ref": {"issuer": t_ref.issuer_domain, "id": t_ref.id},
        "run_ref": {"issuer": t_ref.issuer_domain, "id": "run-attempt-2"},
    }
    second_record["record_sha256"] = hashlib.sha256(canonical_json(second_record).encode()).hexdigest()

    async with harness["session_factory"]() as db:
        registry = TaskRunRegistry(db)
        task = await registry.get_task(uuid.UUID(t_ref.id), WS)
        first_run = (await registry.list_runs_for_task(task.id, WS))[0]
        second_run = await registry.create_run(
            task_id=task.id,
            workspace_id=WS,
            deployment_id=first_run.deployment_id,
            identity_chain=IdentityChain.from_dict(first_run.identity_chain),
            backend_run_ref=BackendRef(RefKind.RUN, "hecate", str(uuid.uuid4())),
        )
        second_record["run_ref"] = {"issuer": second_run.issuer_domain, "id": str(second_run.id)}
        _seal(second_record)
        await registry.update_projection(
            second_run.id,
            WS,
            projection={
                "state": "succeeded",
                "error": None,
                "error_category": None,
                "result_preview": json.dumps({"answer": "three items in stock"}),
                "delivery_acceptance": acceptance_record_of({"status": "succeeded", "acceptance": second_record}),
            },
        )
        await db.commit()

    detail = await _task_detail(harness, first)
    assert len(detail["runs"]) == 2
    by_attempt = {run["attempt_no"]: run for run in detail["runs"]}
    assert by_attempt[1]["delivery_acceptance"]["status"] == "acceptance_failed"
    assert by_attempt[2]["delivery_acceptance"]["status"] == "passed"
    # The durable task result holds only the FIRST attempt's fact here; the
    # per-run verdicts come from each run's own projection — never smeared
    # from the task-level latest result in either direction.
    assert (first.extra or {})["result"]["acceptance"]["status"] == "acceptance_failed"


async def test_relay_lands_the_same_run_facts_as_the_direct_projection(harness) -> None:  # noqa: F811
    """A replayed run_terminal rewrites the run projection identically."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector

    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED

    run_id = await _sole_run_id(harness)
    before = await _projection_of(harness, run_id)
    assert before["delivery_acceptance"]["status"] == "passed"

    projector = PlatformOutboxProjector(harness["store"], harness["session_factory"])
    relay = OutboxRelay(harness["store"].session_factory, projector, relay_key="observation")
    await relay.pump_once()

    after = await _projection_of(harness, run_id)
    assert after["delivery_acceptance"] == before["delivery_acceptance"]
    assert after["state"] == before["state"] == "succeeded"
    assert after["result_preview"] == before["result_preview"]
    assert after.get("result_preview_truncated", False) == before.get("result_preview_truncated", False)


async def test_relay_recovers_the_projection_after_a_lost_dispatcher_write(harness, monkeypatch) -> None:  # noqa: F811
    """Durable terminal committed, projection write lost: the relay recovers it."""
    from hecate_durable.worker import OutboxRelay

    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.execution.task_run_registry import TaskRunRegistryError

    class _ProjectionDownError(TaskRunRegistryError):
        pass

    calls = {"n": 0}
    original = TaskRunRegistry.update_projection

    async def fail_first(self, *args, **kwargs):  # noqa: ANN001, ANN003
        calls["n"] += 1
        if calls["n"] == 1:
            raise _ProjectionDownError("projection write lost (simulated crash)")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(TaskRunRegistry, "update_projection", fail_first)
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, _t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED  # durable terminal committed
    assert calls["n"] == 1  # ...but its projection write was lost
    monkeypatch.setattr(TaskRunRegistry, "update_projection", original)

    run_id = await _sole_run_id(harness)
    assert not (await _projection_of(harness, run_id))  # nothing projected yet

    projector = PlatformOutboxProjector(harness["store"], harness["session_factory"])
    relay = OutboxRelay(harness["store"].session_factory, projector, relay_key="recovery")
    await relay.pump_once()

    projection = await _projection_of(harness, run_id)
    assert projection["state"] == "succeeded"
    assert projection["delivery_acceptance"]["status"] == "passed"
    assert projection["delivery_acceptance"]["final_decision"]["decision_step_id"] == "agent-turn-000002"
    # The recovered fact equals what the direct projection would have stored.
    assert projection["delivery_acceptance"] == (record.extra or {})["result"]["acceptance"]


async def test_relay_preserves_the_recorded_view_when_payload_lacks_one(harness) -> None:  # noqa: F811
    """A later terminal payload without a verdict never erases the record."""
    from hecate.execution.event_relay import PlatformOutboxProjector

    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    run_id = await _sole_run_id(harness)
    before = await _projection_of(harness, run_id)
    assert before["delivery_acceptance"]["status"] == "passed"

    # A synthetic late run_terminal whose payload carries no acceptance key
    # (the backfill shape): state and preview update, the recorded verdict
    # of this run survives the whole-projection replace.
    envelope = {
        "contract_version": 1,
        "kind": "event",
        "event_id": f"late-terminal-{run_id}",
        "task_ref": {"kind": "task", "issuer_domain": t_ref.issuer_domain, "id": t_ref.id},
        "run_ref": {"kind": "run", "issuer_domain": "hecate.platform", "id": str(run_id)},
        "source_sequence": 9_000_001,
        "occurred_at": "2026-10-27T00:00:00+00:00",
        "received_at": "2026-10-27T00:00:00+00:00",
        "payload": {"event_type": "run_terminal", "status": "failed", "error": "late rewrite"},
        "payload_schema_ref": "hecate.platform.run_terminal/0",
    }
    await PlatformOutboxProjector(harness["store"], harness["session_factory"])(envelope)

    after = await _projection_of(harness, run_id)
    assert after["state"] == "failed"
    assert after["delivery_acceptance"] == before["delivery_acceptance"]
    assert after["result_preview"] == before["result_preview"]


async def test_backfill_finish_preserves_the_recorded_view(harness, monkeypatch) -> None:  # noqa: F811
    """The crash-window backfill outcome carries no acceptance key."""
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher, run_row_ref

    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock" + "x" * 3000}))
    record, t_ref, _port = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    assert record.lifecycle_state is TaskLifecycleState.SUCCEEDED
    run_id = await _sole_run_id(harness)
    before = await _projection_of(harness, run_id)
    assert before["delivery_acceptance"]["status"] == "passed"

    dispatcher = PlatformTaskDispatcher(harness["store"], harness["session_factory"])
    payload = await asyncio.to_thread(harness["store"].get_task_input, t_ref) or {}
    async with harness["session_factory"]() as db:
        context = dispatcher._context_of(t_ref, payload)
        run = await TaskRunRegistry(db).get_run(run_id, WS)
        # Neutralize the durable terminal write (already committed for this
        # task) so this _finish models only the projection half of the
        # crash-window backfill: an outcome rebuilt from the projection.
        monkeypatch.setattr(harness["store"], "apply_task_state", lambda *args, **kwargs: None)
        await dispatcher._finish(
            db,
            t_ref,
            run_row_ref(run),
            context,
            {"status": "succeeded", "content": str(before["result_preview"]), "error": None},
            run=run,
            expected_revision=record.revision + 1,
            lease=None,
        )

    after = await _projection_of(harness, run_id)
    assert after["delivery_acceptance"] == before["delivery_acceptance"]
    assert after["result_preview_truncated"] is True


async def test_read_side_refuses_to_interpret_broken_records() -> None:
    """Corrupt or foreign-version records never surface as a verdict."""
    from hecate_runtime.delivery_acceptance import evaluate_delivery, evaluation_declaration

    record = evaluate_delivery(
        "delivered",
        evaluation_declaration({"required_checks": [{"kind": "content_contains", "value": "delivered"}]}, None),
    )
    assert record["status"] == "passed"

    # A pristine record reads back as its whitelisted self.
    view = read_acceptance_view(record)
    assert view is not None and view["status"] == "passed"
    assert set(view) <= _VIEW_FIELDS

    # Tampered content digest: the record digest no longer matches the body.
    broken = read_acceptance_view({**record, "content_sha256": "0" * 64})
    assert broken is not None and broken["status"] == "uninterpretable"
    assert broken["recorded_status"] == "passed"

    # A checker version this build does not ship cannot be interpreted.
    foreign = read_acceptance_view({**record, "checker_version": "delivery-acceptance/99"})
    assert foreign is not None and foreign["status"] == "uninterpretable"
    assert foreign["checker_version"] == "delivery-acceptance/99"
    assert foreign["recorded_status"] == "passed"

    # Absence is the honest no-record; junk never becomes one.
    assert read_acceptance_view(None) is None
    junk = read_acceptance_view({"status": "passed"})
    assert junk is not None and junk["status"] == "uninterpretable"
    junk_list = read_acceptance_view([{"status": "passed"}])
    assert junk_list is not None and junk_list["status"] == "uninterpretable"


def _seal(record: dict) -> dict:
    record.pop("record_sha256", None)
    record["record_sha256"] = hashlib.sha256(canonical_json(record).encode()).hexdigest()
    return record


@pytest.mark.parametrize("field", ["run_ref", "task_ref", "workspace"])
async def test_query_refuses_a_valid_digest_for_another_execution(harness, field) -> None:  # noqa: F811
    """An intact record from another namespace cannot validate this Run."""
    port = ScriptedLoopPort(final_text=json.dumps({"answer": "three items in stock"}))
    record, _, _ = await _dispatch_to_completion(harness, port=port, acceptance=_DECLARED)
    run_id = await _sole_run_id(harness)
    projection = await _projection_of(harness, run_id)
    acceptance = projection["delivery_acceptance"]
    if field == "workspace":
        acceptance["evaluated_by"]["workspace_id"] = str(uuid.uuid4())
    else:
        acceptance[field]["id"] = str(uuid.uuid4())
    _seal(acceptance)
    async with harness["session_factory"]() as db:
        await TaskRunRegistry(db).update_projection(run_id, WS, projection=projection)
        await db.commit()
    (run,) = (await _task_detail(harness, record))["runs"]
    assert run["delivery_acceptance"]["status"] == "uninterpretable"


def test_nested_metadata_and_check_errors_are_whitelisted() -> None:
    """Schema diagnostics can contain content outside the delivery preview."""
    from hecate_runtime.delivery_acceptance import evaluate_delivery, evaluation_declaration

    secret = "PRIVATE-CONTENT-BEYOND-PREVIEW"
    record = evaluate_delivery(
        json.dumps("x" * 2100 + secret),
        evaluation_declaration(None, {"type": "string", "maxLength": 1}),
        final_decision={"decision_step_id": "turn-1", "content_digest": "a" * 64, "context": secret},
        evaluator={"host": "platform", "submitted_by": "user-1", "credentials": secret},
    )
    assert secret in record["checks"][0]["detail"]
    view = read_acceptance_view(record)
    assert view is not None and view["status"] == "acceptance_failed"
    assert secret not in json.dumps(view)
    assert set(view["final_decision"]) == {"decision_step_id", "content_digest"}
    assert set(view["evaluated_by"]) == {"host", "submitted_by"}


@pytest.mark.parametrize("change", [{"checks": "invalid"}, {"version": {}}, {"checks": [None]}])
def test_intact_but_malformed_record_cannot_render_as_passed(change) -> None:
    """Integrity validation alone does not validate a record's structure."""
    from hecate_runtime.delivery_acceptance import evaluate_delivery, evaluation_declaration

    record = evaluate_delivery("delivered", evaluation_declaration({"required_checks": []}, None))
    view = read_acceptance_view(_seal({**record, **change}))
    assert view is not None and view["status"] == "uninterpretable"


def test_terminal_copy_retains_malformed_record_as_evidence() -> None:
    """Malformed storage is distinguishable from a missing evaluation."""
    malformed = ["invalid stored acceptance"]
    assert acceptance_record_of({"acceptance": malformed}) == malformed
