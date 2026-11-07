"""Platform driver for the Controller clarification baseline.

Runs one paired clarification case through the real platform controller
assembly — the same ``PlatformEnvironment``, governance facade, seam
patches and durable store the dual-host matrix uses. The synthetic
registry subclasses the dual-host one: the read receipt carries the
procurement order reference only in the paired-complete case, and the
write tool declares the explicit ``input_required`` contract (with zero
side effects) whenever the field is absent. The driver parks on the real
``waiting_input`` state, issues a real ``provide_input`` command through
``TaskControlService``, and lets the same Task/Run resume.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from tests.scenarios.baseline import controller_host_clarification_spec as cspec
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_spec as spec


class ClarificationRegistry(platform_driver._SyntheticRegistry):
    """Synthetic business boundary with the paired clarification behavior."""

    async def describe(self, name: str) -> dict[str, Any]:
        return {"parameters": cspec.TOOL_SCHEMAS[name]}

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == spec.READ_TOOL:
            self.receipts.append(
                {
                    "tool": name,
                    "arguments": dict(args),
                    "status": "succeeded",
                    "content": dict(cspec.read_content(self.case)),
                }
            )
            return dict(cspec.read_content(self.case))
        if name == spec.WRITE_TOOL:
            self.write_attempts += 1
            order_ref = args.get(cspec.REQUIRED_FIELD)
            if not isinstance(order_ref, str) or not order_ref.strip():
                # Declare the request; no side effect has happened yet.
                request = cspec.input_required_result()
                self.receipts.append(
                    {"tool": name, "arguments": dict(args), "status": "input_required", "content": dict(request)}
                )
                return dict(request)
            self.side_effects += 1
            receipt = {"status": "ok", "update_id": cspec.CLARIFY_RECEIPT_ID}
            self.receipts.append(
                {"tool": name, "arguments": dict(args), "status": "succeeded", "content": dict(receipt)}
            )
            return dict(receipt)
        raise ValueError("unsupported tool")


def _submit_payload() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": cspec.USER_PROMPT}],
        "controller_loop": True,
        "controller": {"max_turns": 8},
        "output_schema": cspec.OUTPUT_SCHEMA,
    }


def _supplement_visible(gateway: Any, pre_park_rounds: int) -> bool:
    """Whether a post-wake model input carried the platform's provided-input
    marker together with the exact provided value."""

    for round_record in gateway.rounds[pre_park_rounds:]:
        supplied = cspec.provided_input(round_record.get("input_messages") or [])
        if supplied == {cspec.REQUIRED_FIELD: cspec.PROVIDED_ORDER_REF}:
            return True
    return False


async def run_clarification_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    """Run one paired clarification case through the real platform assembly."""

    from hecate_runtime.eventstore import InMemoryEventStore

    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_input import TASK_USER_INPUT_SCHEMA_REF

    environment_label = f"{case}-{uuid.uuid4().hex[:8]}"
    environment = platform_driver.PlatformEnvironment(root, environment_label)
    event_store = InMemoryEventStore()
    registry = ClarificationRegistry(case)
    gateway = platform_driver._RecordingGateway(provider)
    patch = platform_driver._SeamPatch(registry, gateway, event_store, tool_definitions=cspec.tool_definitions())
    started = time.monotonic()
    observation: dict[str, Any] = {}
    from hecate.execution.task_control import _background_tasks

    previous_tasks = set(_background_tasks)
    states = {"waiting_input", *platform_driver.TERMINAL_STATES}
    parks: list[dict[str, Any]] = []
    pre_park_rounds = 0
    try:
        async with asyncio.timeout(platform_driver.CASE_TIME_BUDGET_SECONDS):
            patch.patch()
            worker = environment.make_worker()
            agent_id = await environment._seed(with_approval_rule=False, persona=cspec.SYSTEM_PROMPT)
            async with environment.session_factory() as db:
                from hecate.execution.task_control import TaskControlService

                service = TaskControlService(
                    db,
                    store=environment.suite.store,
                    recorder=environment.suite.recorder,
                    backend="postgres",
                    ledger_source="core",
                    session_factory=environment.session_factory,
                    dispatcher=None,
                    worker=worker,
                )
                result = await service.submit(
                    workspace_id=platform_driver.WS,
                    user_id=platform_driver.OWNER,
                    goal="controller clarification baseline case",
                    agent_id=agent_id,
                    input=_submit_payload(),
                )
            task_id = uuid.UUID(result.task_ref.id)

            record = await platform_driver._wait_for_state(environment, task_id, states, worker=worker)
            while record.lifecycle_state.value == "waiting_input":
                if len(parks) >= cspec.MAX_CLARIFICATIONS:
                    raise RuntimeError(f"clarification bound exceeded after {len(parks)} parks")
                wait = (record.extra or {}).get("wait") or {}
                contract_ref = wait.get("contract_ref") or {}
                run_rows = await environment.run_rows(task_id)
                parks.append(
                    {
                        "run_id": str(run_rows[-1].id) if run_rows else None,
                        "wake_kind": wait.get("wake_kind"),
                        "contract_ref": contract_ref,
                        "consumed": wait.get("consumed"),
                        "side_effects_before_park": registry.side_effects,
                        "write_attempts_before_park": registry.write_attempts,
                        "pre_park_model_rounds": len(gateway.rounds),
                    }
                )
                pre_park_rounds = len(gateway.rounds)
                parked_at = time.monotonic()
                async with environment.session_factory() as db:
                    from hecate.execution.task_control import TaskControlService

                    control = TaskControlService(
                        db,
                        store=environment.suite.store,
                        recorder=environment.suite.recorder,
                        backend="postgres",
                        ledger_source="core",
                        session_factory=environment.session_factory,
                        dispatcher=None,
                        worker=worker,
                    )
                    issue = await control.issue_command(
                        workspace_id=platform_driver.WS,
                        task_id=task_id,
                        kind=ControlCommandKind.PROVIDE_INPUT,
                        issuer=str(platform_driver.OWNER),
                        command_id=f"provide-input-{uuid.uuid4()}",
                        payload={cspec.REQUIRED_FIELD: cspec.PROVIDED_ORDER_REF},
                        payload_schema_ref=TASK_USER_INPUT_SCHEMA_REF,
                        detail_ns={"wait_token": wait.get("wait_token")},
                    )
                if issue.record.state.value != "applied":
                    raise RuntimeError(f"provide_input was not applied: {issue.record.state.value} {issue.detail}")
                parks[-1]["wait_ms_harness"] = (time.monotonic() - parked_at) * 1000
                parks[-1]["wake_state"] = issue.record.state.value
                # The durable acknowledgement binds the wake to this exact
                # action identity; an unacknowledged park never consumed.
                from hecate_durable.contracts.references import BackendRef, RefKind

                task_ref = BackendRef(RefKind.TASK, "hecate", str(task_id))
                task_input = await asyncio.to_thread(environment.store.get_task_input, task_ref)
                parks[-1]["acknowledged"] = contract_ref.get("execution_id") in (
                    (task_input or {}).get("_input_wait_acknowledgements") or []
                )
                record = await platform_driver._wait_for_state(environment, task_id, states, worker=worker)

            observation.update(
                await platform_driver._observation_base(environment, task_id, gateway, registry, started)
            )
            observation["task_status"] = record.lifecycle_state.value
            result_extra = (record.extra or {}).get("result") or {}
            observation["final_content"] = result_extra.get("content")
            observation["error_category"] = result_extra.get("error_category")
            observation["error"] = result_extra.get("error")
            observation["reconciliation_reason"] = (record.extra or {}).get("reconciliation_reason")
            observation["model_calls"] = len(gateway.rounds)
            observation["write_attempts"] = registry.write_attempts
            observation["side_effect_count"] = registry.side_effects
            observation["usage"] = platform_driver._usage(gateway.rounds)
            observation["business_calls"] = list(registry.calls)
            observation["approval"] = None
            observation["rebuild"] = {"performed": False}
            from hecate_durable.contracts.references import BackendRef, RefKind

            revisions = await asyncio.to_thread(
                environment.store.list_input_revisions,
                BackendRef(RefKind.TASK, "hecate", str(task_id)),
            )
            observation["clarification"] = {
                "parks": parks,
                "park_count": len(parks),
                "input_revisions": [
                    json.loads(json.dumps(revision.get("content"), default=str)) for revision in revisions
                ],
                "supplement_visible_in_model_input": _supplement_visible(gateway, pre_park_rounds),
                # The responder is the deterministic stand-in for a human:
                # the wait it measures is harness time, never a human wait.
                "responder": "deterministic_injection",
            }
            observation["collection_error"] = None
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = platform_driver._fallback_observation(gateway, registry, started, exc)
        observation["business_calls"] = list(registry.calls)
        observation["usage"] = platform_driver._usage(gateway.rounds)
        observation["clarification"] = {
            "parks": parks,
            "park_count": len(parks),
            "input_revisions": [],
            "supplement_visible_in_model_input": None,
            "responder": "deterministic_injection",
        }
    finally:
        pending = tuple(_background_tasks - previous_tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        patch.unpatch()
        await environment.close()
    return observation
