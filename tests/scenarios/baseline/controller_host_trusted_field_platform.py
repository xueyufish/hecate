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
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import patch as patch_attribute

from httpx import AsyncClient

from hecate.execution.field_provenance import FIELD_PROVENANCE_REFUSED
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec


class TrustedFieldRegistry(platform_driver._SyntheticRegistry):
    """Synthetic business boundary without any provenance logic of its own."""

    def __init__(
        self,
        case: str,
        values: dict[str, str] | None = None,
        *,
        write_failure: str | None = None,
        model_rounds: list[dict[str, Any]] | None = None,
        read_content_fn: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(case)
        self.values = {**tspec.DEFAULT_VALUES, **(values or {})}
        self.write_failure = write_failure
        self.model_rounds = model_rounds
        self.business_call_rounds: list[int | None] = []
        # Optional sample-pack override: serves the pack's desensitized
        # receipt body instead of the offline family's fixed material. The
        # default stays the offline family's own read content.
        self._read_content = read_content_fn or tspec.read_content

    async def describe(self, name: str) -> dict[str, Any]:
        from tests.scenarios.baseline import controller_host_clarification_spec as cspec

        return {"parameters": cspec.TOOL_SCHEMAS[name]}

    async def execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        self.business_call_rounds.append(len(self.model_rounds) - 1 if self.model_rounds else None)
        self.calls.append({"tool": name, "arguments": dict(args)})
        if name == tspec.READ_TOOL:
            content = self._read_content(self.case, self.values)
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


def _submit_payload(user_prompt: str | None = None) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": tspec.USER_PROMPT if user_prompt is None else user_prompt}],
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


@asynccontextmanager
async def _serve_task_routes(
    environment: platform_driver.PlatformEnvironment, worker: Any
) -> AsyncIterator[tuple[AsyncClient, Any]]:
    from fastapi import FastAPI
    from hecate_durable.worker import OutboxRelay

    from hecate.channel.api import tasks as tasks_api
    from hecate.core.deps import get_db
    from hecate.enterprise.auth.token import create_access_token
    from hecate.execution.event_relay import PlatformOutboxProjector
    from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
    from tests.scenarios.tools.managed_platform import serve_over_tcp, stop_tcp_server

    sessions = environment.session_factory
    workspace = platform_driver.WS
    owner = platform_driver.OWNER
    async with sessions() as db:
        db.add(WorkspaceMemberModel(user_id=owner, workspace_id=workspace, role=WorkspaceRole.EDITOR))
        await db.commit()

    async def request_db() -> AsyncIterator[Any]:
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(tasks_api.router, prefix="/api")
    app.include_router(tasks_api.commands_router, prefix="/api")
    app.include_router(tasks_api.runs_router, prefix="/api")
    app.dependency_overrides[get_db] = request_db
    relay = OutboxRelay(
        environment.store.session_factory, PlatformOutboxProjector(environment.store, sessions), relay_key="platform"
    )
    state = SimpleNamespace(durable_worker=worker, durable_outbox_relay=relay)
    token = create_access_token(owner, platform_driver.ORG, workspace, WorkspaceRole.EDITOR.value)
    async with AsyncExitStack() as stack:
        stack.enter_context(patch_attribute.object(tasks_api, "get_durable_suite", return_value=environment.suite))
        tasks_api.bind_worker_state(state)
        stack.callback(tasks_api.unbind_worker_state, state)
        server, serving, url = await serve_over_tcp(app)
        stack.push_async_callback(stop_tcp_server, server, serving)
        client = await stack.enter_async_context(
            AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"}, timeout=15, trust_env=False)
        )
        yield client, relay


async def _inbox_notice(
    client: AsyncClient, relay: Any, task_id: uuid.UUID, run_id: str, kind: str, cursor: str | None = None
) -> tuple[dict[str, Any], str]:
    while True:
        await relay.pump_once()
        response = await client.get(
            f"/api/tasks/{task_id}/result-inbox", params={"cursor": cursor} if cursor is not None else None
        )
        response.raise_for_status()
        body = response.json()
        matches = [notice for notice in body["notifications"] if notice["type"] == kind]
        if matches:
            (notice,) = matches
            if notice["task_id"] != str(task_id) or notice["run_id"] != run_id:
                raise ValueError("REST inbox notice has the wrong Task/Run binding")
            return notice, body["next_cursor"]
        await asyncio.sleep(0.05)


async def run_trusted_field_case(
    case: str,
    provider: Any,
    root: Path,
    *,
    values: dict[str, str] | None = None,
    declare_profile: bool = True,
    rebuild_after_park: bool = False,
    write_failure: str | None = None,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    read_content_fn: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    transport: Literal["service", "rest_tcp"] = "service",
) -> dict[str, Any]:
    """Run one trusted-field case through the real platform assembly.

    ``system_prompt`` replaces the family's default persona in the seeded
    agent version, so an experiment's candidate instruction reaches the
    model through the same frozen configuration path as the default.
    ``user_prompt`` and ``read_content_fn`` likewise ride in with defaults
    that keep the offline family's fixed prompt and read content.
    The REST transport serves the production routes on loopback TCP; the
    service transport also supports the host-rebuild fault injection.
    """

    from hecate_durable.contracts.references import BackendRef, RefKind
    from hecate_runtime.eventstore import InMemoryEventStore

    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_control import TaskControlService, _background_tasks
    from hecate.execution.task_dispatcher import run_row_ref
    from hecate.execution.task_input import TASK_USER_INPUT_SCHEMA_REF

    if transport == "rest_tcp" and rebuild_after_park:
        raise ValueError("the REST transport does not support rebuilding the host while parked")
    resolved = {**tspec.DEFAULT_VALUES, **(values or {})}
    environment_label = f"{case}-{uuid.uuid4().hex[:8]}"
    environment = platform_driver.PlatformEnvironment(root, environment_label)
    gateway = platform_driver._RecordingGateway(provider)
    registry = TrustedFieldRegistry(
        case, resolved, write_failure=write_failure, model_rounds=gateway.rounds, read_content_fn=read_content_fn
    )
    patch = platform_driver._SeamPatch(
        registry, gateway, InMemoryEventStore(), tool_definitions=tspec.tool_definitions()
    )
    started = time.monotonic()
    observation: dict[str, Any] = {}
    task_id: uuid.UUID | None = None
    previous_tasks = set(_background_tasks)
    states = {"waiting_input", *platform_driver.TERMINAL_STATES}
    parks: list[dict[str, Any]] = []
    applied_inputs: list[dict[str, str]] = []
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

    rest_stack = AsyncExitStack()
    client: AsyncClient | None = None
    rest_run_id: str | None = None
    inbox_cursor: str | None = None
    deadline = asyncio.timeout(platform_driver.CASE_TIME_BUDGET_SECONDS)
    try:
        async with deadline:
            patch.patch()
            worker = environment.make_worker()
            agent_id = await environment._seed(
                with_approval_rule=False,
                persona=tspec.SYSTEM_PROMPT if system_prompt is None else system_prompt,
                config_extra={"field_provenance": tspec.PROFILE_DECLARATION} if declare_profile else None,
            )
            goal = "controller trusted-field baseline case"
            payload = _submit_payload(user_prompt)
            if transport == "rest_tcp":
                client, relay = await rest_stack.enter_async_context(_serve_task_routes(environment, worker))
                response = await client.post(
                    "/api/tasks",
                    json={"goal": goal, "agent_id": str(agent_id), "input": payload, "result_delivery": "rest_inbox"},
                )
                response.raise_for_status()
                refs = response.json()
                task_id = uuid.UUID(refs["task_id"])
                rest_run_id = refs["run_id"]
            else:
                async with environment.session_factory() as db:
                    result = await service(db, worker).submit(
                        workspace_id=platform_driver.WS,
                        user_id=platform_driver.OWNER,
                        goal=goal,
                        agent_id=agent_id,
                        input=payload,
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
                input_payload = {tspec.PROTECTED_FIELD: resolved["provided"]}
                command_id = f"provide-input-{uuid.uuid4()}"
                if client is not None and rest_run_id is not None:
                    notice, inbox_cursor = await _inbox_notice(
                        client, relay, task_id, rest_run_id, "input_required", inbox_cursor
                    )
                    detail = await client.get(notice["views"]["task"])
                    detail.raise_for_status()
                    view = detail.json()
                    response = await client.post(
                        notice["views"]["commands"],
                        json={
                            "kind": "provide_input",
                            "command_id": command_id,
                            "expected_revision": view["revision"],
                            "payload": input_payload,
                            "payload_schema_ref": TASK_USER_INPUT_SCHEMA_REF,
                            "detail_ns": {"wait_token": view["wait"]["wait_token"]},
                        },
                    )
                    response.raise_for_status()
                    wake_state = response.json()["state"]
                else:
                    async with environment.session_factory() as db:
                        issue = await service(db, worker).issue_command(
                            workspace_id=platform_driver.WS,
                            task_id=task_id,
                            kind=ControlCommandKind.PROVIDE_INPUT,
                            issuer=str(platform_driver.OWNER),
                            command_id=command_id,
                            payload=input_payload,
                            payload_schema_ref=TASK_USER_INPUT_SCHEMA_REF,
                            detail_ns={"wait_token": wait.get("wait_token")},
                        )
                    wake_state = issue.record.state.value
                parks[-1]["wake_state"] = wake_state
                if wake_state != "applied":
                    raise RuntimeError(f"provide_input was not applied: {wake_state}")
                applied_inputs.append(input_payload)
                record = await platform_driver._wait_for_state(environment, task_id, states, worker=worker)

            observation.update(
                await platform_driver._observation_base(environment, task_id, gateway, registry, started)
            )
            runs = await environment.run_rows(task_id)
            actions = await asyncio.to_thread(environment.store.list_run_actions, run_row_ref(runs[-1])) if runs else []
            revisions = await asyncio.to_thread(environment.store.list_input_revisions, task_ref)
            result_extra = (record.extra or {}).get("result") or {}
            if client is not None and rest_run_id is not None:
                if rest_run_id != observation["run_id"]:
                    raise ValueError("REST submission and terminal state identify different Runs")
                observation["rest_delivery"] = "not_delivered"
                if record.lifecycle_state.value == "succeeded":
                    notice, _ = await _inbox_notice(client, relay, task_id, rest_run_id, "result_ready", inbox_cursor)
                    detail = await client.get(notice["views"]["run_detail"])
                    detail.raise_for_status()
                    run = detail.json()
                    acceptance = run.get("delivery_acceptance") or {}
                    content = result_extra.get("content")
                    if (
                        run["projection"]["state"] == "succeeded"
                        and acceptance.get("status") == "passed"
                        and isinstance(content, str)
                        and acceptance.get("content_sha256") == hashlib.sha256(content.encode()).hexdigest()
                    ):
                        observation["rest_delivery"] = "accepted"
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
                    "input_revisions": applied_inputs,
                    "responder": "deterministic_injection",
                },
            }
        )
    finally:
        stragglers = await platform_driver._cancel_case_tasks(previous_tasks)
        platform_driver._refresh_recording(observation, gateway, registry)
        observation["business_call_rounds"] = registry.business_call_rounds
        platform_driver._record_cleanup_stragglers(observation, stragglers)
        try:
            await rest_stack.aclose()
        finally:
            patch.unpatch()
            await platform_driver._close_case_environment(environment, observation)
    return observation
