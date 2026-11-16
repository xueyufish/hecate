"""Write-once duplicate admission end to end on the real platform Controller.

The write-once variant of the trusted-field profile (``rules[].write_once``)
admits at most one business write per workspace, Task, Run, tool and
resource. Every case here runs the same production assembly as the
trusted-field family — the real runtime port, governance facade, durable
action ledger, accepted-decision chain and input revision store, with a
countable business registry that has NO provenance or dedup logic of its
own. Coverage: a trusted read and an authorization supplement each complete
the Run's FIRST write; a second action of the same decision, and a later
decision's resubmission with a changed quantity but a still-sourced value,
are refused as ``duplicate`` before the business call (one write attempt,
one side effect, the first receipt kept, the refusal recorded as the second
action's ledger outcome); different resources each keep their write; a
crash between the two write actions and the redelivery that follows never
double-writes (the completed receipt replays, the sibling is refused); the
same scripts without the profile land both writes; and the historical
negatives (guessed, substituted) keep their verdicts under the opt-in. The
duplicate path is also served through the real REST routes, checking the
Task terminal state, the action receipts and the result inbox.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from hecate_runtime.eventstore import InMemoryEventStore

from hecate.execution.field_provenance import FIELD_PROVENANCE_REFUSED
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_trusted_field_platform as tdriver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec
from tests.scenarios.test_controller_trusted_field import _assembly, _drive, _provider, _seed, _submit, _write_actions

TERMINAL = platform_driver.TERMINAL_STATES
RESOURCE = dict(tspec.RESOURCE)
OTHER_RESOURCE = {"domain": "domain_a", "sku": "SKU-B2"}
RECEIPT = tspec.DEFAULT_VALUES["receipt"]

WRITE_ONCE_DECLARATION: dict[str, Any] = {
    **json.loads(json.dumps(tspec.PROFILE_DECLARATION)),
    "rules": [{**tspec.PROFILE_DECLARATION["rules"][0], "write_once": True}],
}


class _ScriptedRounds:
    """Deterministic provider playing pre-registered tool-call rounds.

    Each round is a list of (tool, arguments) tool calls; an empty round
    ends the task with the final answer. After the rounds run out the
    provider answers — the task must already be terminal by then.
    """

    name = "deterministic-injection"

    def __init__(self, rounds: list[list[tuple[str, dict[str, Any]]]]) -> None:
        self.rounds = rounds
        self.seen = 0

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        if self.seen >= len(self.rounds):
            return {
                "content": json.dumps({"answer": "done"}),
                "tool_calls": [],
                "usage": None,
                "provider_model": self.name,
            }
        round = self.rounds[self.seen]
        self.seen += 1
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": f"call-round-{self.seen}-{index}",
                    "type": "function",
                    "function": {"name": tool, "arguments": args},
                }
                for index, (tool, args) in enumerate(round)
            ],
            "usage": None,
            "provider_model": self.name,
        }


def _write_arguments(resource: dict[str, str], quantity: int) -> dict[str, Any]:
    return {**resource, "quantity": quantity, tspec.PROTECTED_FIELD: RECEIPT}


def _duplicate_verdicts(observation: dict[str, Any]) -> list[str]:
    """The ledger's per-action verdicts: executed writes and refusals."""

    verdicts = []
    for verdict in observation["guard_verdicts"]:
        if verdict["verdict"] in ("executed", "input_required"):
            verdicts.append(verdict["verdict"])
        elif verdict["verdict"] == "refused":
            verdicts.append(f"refused:{verdict.get('reason')}")
        else:
            verdicts.append(verdict["verdict"])
    return verdicts


# -- the first write still completes -----------------------------------------


@pytest.mark.parametrize("case", ["receipt_sourced", "input_sourced"])
async def test_the_first_write_completes_under_write_once(case: str, tmp_path) -> None:
    observation = await tdriver.run_trusted_field_case(
        case, _provider(case), tmp_path, declaration=WRITE_ONCE_DECLARATION
    )
    assert observation["collection_error"] is None, observation.get("diagnostics")
    # The first write is admitted exactly as without the opt-in: one
    # executed write, no duplicate refusal, task succeeded.
    assert observation["task_status"] == "succeeded"
    # receipt_sourced writes directly; input_sourced parks for input first,
    # and the park consumes no write.
    assert _duplicate_verdicts(observation) == (
        ["executed"] if case == "receipt_sourced" else ["input_required", "executed"]
    )
    assert observation["side_effect_count"] == 1 and observation["write_attempts"] == 1
    landed = [
        call["arguments"][tspec.PROTECTED_FIELD]
        for call in observation["tool_calls"]
        if call["tool"] == tspec.WRITE_TOOL
    ]
    expected = tspec.DEFAULT_VALUES["receipt" if case == "receipt_sourced" else "provided"]
    assert landed == [expected]


# -- the same decision cannot write one resource twice ------------------------


@pytest.mark.parametrize("mimic_input_request", [False, True])
async def test_the_same_decision_cannot_write_a_resource_twice(tmp_path, monkeypatch, mimic_input_request) -> None:
    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            # Two write actions of ONE decision, same resource, same sourced
            # value, different quantities.
            [
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3)),
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 9)),
            ],
        ]
    )
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        if mimic_input_request:
            execute = state.registry.execute

            async def write_then_report_input_required(name, arguments, context=None):
                result = await execute(name, arguments, context)
                if name == tspec.WRITE_TOOL:
                    # This result was returned after the business effect.
                    return {"status": "input_required"}
                return result

            monkeypatch.setattr(state.registry, "execute", write_then_report_input_required)
        agent_id = await _seed(state, declaration=WRITE_ONCE_DECLARATION)
        t_ref, _task = await _submit(state, agent_id)
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
        writes = await _write_actions(state, t_ref)
    assert record.lifecycle_state.value == "failed"
    assert (record.extra or {})["result"]["error_category"] == "field_provenance_duplicate"
    # The first write landed; the second was refused before the business
    # registry — exactly one call, one side effect, one landed receipt.
    assert state.registry.write_attempts == 1 and state.registry.side_effects == 1
    assert len(state.registry.receipts) == 2  # the read + the one write
    assert _duplicate_verdicts({"guard_verdicts": tdriver.guard_verdicts(writes)}) == ["executed", "refused:duplicate"]
    assert json.loads(writes[1]["result_payload"])["status"] == FIELD_PROVENANCE_REFUSED
    assert json.loads(writes[1]["result_payload"])["reason"] == "duplicate"
    # The first receipt stays a real, settled business result; the refusal
    # is the second action's recorded outcome.
    assert writes[0]["last_outcome"]["outcome"] == "succeeded"
    assert writes[1]["pending_reconciliation"] is False


async def test_a_later_decision_cannot_resubmit_with_a_changed_quantity(tmp_path) -> None:
    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            [(tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3))],
            # The write receipt is visible; the model resubmits with a
            # modified quantity and the same sourced value.
            [(tspec.WRITE_TOOL, _write_arguments(RESOURCE, 9))],
        ]
    )
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        agent_id = await _seed(state, declaration=WRITE_ONCE_DECLARATION)
        t_ref, _task = await _submit(state, agent_id)
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
        writes = await _write_actions(state, t_ref)
    assert record.lifecycle_state.value == "failed"
    assert (record.extra or {})["result"]["error_category"] == "field_provenance_duplicate"
    assert state.registry.write_attempts == 1 and state.registry.side_effects == 1
    assert _duplicate_verdicts({"guard_verdicts": tdriver.guard_verdicts(writes)}) == ["executed", "refused:duplicate"]
    assert json.loads(writes[1]["result_payload"])["reason"] == "duplicate"


# -- different resources each keep their write --------------------------------


async def test_different_resources_each_receive_their_write(tmp_path) -> None:
    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            [(tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3))],
            [(tspec.READ_TOOL, dict(OTHER_RESOURCE))],
            [(tspec.WRITE_TOOL, _write_arguments(OTHER_RESOURCE, 1))],
        ]
    )
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        agent_id = await _seed(state, declaration=WRITE_ONCE_DECLARATION)
        t_ref, _task = await _submit(state, agent_id)
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
        writes = await _write_actions(state, t_ref)
    assert record.lifecycle_state.value == "succeeded"
    assert state.registry.side_effects == 2 and state.registry.write_attempts == 2
    landed_resources = [
        {key: call["arguments"][key] for key in RESOURCE}
        for call in state.registry.calls
        if call["tool"] == tspec.WRITE_TOOL
    ]
    assert landed_resources == [RESOURCE, OTHER_RESOURCE]
    assert [action["last_outcome"]["outcome"] for action in writes] == ["succeeded", "succeeded"]


# -- crash between the two writes: redelivery never double-writes --------------


async def test_redelivery_after_a_crash_between_write_actions_never_double_writes(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hecate.execution.task_dispatcher import _InputRequestExecutor

    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            [
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3)),
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 9)),
            ],
        ]
    )
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        agent_id = await _seed(state, declaration=WRITE_ONCE_DECLARATION)
        t_ref, _task = await _submit(state, agent_id)
        execute = _InputRequestExecutor.execute_accepted_call
        crashed = False

        async def crash_on_the_second_write(self, tool_call, *args, **kwargs):
            # Fire once: on redelivery the replay of the completed write
            # passes through this seam too and must reach its receipt.
            nonlocal crashed
            if not crashed and tool_call["function"]["name"] == tspec.WRITE_TOOL and state.registry.write_attempts >= 1:
                crashed = True
                raise asyncio.CancelledError("simulated exit after the first write, before the second")
            return await execute(self, tool_call, *args, **kwargs)

        monkeypatch.setattr(_InputRequestExecutor, "execute_accepted_call", crash_on_the_second_write)
        assert await state.environment.make_worker().dispatch_once(t_ref) is False
        assert state.registry.write_attempts == 1 and state.registry.side_effects == 1
        effects, calls = state.registry.side_effects, len(state.registry.calls)
        # A rebuilt host recovers from durable state only.
        await state.environment.close()
        state.environment = platform_driver.PlatformEnvironment(tmp_path, state.environment.db_file.stem[9:])
        state.patch._event_store = InMemoryEventStore()
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
        writes = await _write_actions(state, t_ref)
    # The replay reused the completed receipt (no business call) and the
    # sibling action was refused as a duplicate: one write ever.
    assert (state.registry.side_effects, len(state.registry.calls)) == (effects, calls)
    assert state.registry.write_attempts == 1
    assert record.lifecycle_state.value == "failed"
    assert (record.extra or {})["result"]["error_category"] == "field_provenance_duplicate"
    assert len(writes) == 2
    assert json.loads(writes[1]["result_payload"])["reason"] == "duplicate"


async def test_a_lost_write_receipt_stops_the_sibling_for_reconciliation(tmp_path, monkeypatch) -> None:
    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            [
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3)),
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 9)),
            ],
        ]
    )
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        execute = state.registry.execute

        async def lose_the_receipt(name, arguments, context=None):
            result = await execute(name, arguments, context)
            if name == tspec.WRITE_TOOL:
                raise TimeoutError("business effect completed before the receipt was lost")
            return result

        monkeypatch.setattr(state.registry, "execute", lose_the_receipt)
        agent_id = await _seed(state, declaration=WRITE_ONCE_DECLARATION)
        t_ref, _task = await _submit(state, agent_id)
        worker = state.environment.make_worker()
        record = await _drive(state, t_ref, worker, TERMINAL)
        await worker.reconcile()
        writes = await _write_actions(state, t_ref)

    assert record.lifecycle_state.value == "reconciliation_required"
    assert state.registry.write_attempts == state.registry.side_effects == 1
    assert len(writes) == 1 and writes[0]["pending_reconciliation"] is True


# -- the negatives: without the profile both writes land -----------------------


async def test_without_the_profile_the_same_script_lands_both_writes(tmp_path) -> None:
    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            [
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3)),
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 9)),
            ],
        ]
    )
    async with _assembly(tmp_path, "receipt_sourced", provider) as state:
        agent_id = await _seed(state, declaration=None)
        t_ref, _task = await _submit(state, agent_id)
        record = await _drive(state, t_ref, state.environment.make_worker(), TERMINAL)
    assert record.lifecycle_state.value == "succeeded"
    assert state.registry.write_attempts == 2 and state.registry.side_effects == 2


@pytest.mark.parametrize("case", ["guessed_value", "substituted_value"])
async def test_the_historical_negatives_keep_their_verdicts_under_write_once(case: str, tmp_path) -> None:
    observation = await tdriver.run_trusted_field_case(
        case, _provider(case), tmp_path, declaration=WRITE_ONCE_DECLARATION
    )
    assert observation["collection_error"] is None, observation.get("diagnostics")
    if case == "guessed_value":
        # An unsourced first attempt still parks for input; the answered
        # value completes the Run's first (and only) write.
        assert observation["task_status"] == "succeeded"
        assert _duplicate_verdicts(observation) == ["input_required", "executed"]
        assert observation["side_effect_count"] == 1
    else:
        # A substitution still refuses as a conflict with zero writes; the
        # refusal consumes no write eligibility.
        assert observation["task_status"] == "failed"
        assert (observation.get("field_provenance_stop") or {}).get("reason") == "conflict"
        assert observation["write_attempts"] == 0 and observation["side_effect_count"] == 0


# -- the duplicate path over the real REST routes ------------------------------


async def test_the_duplicate_refusal_reaches_the_rest_inbox(tmp_path) -> None:
    provider = _ScriptedRounds(
        [
            [(tspec.READ_TOOL, dict(RESOURCE))],
            [
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 3)),
                (tspec.WRITE_TOOL, _write_arguments(RESOURCE, 9)),
            ],
        ]
    )
    observation = await tdriver.run_trusted_field_case(
        "receipt_sourced",
        provider,
        tmp_path,
        declaration=WRITE_ONCE_DECLARATION,
        transport="rest_tcp",
    )
    assert observation["collection_error"] is None, observation.get("diagnostics")
    assert observation["task_status"] == "failed"
    assert observation["error_category"] == "field_provenance_duplicate"
    assert observation["side_effect_count"] == 1
    assert _duplicate_verdicts(observation) == ["executed", "refused:duplicate"]
    # The failed terminal delivered its stop notice through the real inbox
    # routes, and the task view names the same terminal state.
    assert observation["rest_stop_delivery"] == "stopped_notice"
