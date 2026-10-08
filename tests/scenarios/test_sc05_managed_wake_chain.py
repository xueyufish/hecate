"""SC05 wake-chain slices: managed waiting -> platform command -> wake -> successor.

Process-level acceptance over an installed Runner wheel and real platform
HTTP (TCP): a managed delivery parks at a durable approval wait, the
platform issues a resume command through the task-control application
service (the managed branch persists the command for host delivery), and
the host applies it through its durable command inbox — one-time token
consumption, successor attempt, effect receipt, successor association,
terminal projection. Fault variants cover a host restart while waiting, a
control-plane server restart around delivery, a lost effect-receipt
upload, and an expired command. The platform's event upload only accepts
confirmed Task/Run mappings, so a projected successor terminal implies
its association landed first.

The fixed tool plan runs ``submit_inventory_update`` (protected write)
before ``query_inventory`` (approval-gated): the pre-wait side effect is
a real business write whose call count must stay one across the whole
chain, while the post-wake read completes the successor attempt.

PostgreSQL parametrization (runner-side database) follows
``HECATE_STEP6_POSTGRES_URL``; the platform command recorder is
file-backed per test (the shared stack binds it in
``tests/scenarios/tools/managed_platform.py``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from tests.scenarios.tools import runner_harness
from tests.scenarios.tools.managed_platform import (
    ADMIN,
    ISSUER,
    ROOT_NAME,
    SECRET,
    WS,
    build_managed_stack,
    serve_over_tcp,
)

pytestmark = [
    pytest.mark.skipif(
        not runner_harness.uv_available(),
        reason="uv is required for the clean-install harness",
    ),
]

READ_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku"],
    "properties": {
        "domain": {"type": "string", "minLength": 1},
        "sku": {"type": "string", "minLength": 1},
    },
}
WRITE_SCHEMA = {
    "type": "object",
    "required": ["domain", "sku", "quantity"],
    "properties": {
        "domain": {"type": "string", "minLength": 1},
        "sku": {"type": "string", "minLength": 1},
        "quantity": {"type": "integer", "minimum": 1},
    },
}
TOOL_SCHEMAS = {
    "schemas/read.json": READ_SCHEMA,
    "schemas/write.json": WRITE_SCHEMA,
}
# Protected write first (the pre-wait side effect), approval-gated read
# second (the waiting segment the platform command wakes).
WAKE_MANIFEST_TOOLS = [
    {"name": "submit_inventory_update", "schema_ref": "schemas/write.json", "permission": "write"},
    {"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "approval_required"},
]
WAKE_TOOL_ALLOWLIST = ["submit_inventory_update", "query_inventory"]
WAKE_TOOL_ARGUMENTS = {
    "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 3},
    "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
}


@pytest.fixture(scope="module")
def wheel_dist(tmp_path_factory) -> Path:
    dist = Path(tmp_path_factory.mktemp("wake-wheels"))
    runner_harness.ensure_wheels(dist)
    return dist


@pytest.fixture
def auth_context():
    from hecate.core.auth_context import AuthContext
    from hecate.models.workspace_member import WorkspaceRole

    return AuthContext(
        user_id=ADMIN, org_id=WS, workspace_id=WS, role=WorkspaceRole.ADMIN, auth_method="jwt", api_key_scope=None
    )


@pytest.fixture
async def wake_stack(auth_context, managed_secrets, tmp_path):
    async with build_managed_stack(auth_context, tmp_path) as stack:
        yield stack


def _write_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if (c.get("arguments") or {}).get("quantity") is not None]


def _read_calls(calls: list[dict]) -> list[dict]:
    result = []
    for call in calls:
        args = call.get("arguments") or {}
        if args.get("quantity") is None and args.get("domain") is not None:
            result.append(call)
    return result


def _control_plane(base_url: str) -> dict:
    return {
        "base_url": base_url,
        "workspace_id": str(WS),
        "trust_root": ROOT_NAME,
        "host_id": "installed-runner-wake",
        "issuer_domain": ISSUER,
        "secret_ref": "file:secrets/managed-secret",
        "poll_interval_seconds": 0.05,
        "data_domains": ["domain_a"],
    }


def _host_database_url(runner, database_url: str | None) -> str:
    return database_url or f"sqlite:///{(runner.workdir / 'host.db').as_posix()}"


def _host_task_state(runner, delivery_id: str, database_url: str | None):
    """Read the host's durable task state (the runner HTTP identity scope
    covers the managed principal, not the test's reader token)."""

    from hecate_durable.contracts.references import BackendRef, RefKind
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(_host_database_url(runner, database_url))
    try:
        return store.get_task_state(BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_id}"))
    finally:
        store.dispose()


async def _wait_local_waiting(stack, runner, delivery_id: str, database_url: str | None, timeout: float = 30.0) -> dict:
    """Wait until the host's durable run parks at waiting_approval.

    Returns the authorized wait view (token included); the wait record is
    the host's own fact, persisted with the task state.
    """

    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(_host_task_state, runner, delivery_id, database_url)
        if record is not None:
            from hecate_durable.contracts.durable import TaskLifecycleState

            last = {"lifecycle": record.lifecycle_state.value, "wait": (record.extra or {}).get("wait")}
            if record.lifecycle_state is TaskLifecycleState.WAITING_APPROVAL:
                wait = (record.extra or {}).get("wait") or {}
                if wait.get("wait_token") and not wait.get("consumed"):
                    return dict(wait)
        await asyncio.sleep(0.1)
    from hecate_durable.storage import SqlDurableStore

    diag_store = SqlDurableStore(_host_database_url(runner, database_url))
    try:
        all_tasks = [(r.task_ref.id, r.lifecycle_state.value) for r in diag_store.list_tasks()]
    finally:
        diag_store.dispose()
    async with stack["session_factory"]() as db:
        from sqlalchemy import select

        from hecate.models.managed_delivery import ManagedDeliveryModel

        rows = (await db.execute(select(ManagedDeliveryModel.id))).scalars().all()
        platform_deliveries = [str(row) for row in rows]
    raise AssertionError(
        f"task for delivery {delivery_id} never reached waiting_approval; last={last}; "
        f"all_tasks={all_tasks}; platform_deliveries={platform_deliveries}"
    )


async def _wait_command_state(stack, command_id: str, expected: set[str], timeout: float = 30.0) -> Any:
    from hecate.contracts.execution.durable import CommandState

    wanted = {CommandState(state) for state in expected}
    platform_store = stack["platform_store"]
    deadline = asyncio.get_running_loop().time() + timeout
    record = None
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(platform_store.get, command_id)
        if record is not None and record.state in wanted:
            return record
        await asyncio.sleep(0.05)
    raise AssertionError(f"command {command_id} did not reach {expected}; record={record}")


async def _wait_enrollment(sf) -> uuid.UUID:
    from sqlalchemy import select

    from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        async with sf() as db:
            row = (
                (
                    await db.execute(
                        select(StandaloneEnrollmentModel).where(
                            StandaloneEnrollmentModel.workspace_id == WS,
                            StandaloneEnrollmentModel.deleted.is_(False),
                        )
                    )
                )
                .scalars()
                .first()
            )
            if row is not None:
                return row.id
        await asyncio.sleep(0.05)
    raise AssertionError("installed Runner did not register over platform HTTP")


async def _opt_in(sf, enrollment_id: uuid.UUID) -> None:
    from hecate.execution.enrollment_resolver import HmacEnrollmentResolver
    from hecate.execution.task_run_registry import TaskRunRegistry

    async with sf() as db:
        resolver = HmacEnrollmentResolver(db, secret_candidates={ROOT_NAME: SECRET})
        await TaskRunRegistry(db, enrollment_resolver=resolver.verify).set_managed_opt_in(
            enrollment_id,
            WS,
            managed_new_runs=True,
            operator_id=ADMIN,
            admitted=True,
            managed_scope=["domain_a"],
        )
        await db.commit()


async def _platform_task_id(sf, delivery_id: str) -> uuid.UUID:
    from hecate.models.managed_delivery import ManagedDeliveryModel

    async with sf() as db:
        delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
        assert delivery is not None
        return uuid.UUID(delivery.task_ref["id"])


async def _issue_wake(
    stack,
    task_id: uuid.UUID,
    *,
    command_id: str,
    wait_token: str,
    expires_at: datetime | None = None,
) -> Any:
    """Record a resume command through the real task-control service.

    The managed branch persists the command for host delivery (no inline
    enforcement), which is exactly the path the public command API takes
    for enrolled deployments.
    """

    from hecate.contracts.execution.durable import ControlCommandKind
    from hecate.execution.task_control import TaskControlService

    platform_store = stack["platform_store"]
    async with stack["session_factory"]() as db:
        service = TaskControlService(
            db,
            store=platform_store,
            recorder=platform_store,
            backend="postgres",
            ledger_source="core",
            session_factory=stack["session_factory"],
        )
        result = await service.issue_command(
            workspace_id=WS,
            task_id=task_id,
            kind=ControlCommandKind.RESUME,
            issuer="wake-acceptance",
            command_id=command_id,
            payload=None,
            expires_at=(expires_at or datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
            detail_ns={"wait_token": wait_token},
        )
        await db.commit()
        return result


async def _wait_projected_succeeded(stack, delivery_id: str, runner, calls) -> dict:
    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunModel

    deadline = asyncio.get_running_loop().time() + 30
    observed: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        async with stack["session_factory"]() as db:
            delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
            if delivery is not None:
                run = await db.get(RunModel, uuid.UUID(delivery.run_ref["id"]))
                projection = dict(run.projection or {}) if run is not None else {}
                # Successor association rides accepted_refs; the successor's
                # own events are only accepted for confirmed mappings, so a
                # projected successor terminal implies the association landed.
                for attempt in (delivery.accepted_refs or {}).get("successor_runs") or []:
                    platform_ref = attempt.get("platform_run_ref") or {}
                    if platform_ref.get("issuer_domain") == "hecate":
                        projected_run = await db.get(RunModel, uuid.UUID(platform_ref["id"]))
                        projected = dict(projected_run.projection or {}) if projected_run is not None else {}
                        if projected.get("state") == "succeeded":
                            return projected
                if projection.get("state") == "succeeded":
                    return projection
                observed = {
                    "delivery_state": delivery.state,
                    "accepted_refs": delivery.accepted_refs,
                    "projection": projection,
                }
        await asyncio.sleep(0.1)
    log_path = runner.workdir / "runner.log"
    runner_log = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path else ""
    local_run_id = f"managed-run-{delivery_id}"
    runner_status = runner.request("GET", f"/runs/{local_run_id}") if runner is not None else None
    raise AssertionError(
        f"delivery {delivery_id} did not project a succeeded run; observed={observed}; "
        f"business_calls={calls}; runner_status={runner_status}; runner_log={runner_log}"
    )


class DropFirstCommandEffect(BaseHTTPMiddleware):
    """Drop the first effect-receipt upload with a 503 (lost response)."""

    dropped = 0

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        body = b"".join([chunk async for chunk in response.body_iterator])
        if request.url.path == "/managed/host/commands/effect" and DropFirstCommandEffect.dropped == 0:
            DropFirstCommandEffect.dropped += 1
            return Response(status_code=503)
        return Response(content=body, status_code=response.status_code, headers=dict(response.headers))


@asynccontextmanager
async def _wake_env(
    stack,
    tmp_path: Path,
    wheel_dist: Path,
    database_url: str | None,
    *,
    middleware_cls: type | None = None,
) -> AsyncGenerator[SimpleNamespace, None]:
    """Admitted platform over TCP + installed runner + enrolled host.

    Tests may replace ``env.runner`` (restart) and ``env.server``/
    ``env.server_task`` (control-plane restart); cleanup follows the
    CURRENT handles, not the originals.
    """

    await stack["admit"]()
    if middleware_cls is not None:
        stack["app"].add_middleware(middleware_cls)
    server, server_task, base_url = await serve_over_tcp(stack["app"])
    env = SimpleNamespace(runner=None, business_server=None, server=server, server_task=server_task)
    try:
        runner, business_server, calls = runner_harness.start_runner(
            tmp_path,
            manifest_tools=WAKE_MANIFEST_TOOLS,
            durable=True,
            tool_allowlist=WAKE_TOOL_ALLOWLIST,
            tool_schemas=TOOL_SCHEMAS,
            control_plane=_control_plane(base_url),
            managed_secret=SECRET,
            durable_database_url=database_url,
            dist_dir=wheel_dist,
        )
        env.runner = runner
        env.business_server = business_server
        env.calls = calls
        env.base_url = base_url
        enrollment_id = await _wait_enrollment(stack["session_factory"])
        await _opt_in(stack["session_factory"], enrollment_id)
        yield env
    finally:
        if env.runner is not None:
            env.runner.stop(kill=True)
        if env.business_server is not None:
            runner_harness.stop_business_api(env.business_server)
        env.server.should_exit = True
        await env.server_task


async def _queue_wake_delivery(stack) -> str:
    return await stack["queue_delivery"](
        {"input": {"prompt": "wake chain"}, "tool_arguments": dict(WAKE_TOOL_ARGUMENTS)}
    )


async def test_sc05_wake_chain_succeeds_over_real_http(
    wake_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """Full chain: waiting -> platform resume command -> successor -> terminal."""

    async with _wake_env(wake_stack, tmp_path, wheel_dist, step6_runner_database_url) as env:
        delivery_id = await _queue_wake_delivery(wake_stack)
        waiting = await _wait_local_waiting(wake_stack, env.runner, delivery_id, step6_runner_database_url)
        token = waiting["wait_token"]
        # The pre-wait protected write has executed exactly once when the
        # run parks; nothing after this point may add a second write.
        assert len(_write_calls(env.calls)) == 1

        task_id = await _platform_task_id(wake_stack["session_factory"], delivery_id)
        issued = await _issue_wake(wake_stack, task_id, command_id="wake-happy-1", wait_token=token)
        assert issued.detail == "command persisted for managed host delivery"

        projected = await _wait_projected_succeeded(wake_stack, delivery_id, env.runner, env.calls)
        assert projected["state"] == "succeeded"

        record = await _wait_command_state(wake_stack, "wake-happy-1", {"applied"})
        assert record.state.value == "applied"
        # One pre-wait write, one post-wake read. The task itself advanced
        # to its terminal fact; the ORIGINAL waiting run's platform
        # projection keeps its own waiting fact and does not borrow the
        # successor's success.
        assert len(_write_calls(env.calls)) == 1
        assert len(_read_calls(env.calls)) == 1
        async with wake_stack["session_factory"]() as db:
            from hecate.models.managed_delivery import ManagedDeliveryModel
            from hecate.models.run import RunModel

            delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
            original_run = await db.get(RunModel, uuid.UUID(delivery.run_ref["id"]))
            original_projection = dict(original_run.projection or {}) if original_run is not None else {}
        assert original_projection.get("state") == "waiting_approval"


async def test_sc05_wake_after_host_restart_keeps_prewait_write_once(
    wake_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """A hard host kill while waiting does not redo the pre-wait write."""

    async with _wake_env(wake_stack, tmp_path, wheel_dist, step6_runner_database_url) as env:
        delivery_id = await _queue_wake_delivery(wake_stack)
        waiting = await _wait_local_waiting(wake_stack, env.runner, delivery_id, step6_runner_database_url)
        token = waiting["wait_token"]
        assert len(_write_calls(env.calls)) == 1

        env.runner = env.runner.restart(kill_previous=True)
        assert env.runner.request("GET", "/healthz", token=None)[0] == 200
        assert len(_write_calls(env.calls)) == 1, "restart itself must not redo the pre-wait write"

        task_id = await _platform_task_id(wake_stack["session_factory"], delivery_id)
        await _issue_wake(wake_stack, task_id, command_id="wake-restart-1", wait_token=token)

        projected = await _wait_projected_succeeded(wake_stack, delivery_id, env.runner, env.calls)
        assert projected["state"] == "succeeded"
        await _wait_command_state(wake_stack, "wake-restart-1", {"applied"})
        assert len(_write_calls(env.calls)) == 1, "wake after restart keeps the pre-wait write at one call"
        assert len(_read_calls(env.calls)) == 1


async def test_sc05_wake_survives_platform_restart_and_replays_idempotently(
    wake_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """A command issued while the control plane is down is delivered after restart."""

    async with _wake_env(wake_stack, tmp_path, wheel_dist, step6_runner_database_url) as env:
        delivery_id = await _queue_wake_delivery(wake_stack)
        waiting = await _wait_local_waiting(wake_stack, env.runner, delivery_id, step6_runner_database_url)
        token = waiting["wait_token"]
        assert len(_write_calls(env.calls)) == 1

        # Stop the control plane, record the command against the persisted
        # recorder, then bring the server back on the same port.
        env.server.should_exit = True
        await env.server_task
        task_id = await _platform_task_id(wake_stack["session_factory"], delivery_id)
        command_deadline = datetime.now(UTC) + timedelta(seconds=120)
        await _issue_wake(
            wake_stack,
            task_id,
            command_id="wake-platform-1",
            wait_token=token,
            expires_at=command_deadline,
        )
        env.server, env.server_task, _base = await serve_over_tcp(
            wake_stack["app"], port=int(env.base_url.rsplit(":", 1)[-1])
        )

        projected = await _wait_projected_succeeded(wake_stack, delivery_id, env.runner, env.calls)
        assert projected["state"] == "succeeded"
        await _wait_command_state(wake_stack, "wake-platform-1", {"applied"})

        # Re-issuing the settled command with identical fields replays the
        # original receipt; no second effect, no second business action.
        replay = await _issue_wake(
            wake_stack,
            task_id,
            command_id="wake-platform-1",
            wait_token=token,
            expires_at=command_deadline,
        )
        assert replay.detail == "idempotent replay of a settled command"
        assert replay.record.state.value == "applied"
        assert len(_write_calls(env.calls)) == 1
        assert len(_read_calls(env.calls)) == 1


async def test_sc05_wake_effect_upload_loss_retries_without_double_apply(
    wake_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """A lost effect upload is retried via redelivery and applies once."""

    DropFirstCommandEffect.dropped = 0  # reset across parametrized runs
    async with _wake_env(
        wake_stack,
        tmp_path,
        wheel_dist,
        step6_runner_database_url,
        middleware_cls=DropFirstCommandEffect,
    ) as env:
        delivery_id = await _queue_wake_delivery(wake_stack)
        waiting = await _wait_local_waiting(wake_stack, env.runner, delivery_id, step6_runner_database_url)
        token = waiting["wait_token"]

        task_id = await _platform_task_id(wake_stack["session_factory"], delivery_id)
        await _issue_wake(wake_stack, task_id, command_id="wake-loss-1", wait_token=token)

        projected = await _wait_projected_succeeded(wake_stack, delivery_id, env.runner, env.calls)
        assert projected["state"] == "succeeded"
        await _wait_command_state(wake_stack, "wake-loss-1", {"applied"})
        # Exactly one drop; the retry produced exactly one applied effect —
        # the business write count proves no double application.
        assert DropFirstCommandEffect.dropped == 1
        assert len(_write_calls(env.calls)) == 1
        assert len(_read_calls(env.calls)) == 1


async def test_sc05_expired_wake_command_rejected_without_business_effect(
    wake_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """A command that expires before host delivery is rejected with a receipt."""

    async with _wake_env(wake_stack, tmp_path, wheel_dist, step6_runner_database_url) as env:
        delivery_id = await _queue_wake_delivery(wake_stack)
        waiting = await _wait_local_waiting(wake_stack, env.runner, delivery_id, step6_runner_database_url)
        token = waiting["wait_token"]
        assert len(_write_calls(env.calls)) == 1

        # Freeze delivery: stop the control plane, record a short-lived
        # command, let it pass its deadline, then restart and let the host
        # pull the already-expired command.
        env.server.should_exit = True
        await env.server_task
        task_id = await _platform_task_id(wake_stack["session_factory"], delivery_id)
        await _issue_wake(
            wake_stack,
            task_id,
            command_id="wake-expired-1",
            wait_token=token,
            expires_at=datetime.now(UTC) + timedelta(seconds=1),
        )
        await asyncio.sleep(2.0)
        env.server, env.server_task, _base = await serve_over_tcp(
            wake_stack["app"], port=int(env.base_url.rsplit(":", 1)[-1])
        )

        record = await _wait_command_state(wake_stack, "wake-expired-1", {"expired", "rejected"})
        assert record.state.value in ("expired", "rejected")
        # No wake happened: zero post-wake reads, the pre-wait write stays
        # at one, and the local run is still waiting on its own facts.
        assert len(_read_calls(env.calls)) == 0
        assert len(_write_calls(env.calls)) == 1
        own = _host_task_state(env.runner, delivery_id, step6_runner_database_url)
        assert own is not None and own.lifecycle_state.value == "waiting_approval"
