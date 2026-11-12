"""Platform driver for the Controller trusted-field baseline.

Runs one trusted-field case through the real platform controller assembly —
the same ``PlatformEnvironment``, production runtime port, governance facade,
durable action ledger and input revision store the other families use. The
agent version carries the host-declared field provenance profile in its
frozen configuration. The synthetic business registry has NO provenance
logic: every write that reaches it lands, so a blocked write in these
records was blocked by the production guard before the business call, and
the guard's verdict is read back from the durable action ledger. Input
waits park on the real ``waiting_input`` state and wake through a real
``provide_input`` command; ``rebuild_after_park`` reassembles the whole host
over the persisted SQLite state before the wake.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from hecate.execution.field_provenance import FIELD_PROVENANCE_REFUSED
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec


class TrustedFieldRegistry(platform_driver._SyntheticRegistry):
    """Synthetic business boundary without any provenance logic of its own."""

    def __init__(self, case: str, values: dict[str, str] | None = None, *, write_failure: str | None = None) -> None:
        super().__init__(case)
        self.values = {**tspec.DEFAULT_VALUES, **(values or {})}
        self.write_failure = write_failure

    async def describe(self, name: str) -> dict[str, Any]:
        from tests.scenarios.baseline import controller_host_clarification_spec as cspec

        return {"parameters": cspec.TOOL_SCHEMAS[name]}

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == tspec.READ_TOOL:
            content = tspec.read_content(self.case, self.values)
            self.receipts.append({"tool": name, "arguments": dict(args), "status": "succeeded", "content": content})
            return dict(content)
        if name == tspec.WRITE_TOOL:
            self.write_attempts += 1
            if self.write_failure == "timeout":
                self.receipts.append({"tool": name, "arguments": dict(args), "status": "outcome_unknown"})
                raise TimeoutError("synthetic lost write receipt")
            self.side_effects += 1
            receipt = {"status": "ok", "update_id": tspec.RECEIPT_ID}
            self.receipts.append({"tool": name, "arguments": dict(args), "status": "succeeded", "content": receipt})
            return dict(receipt)
        raise ValueError("unsupported tool")


def _submit_payload() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": tspec.USER_PROMPT}],
        "controller_loop": True,
        "controller": {"max_turns": 8},
        "output_schema": tspec.OUTPUT_SCHEMA,
    }


def guard_verdicts(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify each recorded write outcome from the durable action ledger."""

    verdicts: list[dict[str, Any]] = []
    for action in actions:
        if (action.get("intent") or {}).get("action_name") != tspec.WRITE_TOOL:
            continue
        payload = action.get("result_payload")
        outcome = (action.get("last_outcome") or {}).get("outcome")
        contract = payload.get("contract") if isinstance(payload, dict) else None
        if (
            isinstance(payload, dict)
            and payload.get("status") == "input_required"
            and isinstance(contract, dict)
            and isinstance(contract.get("field_provenance"), dict)
        ):
            verdicts.append({"verdict": "input_required", "fields": list(contract.get("fields") or [])})
            continue
        refusal = _refusal(payload)
        if refusal is not None:
            verdicts.append({"verdict": "refused", "reason": refusal.get("reason")})
        elif outcome == "succeeded":
            verdicts.append({"verdict": "executed"})
        else:
            verdicts.append({"verdict": outcome or "unsettled"})
    return verdicts


def _refusal(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, str):
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed.get("status") == FIELD_PROVENANCE_REFUSED else None


async def run_trusted_field_case(
    case: str,
    provider: Any,
    root: Path,
    *,
    values: dict[str, str] | None = None,
    declare_profile: bool = True,
    rebuild_after_park: bool = False,
    write_failure: str | None = None,
) -> dict[str, Any]:
    """Run one trusted-field case through the real platform assembly."""

    from hecate_durable.contracts.references import BackendRef, RefKind
    from hecate_runtime.eventstore import InMemoryEventStore

    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_control import TaskControlService, _background_tasks
    from hecate.execution.task_dispatcher import run_row_ref
    from hecate.execution.task_input import TASK_USER_INPUT_SCHEMA_REF

    resolved = {**tspec.DEFAULT_VALUES, **(values or {})}
    environment_label = f"{case}-{uuid.uuid4().hex[:8]}"
    environment = platform_driver.PlatformEnvironment(root, environment_label)
    registry = TrustedFieldRegistry(case, resolved, write_failure=write_failure)
    gateway = platform_driver._RecordingGateway(provider)
    patch = platform_driver._SeamPatch(
        registry, gateway, InMemoryEventStore(), tool_definitions=tspec.tool_definitions()
    )
    started = time.monotonic()
    observation: dict[str, Any] = {}
    task_id: uuid.UUID | None = None
    previous_tasks = set(_background_tasks)
    states = {"waiting_input", *platform_driver.TERMINAL_STATES}
    parks: list[dict[str, Any]] = []
    rebuilds = 0

    def service(db: Any, worker: Any) -> TaskControlService:
        return TaskControlService(
            db,
            store=environment.suite.store,
            recorder=environment.suite.recorder,
            backend="postgres",
            ledger_source="core",
            session_factory=environment.session_factory,
            dispatcher=None,
            worker=worker,
        )

    deadline = asyncio.timeout(platform_driver.CASE_TIME_BUDGET_SECONDS)
    try:
        async with deadline:
            patch.patch()
            worker = environment.make_worker()
            agent_id = await environment._seed(
                with_approval_rule=False,
                persona=tspec.SYSTEM_PROMPT,
                config_extra={"field_provenance": tspec.PROFILE_DECLARATION} if declare_profile else None,
            )
            async with environment.session_factory() as db:
                result = await service(db, worker).submit(
                    workspace_id=platform_driver.WS,
                    user_id=platform_driver.OWNER,
                    goal="controller trusted-field baseline case",
                    agent_id=agent_id,
                    input=_submit_payload(),
                )
            task_id = uuid.UUID(result.task_ref.id)
            task_ref = BackendRef(RefKind.TASK, "hecate", str(task_id))
            record = await platform_driver._wait_for_state(environment, task_id, states, worker=worker)
            while record.lifecycle_state.value == "waiting_input":
                if len(parks) >= tspec.MAX_CLARIFICATIONS:
                    raise RuntimeError(f"clarification bound exceeded after {len(parks)} parks")
                wait = (record.extra or {}).get("wait") or {}
                run_rows = await environment.run_rows(task_id)
                parks.append(
                    {
                        "run_id": str(run_rows[-1].id) if run_rows else None,
                        "wake_kind": wait.get("wake_kind"),
                        "contract_ref": wait.get("contract_ref") or {},
                        "business_calls_before_park": len(registry.calls),
                        "landed_writes_before_park": registry.side_effects,
                        "model_rounds_before_park": len(gateway.rounds),
                    }
                )
                if rebuild_after_park:
                    # Reopen the SQL state and drop every in-process object:
                    # the wake and the guard must rebuild from durable facts.
                    if await worker.drain(timeout=5):
                        raise RuntimeError("platform dispatch did not drain before host rebuild")
                    await environment.close()
                    environment = platform_driver.PlatformEnvironment(root, environment_label)
                    patch._event_store = InMemoryEventStore()
                    gateway.phase = "post_rebuild"
                    worker = environment.make_worker()
                    rebuilds += 1
                async with environment.session_factory() as db:
                    issue = await service(db, worker).issue_command(
                        workspace_id=platform_driver.WS,
                        task_id=task_id,
                        kind=ControlCommandKind.PROVIDE_INPUT,
                        issuer=str(platform_driver.OWNER),
                        command_id=f"provide-input-{uuid.uuid4()}",
                        payload={tspec.PROTECTED_FIELD: resolved["provided"]},
                        payload_schema_ref=TASK_USER_INPUT_SCHEMA_REF,
                        detail_ns={"wait_token": wait.get("wait_token")},
                    )
                parks[-1]["wake_state"] = issue.record.state.value
                if issue.record.state.value != "applied":
                    raise RuntimeError(f"provide_input was not applied: {issue.record.state.value} {issue.detail}")
                record = await platform_driver._wait_for_state(environment, task_id, states, worker=worker)

            observation.update(
                await platform_driver._observation_base(environment, task_id, gateway, registry, started)
            )
            runs = await environment.run_rows(task_id)
            actions = await asyncio.to_thread(environment.store.list_run_actions, run_row_ref(runs[-1])) if runs else []
            revisions = await asyncio.to_thread(environment.store.list_input_revisions, task_ref)
            result_extra = (record.extra or {}).get("result") or {}
            observation.update(
                {
                    "task_status": record.lifecycle_state.value,
                    "final_content": result_extra.get("content"),
                    "error_category": result_extra.get("error_category"),
                    "error": result_extra.get("error"),
                    "field_provenance_stop": result_extra.get("field_provenance"),
                    "reconciliation_reason": (record.extra or {}).get("reconciliation_reason"),
                    "guard_verdicts": guard_verdicts(actions),
                    "values": dict(resolved),
                    "profile": tspec.PROFILE.pin() if declare_profile else None,
                    "rebuild": {"performed": rebuilds > 0, "count": rebuilds},
                    "clarification": {
                        "parks": parks,
                        "park_count": len(parks),
                        "input_revisions": [
                            json.loads(json.dumps(revision.get("content"), default=str)) for revision in revisions
                        ],
                        "responder": "deterministic_injection",
                    },
                    "collection_error": None,
                }
            )
        observation["diagnostics"] = await platform_driver.read_task_diagnostics(
            environment, task_id, "terminal_state_observed"
        )
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        stragglers = await platform_driver._cancel_case_tasks(previous_tasks)
        end_reason = "case_deadline_exceeded" if deadline.expired() else "collection_exception"
        diagnostics = await platform_driver.read_task_diagnostics(
            environment, task_id, end_reason, error=exc, cleanup_stragglers=stragglers
        )
        observation = platform_driver._fallback_observation(
            gateway,
            registry,
            started,
            exc,
            task_id=str(task_id) if task_id is not None else None,
            run_id=observation.get("run_id"),
            diagnostics=diagnostics,
        )
        observation.update(
            {
                "guard_verdicts": [],
                "values": dict(resolved),
                "profile": tspec.PROFILE.pin() if declare_profile else None,
                "field_provenance_stop": None,
                "clarification": {
                    "parks": parks,
                    "park_count": len(parks),
                    "input_revisions": [],
                    "responder": "deterministic_injection",
                },
            }
        )
    finally:
        stragglers = await platform_driver._cancel_case_tasks(previous_tasks)
        platform_driver._refresh_recording(observation, gateway, registry)
        platform_driver._record_cleanup_stragglers(observation, stragglers)
        patch.unpatch()
        await environment.close()
    return observation
