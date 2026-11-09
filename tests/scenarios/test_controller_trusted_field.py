"""Trusted field provenance end to end on the real platform Controller dispatch.

Every case runs the production runtime port, governance facade, durable
action ledger, accepted-decision chain and input revision store; only the
business registry (with NO provenance logic — every write that reaches it
lands) and the model seam are synthetic. Blocks are therefore the production
guard's verdicts. Coverage: each scripted case under the profile; the same
scripts without the profile (the guessed and substituted values land);
swapped legitimate values, including the historical baseline's fixed
answer used as a guess; replays across workspaces, Tasks, Runs and
resources; argument and message provenance claims; a wake after a full host
rebuild; redelivery after a crash (a completed write is never re-executed,
a recorded refusal replays as the same stop); an unknown write outcome that
stays pending reconciliation; old decisions that gain no authority from
later answers; and fail-fast declaration and execution-path checks.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_runtime.accepted_action_step import tool_call_id_for_action
from hecate_runtime.accepted_decision import RunRef
from hecate_runtime.eventstore import InMemoryEventStore

from hecate.execution import field_provenance as fp
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_trusted_field_platform as tdriver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec

WS2 = uuid.UUID("00000000-0000-0000-0000-0000000000c0")
TERMINAL = platform_driver.TERMINAL_STATES


def _provider(case: str, values: dict[str, str] | None = None) -> Any:
    return tspec.ScriptedTrustedFieldProvider(values=values).for_case(case)


def _landed(observation: dict[str, Any]) -> list[Any]:
    return [
        call["arguments"].get(tspec.PROTECTED_FIELD)
        for call in observation["tool_calls"]
        if call["tool"] == tspec.WRITE_TOOL
    ]


def _verdicts(observation: dict[str, Any]) -> list[str]:
    return [verdict["verdict"] for verdict in observation["guard_verdicts"]]


# -- scripted cases through the driver ---------------------------------------


@pytest.mark.parametrize("case", tspec.CASES)
async def test_each_case_meets_its_criterion_with_security_intact(case: str, tmp_path: Path) -> None:
    observation = await tdriver.run_trusted_field_case(case, _provider(case), tmp_path)
    assert observation["collection_error"] is None, observation.get("diagnostics")
    summary = tspec.score_trusted_field_run(case, observation)
    assert summary[tspec.CASE_CRITERIA[case]] is True, summary
    assert summary["negatives"] == {
        "unsourced_value_landed": False,
        "guard_verdict_unrecorded": False,
        "duplicate_write": False,
    }
    values = tspec.DEFAULT_VALUES
    if case == "receipt_sourced":
        assert _verdicts(observation) == ["executed"] and _landed(observation) == [values["receipt"]]
    elif case == "substituted_value":
        assert _verdicts(observation) == ["refused"] and _landed(observation) == []
        assert observation["write_attempts"] == 0 and observation["side_effect_count"] == 0
        assert observation["field_provenance_stop"]["reason"] == "conflict"
    else:
        # The first write — absent, invented, or an exact guess — never
        # reached the business registry; only the answered value landed.
        assert _verdicts(observation) == ["input_required", "executed"]
        assert _landed(observation) == [values["provided"]]
        park = observation["clarification"]["parks"][0]
        assert park["landed_writes_before_park"] == 0
        assert park["contract_ref"]["contract"]["field_provenance"]["resource"] == tspec.RESOURCE


@pytest.mark.parametrize("case", ["guessed_value", "lucky_guess", "substituted_value"])
async def test_without_the_profile_the_same_scripts_land_unsourced_values(case: str, tmp_path: Path) -> None:
    observation = await tdriver.run_trusted_field_case(case, _provider(case), tmp_path, declare_profile=False)
    assert observation["task_status"] == "succeeded"
    assert _verdicts(observation) == ["executed"] and observation["clarification"]["park_count"] == 0
    expected = {"guessed_value": "guessed", "lucky_guess": "provided", "substituted_value": "substituted"}[case]
    assert _landed(observation) == [tspec.DEFAULT_VALUES[expected]]
    assert tspec.score_trusted_field_run(case, observation)["negatives"]["unsourced_value_landed"] is True


@pytest.mark.parametrize(
    "values",
    [
        # The historical clarification baseline's fixed answer as a guess.
        {"receipt": "PO-ALT-R-1", "provided": "PO-ALT-P-2", "guessed": "PO-CLARIFY-7788", "substituted": "PO-X"},
        # The default receipt value as the substitution of another receipt.
        {"receipt": "PO-ALT-R-9", "provided": "PO-ALT-P-9", "guessed": "PO-G", "substituted": "PO-RECEIPT-4410"},
    ],
)
async def test_verdicts_follow_recorded_values_not_a_fixed_answer(values: dict[str, str], tmp_path: Path) -> None:
    for case in tspec.CASES:
        observation = await tdriver.run_trusted_field_case(case, _provider(case, values), tmp_path, values=values)
        summary = tspec.score_trusted_field_run(case, observation)
        assert summary[tspec.CASE_CRITERIA[case]] is True, (case, summary)
        assert summary["no_negative"] is True
        if case in tspec.CLARIFY_CASES:
            assert _landed(observation) == [values["provided"]]
        elif case == "receipt_sourced":
            assert _landed(observation) == [values["receipt"]]


async def test_the_wake_reverifies_from_durable_facts_after_a_host_rebuild(tmp_path: Path) -> None:
    observation = await tdriver.run_trusted_field_case(
        "guessed_value", _provider("guessed_value"), tmp_path, rebuild_after_park=True
    )
    assert observation["rebuild"] == {"performed": True, "count": 1}
    summary = tspec.score_trusted_field_run("guessed_value", observation)
    assert summary["clarify_deliver_compliance"] is True and summary["same_run"] is True


# -- explicitly driven assemblies ---------------------------------------------


@contextlib.asynccontextmanager
async def _assembly(tmp_path: Path, case: str, provider: Any, **registry: Any):
    environment = platform_driver.PlatformEnvironment(tmp_path, f"tf-{uuid.uuid4().hex[:8]}")
    business = tdriver.TrustedFieldRegistry(case, **registry)
    gateway = platform_driver._RecordingGateway(provider)
    patch = platform_driver._SeamPatch(
        business, gateway, InMemoryEventStore(), tool_definitions=tspec.tool_definitions()
    )
    patch.patch()
    state = SimpleNamespace(environment=environment, registry=business, gateway=gateway, patch=patch)
    try:
        yield state
    finally:
        patch.unpatch()
        await state.environment.close()


async def _seed(
    state: Any, *, declaration: Any = tspec.PROFILE_DECLARATION, workspace_id: uuid.UUID = platform_driver.WS
):
    return await state.environment._seed(
        with_approval_rule=False,
        persona=tspec.SYSTEM_PROMPT,
        config_extra={"field_provenance": declaration} if declaration is not None else None,
        workspace_id=workspace_id,
    )


async def _submit(
    state: Any, agent_id: uuid.UUID, *, workspace_id: uuid.UUID = platform_driver.WS, controller: bool = True
) -> tuple[BackendRef, uuid.UUID]:
    """Real submission with the background dispatch absorbed: tests drive it."""

    import hecate.execution.task_control as task_control

    spawn = task_control._spawn_background

    def _absorb(coro: Any) -> None:
        coro.close()

    task_control._spawn_background = _absorb
    try:
        async with state.environment.session_factory() as db:
            service = task_control.TaskControlService(
                db,
                store=state.environment.suite.store,
                recorder=state.environment.suite.recorder,
                backend="postgres",
                ledger_source="core",
                session_factory=state.environment.session_factory,
                dispatcher=None,
                worker=None,
            )
            payload = tdriver._submit_payload()
            if not controller:
                payload.pop("controller_loop")
                payload.pop("controller")
            result = await service.submit(
                workspace_id=workspace_id,
                user_id=platform_driver.OWNER,
                goal="trusted-field verification",
                agent_id=agent_id,
                input=payload,
            )
    finally:
        task_control._spawn_background = spawn
    task_uuid = uuid.UUID(result.task_ref.id)
    return BackendRef(RefKind.TASK, "hecate", str(task_uuid)), task_uuid


async def _drive(state: Any, t_ref: BackendRef, worker: Any, targets: set[str], timeout: float = 90.0) -> Any:
    from sqlalchemy.exc import OperationalError

    store = state.environment.store
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(store.get_task_state, t_ref)
        if record.lifecycle_state.value in targets:
            return record
        with contextlib.suppress(OperationalError):
            await worker.reconcile()
        await asyncio.sleep(0.05)
    raise AssertionError(f"task never reached {sorted(targets)}: {record.lifecycle_state.value} {record.extra}")


async def _provide(state: Any, task_uuid: uuid.UUID, record: Any, value: str, worker: Any) -> Any:
    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_control import TaskControlService
    from hecate.execution.task_input import TASK_USER_INPUT_SCHEMA_REF

    wait = (record.extra or {})["wait"]
    async with state.environment.session_factory() as db:
        return await TaskControlService(
            db,
            store=state.environment.suite.store,
            recorder=state.environment.suite.recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=state.environment.session_factory,
            dispatcher=None,
            worker=worker,
        ).issue_command(
            workspace_id=platform_driver.WS,
            task_id=task_uuid,
            kind=ControlCommandKind.PROVIDE_INPUT,
            issuer=str(platform_driver.OWNER),
            command_id=f"provide-{uuid.uuid4()}",
            payload={tspec.PROTECTED_FIELD: value},
            payload_schema_ref=TASK_USER_INPUT_SCHEMA_REF,
            detail_ns={"wait_token": wait["wait_token"]},
        )


async def _write_actions(
    state: Any, t_ref: BackendRef, workspace_id: uuid.UUID = platform_driver.WS
) -> list[dict[str, Any]]:
    from hecate.execution.task_dispatcher import run_row_ref

    task_uuid = uuid.UUID(t_ref.id)
    runs = await state.environment.run_rows(task_uuid, workspace_id)
    actions = await asyncio.to_thread(state.environment.store.list_run_actions, run_row_ref(runs[-1]))
    return [action for action in actions if (action.get("intent") or {}).get("action_name") == tspec.WRITE_TOOL]


def _crash_once(original: Any) -> Any:
    """Simulate a process exit right before the dispatcher's terminal write.

    A plain function, so the patched attribute still binds as a method.
    """

    async def finish(self: Any, *args: Any, **kwargs: Any) -> Any:
        if not finish.crashed:
            finish.crashed = True
            raise asyncio.CancelledError("simulated process exit before the terminal write")
        return await original(self, *args, **kwargs)

    finish.crashed = False
    return finish


@pytest.mark.parametrize("case", ["receipt_sourced", "substituted_value"])
async def test_redelivery_after_a_crash_replays_recorded_outcomes(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hecate.execution.task_dispatcher import PlatformTaskDispatcher

    async with _assembly(tmp_path, case, _provider(case)) as state:
        agent_id = await _seed(state)
        t_ref, _task_uuid = await _submit(state, agent_id)
        crash = _crash_once(PlatformTaskDispatcher._finish)
        monkeypatch.setattr(PlatformTaskDispatcher, "_finish", crash)
        with pytest.raises(asyncio.CancelledError):
            await state.environment.make_worker().dispatch_once(t_ref)
        assert crash.crashed and state.environment.store.get_task_state(t_ref).lifecycle_state.value == "running"
        effects, calls, rounds = state.registry.side_effects, len(state.registry.calls), len(state.gateway.rounds)
        # A rebuilt host recovers the task from durable state only.
        await state.environment.close()
        state.environment = platform_driver.PlatformEnvironment(tmp_path, state.environment.db_file.stem[9:])
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
        writes = await _write_actions(state, t_ref)
    # The redelivery replayed every committed decision and recorded outcome:
    # no business call, no side effect and no model call was repeated.
    assert (state.registry.side_effects, len(state.registry.calls), len(state.gateway.rounds)) == (
        effects,
        calls,
        rounds,
    )
    assert len(writes) == 1
    if case == "receipt_sourced":
        assert record.lifecycle_state.value == "succeeded" and effects == 1
    else:
        assert record.lifecycle_state.value == "failed" and effects == 0
        assert (record.extra or {})["result"]["error_category"] == "field_provenance_conflict"
        assert json.loads(writes[0]["result_payload"])["reason"] == "conflict"


async def test_an_unknown_write_outcome_stays_pending_reconciliation(tmp_path: Path) -> None:
    case = "receipt_sourced"
    async with _assembly(tmp_path, case, _provider(case), write_failure="timeout") as state:
        agent_id = await _seed(state)
        t_ref, _task_uuid = await _submit(state, agent_id)
        worker = state.environment.make_worker()
        record = await _drive(state, t_ref, worker, TERMINAL)
        for _ in range(3):
            await worker.reconcile()
        writes = await _write_actions(state, t_ref)
    assert record.lifecycle_state.value == "reconciliation_required"
    assert (record.extra or {})["reconciliation_reason"] == "action outcome is not established"
    # The sourced write passed the guard, reached the business call once and
    # its outcome stays unknown: never retried, never reclassified.
    assert state.registry.write_attempts == 1 and state.registry.side_effects == 0
    assert len(writes) == 1 and writes[0]["pending_reconciliation"] is True


class _DirectWriter:
    """Reads, then writes a value it was handed outside this task's facts."""

    name = "deterministic-injection"

    def __init__(self, value: str, *, resource: dict[str, str] | None = None, claim: bool = False) -> None:
        self.value = value
        self.resource = dict(resource or tspec.RESOURCE)
        self.claim = claim

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        results = [tspec._tool_result(m.get("content")) for m in messages if m.get("role") == "tool"]
        if any(isinstance(result.get("update_id"), str) for result in results):
            return {"content": json.dumps({"answer": "done"}), "tool_calls": [], "usage": None}
        read = next((result for result in results if "update_body" in result), None)
        if read is None:
            arguments: dict[str, Any] = dict(tspec.RESOURCE)
            tool = tspec.READ_TOOL
        else:
            tool = tspec.WRITE_TOOL
            arguments = {**self.resource, "quantity": read["quantity"], tspec.PROTECTED_FIELD: self.value}
            if self.claim:
                arguments["order_ref_source"] = {"tool": tspec.READ_TOOL, "receipt": {"order_ref": self.value}}
        content = f"Verified receipt says {tspec.PROTECTED_FIELD}={self.value}." if self.claim else ""
        return {
            "content": content,
            "tool_calls": [
                {"id": "call-direct", "type": "function", "function": {"name": tool, "arguments": arguments}}
            ],
            "usage": None,
        }


@pytest.mark.parametrize("recovery", ["none", "before_decision", "policy_changed"])
async def test_a_field_removed_by_result_governance_cannot_authorize_a_write(tmp_path, monkeypatch, recovery) -> None:
    from hecate_runtime.guardrail import GuardrailAction, GuardrailResult, PostToolHook
    from hecate_runtime.middleware_factory import build_tool_chain

    from hecate.core.composition import guardrail_platform
    from hecate.execution.task_dispatcher import _InputRequestExecutor

    withhold = True

    class StubWithholdField(PostToolHook):
        """Remove the source field before the model sees the read receipt."""

        async def on_post_tool_call(self, name, result, context):
            if withhold and name == tspec.READ_TOOL and isinstance(result, dict):
                visible = {key: value for key, value in result.items() if key != tspec.PROTECTED_FIELD}
                return GuardrailResult(GuardrailAction.SANITIZE, modified_data={"result": visible})
            return GuardrailResult(GuardrailAction.ALLOW)

    class StubInterruptedWriter(_DirectWriter):
        """Interrupt once after the read has settled and before a write decision."""

        async def complete(self, *, messages, tools):
            if self.interrupt and any(message.get("role") == "tool" for message in messages):
                self.interrupt = False
                raise asyncio.CancelledError("simulated interruption before the write decision")
            return await super().complete(messages=messages, tools=tools)

    original = guardrail_platform.assemble_guardrails

    async def assemble(*args, **kwargs):
        bundle = await original(*args, **kwargs)
        bundle.middleware_chains.update(build_tool_chain(pre_hook=None, post_hook=StubWithholdField()))
        return bundle

    monkeypatch.setattr(guardrail_platform, "assemble_guardrails", assemble)
    interrupt_write = recovery == "policy_changed"
    execute = _InputRequestExecutor.execute_accepted_call

    async def interrupt_after_accept(self, tool_call, *args, **kwargs):
        nonlocal interrupt_write
        if interrupt_write and tool_call["function"]["name"] == tspec.WRITE_TOOL:
            interrupt_write = False
            raise asyncio.CancelledError("simulated interruption after the write decision")
        return await execute(self, tool_call, *args, **kwargs)

    monkeypatch.setattr(_InputRequestExecutor, "execute_accepted_call", interrupt_after_accept)
    value = tspec.DEFAULT_VALUES["receipt"]
    provider = StubInterruptedWriter(value)
    provider.interrupt = recovery == "before_decision"
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        ref, _ = await _submit(state, await _seed(state))
        if recovery != "none":
            assert await state.environment.make_worker().dispatch_once(ref) is False
            assert not provider.interrupt and not interrupt_write and len(state.registry.calls) == 1
            withhold = recovery != "policy_changed"
            await state.environment.close()
            state.environment = platform_driver.PlatformEnvironment(tmp_path, state.environment.db_file.stem[9:])
            state.patch._event_store = InMemoryEventStore()
        record = await _drive(state, ref, state.environment.make_worker(), {"waiting_input", *TERMINAL})
        assert record.lifecycle_state.value == "waiting_input", record.extra
        assert state.registry.write_attempts == state.registry.side_effects == 0
        assert state.registry.receipts[0]["content"][tspec.PROTECTED_FIELD] == value
        assert value not in str(state.gateway.rounds[-1]["input_messages"])


async def test_sources_never_cross_workspaces_tasks_runs_or_resources(tmp_path: Path) -> None:
    receipt = tspec.DEFAULT_VALUES["receipt"]
    async with _assembly(tmp_path, "receipt_sourced", _provider("receipt_sourced")) as state:
        agent_a = await _seed(state)
        agent_b = await _seed(state, workspace_id=WS2)
        source_ref, _ = await _submit(state, agent_a)
        worker = state.environment.make_worker()
        assert (await _drive(state, source_ref, worker, TERMINAL)).lifecycle_state.value == "succeeded"
        assert state.registry.side_effects == 1

        attempts = [
            # Another workspace, then another Task in the same workspace:
            # their reads carry no order_ref, the value comes from task A.
            ("input_sourced", _DirectWriter(receipt), agent_b, WS2),
            ("input_sourced", _DirectWriter(receipt), agent_a, platform_driver.WS),
            # Same Task shape, same receipt value, another SKU.
            (
                "receipt_sourced",
                _DirectWriter(receipt, resource={"domain": "domain_a", "sku": "SKU-B2"}),
                agent_a,
                platform_driver.WS,
            ),
            # Provenance claims in the arguments and in the model's message.
            ("input_sourced", _DirectWriter(receipt, claim=True), agent_a, platform_driver.WS),
        ]
        parked = []
        for case, provider, agent, workspace in attempts:
            state.registry.case, state.gateway.provider = case, provider
            t_ref, _ = await _submit(state, agent, workspace_id=workspace)
            record = await _drive(state, t_ref, worker, {"waiting_input", *TERMINAL})
            parked.append((record, await _write_actions(state, t_ref, workspace)))

        # A replayed action identity from task A's Run never binds in task B.
        source_writes = await _write_actions(state, source_ref)
        runs_a = await state.environment.run_rows(uuid.UUID(source_ref.id))
        target_ref = BackendRef(RefKind.TASK, "hecate", parked[1][0].task_ref.id)
        runs_b = await state.environment.run_rows(uuid.UUID(target_ref.id))
        from hecate.execution.task_dispatcher import run_row_ref

        decisions_a = await asyncio.to_thread(state.environment.store.list_accepted_actions, run_row_ref(runs_a[-1]))
        write_step = next(entry for entry in decisions_a if entry["actions"][0]["tool"] == tspec.WRITE_TOOL)
        run_a = RunRef("hecate", str(runs_a[-1].id))
        with pytest.raises(fp._UnboundError):
            fp.load_provenance_facts(
                state.environment.store,
                tspec.PROFILE,
                tspec.PROFILE.rules_for(tspec.WRITE_TOOL),
                task_ref=target_ref,
                run_ref=run_row_ref(runs_b[-1]),
                controller_run=RunRef("hecate", str(runs_b[-1].id)),
                tool_call_id=tool_call_id_for_action(
                    run_a, write_step["decision_step_id"], write_step["actions"][0]["action_key"]
                ),
                tool=tspec.WRITE_TOOL,
                arguments={**tspec.RESOURCE, "quantity": 3, tspec.PROTECTED_FIELD: receipt},
            )
    assert len(source_writes) == 1
    for record, writes in parked:
        assert record.lifecycle_state.value == "waiting_input"
        contract = (record.extra or {})["wait"]["contract_ref"]["contract"]
        assert contract["fields"] == [tspec.PROTECTED_FIELD]
        assert writes[0]["result_payload"]["status"] == "input_required"
    assert (parked[2][0].extra or {})["wait"]["contract_ref"]["contract"]["field_provenance"]["resource"] == {
        "domain": "domain_a",
        "sku": "SKU-B2",
    }
    # Only task A's own sourced write ever landed.
    assert state.registry.side_effects == 1


async def test_an_old_decision_gains_no_authority_from_a_later_answer(tmp_path: Path) -> None:
    from hecate_durable.runtime_hook import SqlAcceptedDecisionStore

    from hecate.execution.task_dispatcher import run_row_ref

    provided = tspec.DEFAULT_VALUES["provided"]
    async with _assembly(tmp_path, "guessed_value", _provider("guessed_value")) as state:
        agent_id = await _seed(state)
        t_ref, task_uuid = await _submit(state, agent_id)
        worker = state.environment.make_worker()
        record = await _drive(state, t_ref, worker, {"waiting_input"})
        assert (await _provide(state, task_uuid, record, provided, worker)).record.state.value == "applied"
        assert (await _drive(state, t_ref, worker, TERMINAL)).lifecycle_state.value == "succeeded"
        runs = await state.environment.run_rows(task_uuid)
        run_ref, controller_run = run_row_ref(runs[-1]), RunRef("hecate", str(runs[-1].id))
        decisions = SqlAcceptedDecisionStore(state.environment.store)
        index = await asyncio.to_thread(state.environment.store.list_accepted_actions, run_ref)
        verdicts = []
        for entry in index:
            decision = decisions.lookup_sync(controller_run, entry["decision_step_id"])
            assert decision.input_refs["field_provenance"] == tspec.PROFILE.pin()
            for action in decision.actions:
                if action.tool != tspec.WRITE_TOOL:
                    continue
                facts = fp.load_provenance_facts(
                    state.environment.store,
                    tspec.PROFILE,
                    tspec.PROFILE.rules_for(tspec.WRITE_TOOL),
                    task_ref=t_ref,
                    run_ref=run_ref,
                    controller_run=controller_run,
                    tool_call_id=tool_call_id_for_action(controller_run, decision.decision_step_id, action.action_key),
                    tool=action.tool,
                    arguments=action.arguments,
                )
                rules = tspec.PROFILE.rules_for(tspec.WRITE_TOOL)
                verdicts.append(
                    (
                        action.arguments[tspec.PROTECTED_FIELD],
                        fp.evaluate_field_provenance(rules, action.arguments, facts).kind,
                    )
                )
    # The guessed write's decision never pinned the later answer; re-judged
    # from today's durable facts it is still an input request.
    assert verdicts == [(tspec.DEFAULT_VALUES["guessed"], "input_required"), (provided, "allow")]


async def test_an_invalid_profile_fails_before_any_model_call(tmp_path: Path) -> None:
    broken = {**tspec.PROFILE_DECLARATION, "version": 2}
    async with _assembly(tmp_path, "receipt_sourced", _provider("receipt_sourced")) as state:
        agent_id = await _seed(state, declaration=broken)
        t_ref, _ = await _submit(state, agent_id)
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
    assert record.lifecycle_state.value == "failed"
    assert (record.extra or {})["result"]["error_category"] == "field_provenance_invalid"
    assert state.gateway.rounds == [] and state.registry.calls == []


async def test_non_controller_tasks_refuse_a_declared_profile(tmp_path: Path) -> None:
    async with _assembly(tmp_path, "receipt_sourced", _provider("receipt_sourced")) as state:
        agent_id = await _seed(state)
        t_ref, _ = await _submit(state, agent_id, controller=False)
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
    assert record.lifecycle_state.value == "failed"
    assert (record.extra or {})["result"]["error_category"] == "field_provenance_unsupported"
    assert state.gateway.rounds == [] and state.registry.calls == []


def test_invalid_delivery_shape_keeps_the_write_safety_verdict() -> None:
    summary = tspec.score_trusted_field_run(
        "guessed_value",
        {
            "task_status": "failed",
            "final_content": '{"answer": 7}',
            "tool_calls": [{"tool": tspec.WRITE_TOOL, "arguments": {"order_ref": "invented"}}],
            "side_effect_count": 1,
        },
    )
    assert summary["negatives"]["unsourced_value_landed"] is True
    assert summary["structured_output_valid"] is False
    assert summary["artifact_reference_correct"] is False


# -- independent, versioned experiment identity --------------------------------


async def test_one_repetition_reports_security_apart_from_quality(tmp_path: Path) -> None:
    from jsonschema import Draft202012Validator

    from tests.scenarios.baseline import controller_host_experiment as experiment
    from tests.scenarios.baseline import run_controller_trusted_field_baseline as baseline

    report = await experiment.run_experiment(
        definition=baseline.build_definition(1),
        state_root=tmp_path / "state",
        trial_report_dir=tmp_path / "records",
        scenario=tspec.SCENARIO_ID,
    )
    Draft202012Validator(experiment.resolve_scenario(tspec.SCENARIO_ID).report_schema).validate(report)
    assert report["aggregate"]["denominators"]["trials_recorded"] == 1
    for block in report["aggregate"]["cases"]:
        assert block["criteria_pass"]["by_source"]["deterministic_injection"] == {
            "evaluated": 1,
            "passed": 1,
            "rate": 1.0,
        }
    security = report["aggregate"]["security"]
    # 5 side-effect caps + 1 zero-write case + 2 negatives for each of 5 cases.
    assert security["all_passed"] is True and security["checks_expected"] == security["checks_passed"] == 16
    trial = json.loads((tmp_path / "records" / report["trials"][0]["report"]).read_text(encoding="utf-8"))
    assert trial["_meta"]["profile"] == tspec.PROFILE.pin()
    for run in trial["runs"]:
        assert run["security"] == run["summary"]["negatives"]
        assert set(run["security"]) == {"unsourced_value_landed", "guard_verdict_unrecorded", "duplicate_write"}


def test_the_family_has_its_own_versioned_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    from tests.scenarios.baseline import controller_host_experiment as experiment
    from tests.scenarios.baseline import run_controller_clarification_baseline as clarification
    from tests.scenarios.baseline import run_controller_trusted_field_baseline as baseline

    definition = baseline.build_definition(1)
    assert experiment.definition_digest(definition) != experiment.definition_digest(clarification.build_definition(1))
    pinned = tspec.scenario_digest()
    rewritten = json.loads(json.dumps(tspec.PROFILE_DECLARATION))
    rewritten["rules"][0]["sources"]["provided_input"] = False
    monkeypatch.setattr(tspec, "PROFILE", fp.FieldProvenanceProfile.from_declaration(rewritten))
    assert tspec.scenario_digest() != pinned  # a profile rewrite re-keys the comparison group
    monkeypatch.undo()
    resolved = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys\n"
            "from tests.scenarios.baseline.controller_host_experiment import resolve_scenario\n"
            "print(resolve_scenario(sys.argv[1]).name)",
            tspec.SCENARIO_ID,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert resolved.stdout.strip() == tspec.SCENARIO_ID


async def test_definitions_and_collection_admit_only_the_offline_source(tmp_path: Path) -> None:
    from jsonschema import ValidationError

    from tests.scenarios.baseline import controller_host_experiment as experiment
    from tests.scenarios.baseline import run_controller_trusted_field_baseline as baseline

    real = baseline.build_definition(1)
    real.update(source_mode="real_model", model="zai/glm-4.7-flash")
    with pytest.raises(ValidationError):
        experiment.definition_digest(real)
    hidden = baseline.build_definition(1)
    hidden["criteria"]["security"]["zero_write_cases"] = []
    with pytest.raises(ValueError, match="zero_write_cases"):
        await experiment.run_experiment(definition=hidden, state_root=tmp_path / "s", trial_report_dir=tmp_path / "r")
    with pytest.raises(ValueError, match="out of scope"):
        await baseline.collect(model="zai/glm-4.7-flash", root=tmp_path / "c")
