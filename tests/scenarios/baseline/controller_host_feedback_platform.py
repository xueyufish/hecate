"""Platform driver for the Controller feedback baseline.

Runs one feedback case through the real platform assembly — the same
``PlatformEnvironment``, governance facade, seam patches and durable
store the dual-host and clarification drivers use. The synthetic
registry subclasses the dual-host one with the business-feedback
behavior: the write tool rejects the suggested quantity with an
explicit ``applied=false`` correction (zero side effects), lands the
corrected quantity, and can lose its receipt through the existing
outcome-unknown path after the side effect has landed. The test-side
business counters stay in the observation as independent evidence.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from tests.scenarios.baseline import controller_host_feedback_spec as fspec
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_spec as spec


class FeedbackRegistry(platform_driver._SyntheticRegistry):
    """Synthetic business boundary with the paired feedback behavior."""

    async def describe(self, name: str) -> dict[str, Any]:
        return {"parameters": fspec.TOOL_SCHEMAS[name]}

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == spec.READ_TOOL:
            self.receipts.append(
                {
                    "tool": name,
                    "arguments": dict(args),
                    "status": "succeeded",
                    "content": dict(fspec.read_content(self.case)),
                }
            )
            return dict(fspec.read_content(self.case))
        if name == spec.WRITE_TOOL:
            self.write_attempts += 1
            if self.case == "unknown_write_stop":
                # The write lands exactly once; only its receipt is lost.
                self.side_effects += 1
                self.receipts.append(
                    {"tool": name, "arguments": dict(args), "status": "outcome_unknown", "content": None}
                )
                raise TimeoutError("synthetic lost write receipt")
            if args.get("quantity") != fspec.CORRECTED_QUANTITY:
                # Explicit business rejection: no side effect, corrective
                # feedback the model must consume.
                rejection = fspec.rejection_result()
                self.rejections.append(dict(rejection))
                self.receipts.append(
                    {"tool": name, "arguments": dict(args), "status": "rejected", "content": dict(rejection)}
                )
                return dict(rejection)
            self.side_effects += 1
            receipt = {"status": "ok", "update_id": fspec.FEEDBACK_RECEIPT_ID}
            self.receipts.append(
                {"tool": name, "arguments": dict(args), "status": "succeeded", "content": dict(receipt)}
            )
            return dict(receipt)
        raise ValueError("unsupported tool")

    def __init__(self, case: str) -> None:
        super().__init__(case)
        self.rejections: list[dict[str, Any]] = []


def _submit_payload() -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": fspec.USER_PROMPT}],
        "controller_loop": True,
        "controller": {"max_turns": 8},
        "output_schema": fspec.OUTPUT_SCHEMA,
    }


async def run_feedback_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    """Run one feedback case through the real platform assembly."""

    from hecate_runtime.eventstore import InMemoryEventStore

    environment_label = f"{case}-{uuid.uuid4().hex[:8]}"
    environment = platform_driver.PlatformEnvironment(root, environment_label)
    event_store = InMemoryEventStore()
    registry = FeedbackRegistry(case)
    gateway = platform_driver._RecordingGateway(provider)
    patch = platform_driver._SeamPatch(registry, gateway, event_store, tool_definitions=fspec.tool_definitions())
    started = time.monotonic()
    observation: dict[str, Any] = {}
    from hecate.execution.task_control import _background_tasks

    previous_tasks = set(_background_tasks)
    try:
        async with asyncio.timeout(platform_driver.CASE_TIME_BUDGET_SECONDS):
            patch.patch()
            worker = environment.make_worker()
            agent_id = await environment._seed(with_approval_rule=False, persona=fspec.SYSTEM_PROMPT)
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
                    goal="controller feedback baseline case",
                    agent_id=agent_id,
                    input=_submit_payload(),
                )
            task_id = uuid.UUID(result.task_ref.id)
            record = await platform_driver._wait_for_state(
                environment, task_id, platform_driver.TERMINAL_STATES, worker=worker
            )
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
            observation["feedback"] = {
                # The rejection payloads and the business counters are the
                # test-side evidence, recorded independently of the model.
                "rejections": list(registry.rejections),
                "business_write_count": registry.side_effects,
                "business_attempts": registry.write_attempts,
                "responder": "deterministic_injection",
            }
            observation["collection_error"] = None
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = platform_driver._fallback_observation(gateway, registry, started, exc)
        observation["business_calls"] = list(registry.calls)
        observation["usage"] = platform_driver._usage(gateway.rounds)
        observation["feedback"] = {
            "rejections": list(registry.rejections),
            "business_write_count": registry.side_effects,
            "business_attempts": registry.write_attempts,
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
