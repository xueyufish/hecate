"""SC04 lease-renewal slices: multi-action renewal, bounded refusal, replay record.

Process-level acceptance for the step6b lease-renewal policy over an
installed Runner wheel and real platform HTTP (TCP). One lease authorizes
exactly one protected action, so a run with two protected writes crosses
the renewal boundary: the second dispatch waits (declared budget) for the
channel's next pull to install a fresh lease. Variants:

- happy renewal — both writes execute exactly once, counts verified;
- stopped lease delivery — the platform refuses pulls after the first
  accept, so no fresh lease ever arrives and the second dispatch is
  refused within the declared window (run failed, zero further business
  calls, prior action facts intact, terminal still uploads);
- natural expiry — an issued-but-unconsumed lease dies of age before the
  first protected action: a short platform-issued TTL plus a held
  attempts start (synchronized on the hold, no fixed waits) put the
  dispatch past the recorded expiry — the run refuses on the expired
  lease with zero business calls, the nonce stays unconsumed, the action
  ledger holds no claim, and the refused terminal still projects; after
  issuance and pulls recover, only the new task executes and the refused
  task never re-executes;
- scope violation — a domain outside the lease scope is refused at once
  without waiting and without any business call;
- host restart — the consumed-nonce record persists beside the evidence
  state and normal renewal keeps working across the restart.

Install-time refusal and the structural policy-version/staleness
judgments stay pinned at the gate level (unit tests); legal approval
authority stays with step7.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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
# Two protected writes then a read: the second write crosses the renewal
# boundary (one lease authorizes one protected action).
RENEWAL_MANIFEST_TOOLS = [
    {"name": "submit_inventory_update", "schema_ref": "schemas/write.json", "permission": "write"},
    {"name": "submit_inventory_adjustment", "schema_ref": "schemas/write.json", "permission": "write"},
    {"name": "query_inventory", "schema_ref": "schemas/read.json", "permission": "read"},
]
RENEWAL_TOOL_ALLOWLIST = ["submit_inventory_update", "submit_inventory_adjustment", "query_inventory"]
RENEWAL_TOOL_ARGUMENTS = {
    "submit_inventory_update": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 3},
    "submit_inventory_adjustment": {"domain": "domain_a", "sku": "SKU-A1", "quantity": 7},
    "query_inventory": {"domain": "domain_a", "sku": "SKU-A1"},
}


@pytest.fixture(scope="module")
def wheel_dist(tmp_path_factory) -> Path:
    dist = Path(tmp_path_factory.mktemp("lease-wheels"))
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
async def lease_stack(auth_context, managed_secrets, tmp_path):
    async with build_managed_stack(auth_context, tmp_path) as stack:
        yield stack


def _control_plane(base_url: str, **overrides: Any) -> dict:
    block = {
        "base_url": base_url,
        "workspace_id": str(WS),
        "trust_root": ROOT_NAME,
        "host_id": "installed-runner-lease",
        "issuer_domain": ISSUER,
        "secret_ref": "file:secrets/managed-secret",
        "poll_interval_seconds": 0.05,
        "data_domains": ["domain_a"],
    }
    block.update(overrides)
    return block


def _write_calls(calls: list[dict], quantity: int) -> list[dict]:
    return [c for c in calls if (c.get("arguments") or {}).get("quantity") == quantity]


def _read_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if (c.get("arguments") or {}).get("quantity") is None]


class StopPullsAfterFirstAccept(BaseHTTPMiddleware):
    """Refuse every pull once the runner has accepted its delivery.

    With pulls stopped no fresh lease is ever installed, so the run's
    second protected dispatch exercises the bounded-refusal path while the
    event-upload path (different endpoint) stays alive for the terminal.
    """

    blocked = False

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path == "/managed/host/accept":
            self.blocked = True
        if self.blocked and request.url.path == "/managed/host/pull":
            return Response(status_code=503)
        body = b"".join([chunk async for chunk in response.body_iterator])
        return Response(content=body, status_code=response.status_code, headers=dict(response.headers))


class NaturalExpiryWindow(BaseHTTPMiddleware):
    """Natural-expiry fault window for one delivered managed task.

    The lease riding the delivery pull is the last one: every later pull
    is refused so nothing renews it, and the attempts association response
    is held past the issued TTL, so the run's first protected dispatch
    starts against a naturally expired lease. The event-upload path stays
    alive for the refused terminal.
    """

    lease_of_delivery: dict | None = None
    renewals_stopped = False
    hold_deadline: float | None = None
    hold_seconds = 0.0

    @classmethod
    def arm(cls, hold_seconds: float) -> None:
        cls.lease_of_delivery = None
        cls.renewals_stopped = False
        cls.hold_deadline = None
        cls.hold_seconds = hold_seconds

    @classmethod
    def resume(cls) -> None:
        cls.renewals_stopped = False
        cls.hold_deadline = None

    async def dispatch(self, request, call_next):
        gate = type(self)
        if request.url.path == "/managed/host/pull":
            if gate.renewals_stopped:
                return Response(status_code=503)
            response = await call_next(request)
            if response.status_code == 200:
                body = b"".join([chunk async for chunk in response.body_iterator])
                payload = json.loads(body)
                if payload.get("deliveries") and gate.lease_of_delivery is None:
                    # This pull's lease authorizes the delivered task and
                    # must age out unconsumed: no later pull may renew it.
                    gate.lease_of_delivery = payload["lease"]
                    gate.renewals_stopped = True
                    gate.hold_deadline = time.monotonic() + gate.hold_seconds
                return Response(content=body, status_code=200, headers=dict(response.headers))
            return response
        if request.url.path == "/managed/host/attempts" and gate.hold_deadline is not None:
            # Every attempts call inside the window waits it out: the
            # scheduler must not start the run while the lease is alive.
            remaining = gate.hold_deadline - time.monotonic()
            if remaining > 0:
                await asyncio.sleep(remaining)
            return await call_next(request)
        return await call_next(request)


@asynccontextmanager
async def _lease_env(
    stack,
    tmp_path: Path,
    wheel_dist: Path,
    database_url: str | None,
    *,
    control_plane_overrides: dict[str, Any] | None = None,
    managed_scope: list[str] | None = None,
    middleware_cls: type | None = None,
) -> AsyncGenerator[SimpleNamespace, None]:
    """Admitted platform over TCP + installed runner + opted-in host."""

    await stack["admit"]()
    if middleware_cls is not None:
        stack["app"].add_middleware(middleware_cls)
    server, server_task, base_url = await serve_over_tcp(stack["app"])
    env = SimpleNamespace(runner=None, business_server=None, server=server, server_task=server_task)
    try:
        runner, business_server, calls = runner_harness.start_runner(
            tmp_path,
            manifest_tools=RENEWAL_MANIFEST_TOOLS,
            durable=True,
            tool_allowlist=RENEWAL_TOOL_ALLOWLIST,
            tool_schemas=TOOL_SCHEMAS,
            control_plane=_control_plane(base_url, **(control_plane_overrides or {})),
            managed_secret=SECRET,
            durable_database_url=database_url,
            dist_dir=wheel_dist,
        )
        env.runner = runner
        env.business_server = business_server
        env.calls = calls
        env.base_url = base_url
        enrollment_id = await _wait_enrollment(stack["session_factory"])
        await _opt_in(
            stack["session_factory"],
            enrollment_id,
            managed_scope=managed_scope or ["domain_a"],
        )
        yield env
    finally:
        if env.runner is not None:
            env.runner.stop(kill=True)
        if env.business_server is not None:
            runner_harness.stop_business_api(env.business_server)
        env.server.should_exit = True
        await env.server_task


def _host_database_url(runner, database_url: str | None) -> str:
    return database_url or f"sqlite:///{(runner.workdir / 'host.db').as_posix()}"


def _host_task_state(runner, delivery_id: str, database_url: str | None):
    from hecate_durable.contracts.references import BackendRef, RefKind
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(_host_database_url(runner, database_url))
    try:
        return store.get_task_state(BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_id}"))
    finally:
        store.dispose()


async def _wait_local_terminal(
    stack, runner, delivery_id: str, database_url: str | None, states: set[str], timeout: float = 30.0
) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        record = await asyncio.to_thread(_host_task_state, runner, delivery_id, database_url)
        if record is not None:
            last = record.lifecycle_state.value
            if record.lifecycle_state.value in states:
                return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"task for delivery {delivery_id} never reached {states}; last={last}")


async def _wait_projected_state(stack, delivery_id: str, runner, states: set[str], timeout: float = 30.0) -> str:
    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunModel

    deadline = asyncio.get_running_loop().time() + timeout
    observed: dict | None = None
    while asyncio.get_running_loop().time() < deadline:
        async with stack["session_factory"]() as db:
            delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
            if delivery is not None:
                run = await db.get(RunModel, uuid.UUID(delivery.run_ref["id"]))
                projection = dict(run.projection or {}) if run is not None else {}
                if projection.get("state") in states:
                    return str(projection["state"])
                observed = {"delivery_state": delivery.state, "projection": projection}
        await asyncio.sleep(0.1)
    log_path = runner.workdir / "runner.log"
    runner_log = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path else ""
    raise AssertionError(
        f"delivery {delivery_id} did not project {states}; observed={observed}; runner_log={runner_log}"
    )


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


async def _opt_in(sf, enrollment_id: uuid.UUID, *, managed_scope: list[str]) -> None:
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
            managed_scope=managed_scope,
        )
        await db.commit()


def _nonce_record(runner) -> Path:
    records = list(runner.workdir.rglob("lease-consumed-nonces.jsonl"))
    assert records, "consumed-nonce record not found under the runner workdir"
    return records[0]


def _consumed_nonces(runner) -> set[str]:
    """Nonces persisted as consumed (tolerates a not-yet-created record)."""

    records = list(runner.workdir.rglob("lease-consumed-nonces.jsonl"))
    if not records:
        return set()
    return {json.loads(line)["nonce"] for line in records[0].read_text(encoding="utf-8").splitlines() if line.strip()}


def _fault_run_facts(runner, delivery_id: str, database_url: str | None) -> tuple[list[dict], str | None]:
    """(action ledger rows, local run id) for one delivered task."""

    from hecate_durable.contracts.references import BackendRef, RefKind
    from hecate_durable.storage import SqlDurableStore

    store = SqlDurableStore(_host_database_url(runner, database_url))
    try:
        task_ref = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_id}")
        run_ref = store.run_for_task(task_ref)
        actions = store.list_run_actions(run_ref) if run_ref is not None else []
        return actions, run_ref.id if run_ref is not None else None
    finally:
        store.dispose()


def _expired_lease_denial(runner, run_id: str | None) -> dict | None:
    """The local evidence record attributing a dispatch denial to expiry."""

    if run_id is None:
        return None
    for path in sorted((runner.profile_dir / "evidence").glob("evidence-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            detail = record.get("detail") or {}
            if (
                record.get("kind") == "tool_result"
                and record.get("outcome") == "denied"
                and record.get("ref") == run_id
                and "lease expired" in str(detail.get("reason"))
            ):
                return record
    return None


async def _queue_renewal_delivery(stack, *, domain: str = "domain_a") -> str:
    arguments = {name: {**args, "domain": domain} for name, args in RENEWAL_TOOL_ARGUMENTS.items()}
    return await stack["queue_delivery"]({"input": {"prompt": "lease renewal"}, "tool_arguments": arguments})


async def test_sc04_multi_action_run_renews_lease_per_action(
    lease_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """Two protected writes in one run: each consumes its own fresh lease."""

    async with _lease_env(lease_stack, tmp_path, wheel_dist, step6_runner_database_url) as env:
        delivery_id = await _queue_renewal_delivery(lease_stack)
        terminal = await _wait_local_terminal(
            lease_stack, env.runner, delivery_id, step6_runner_database_url, {"succeeded"}
        )
        assert terminal == "succeeded"
        # Each protected action ran exactly once — one lease per action, no
        # refusal, no redo.
        assert len(_write_calls(env.calls, 3)) == 1
        assert len(_write_calls(env.calls, 7)) == 1
        assert len(_read_calls(env.calls)) == 1

        record = _nonce_record(env.runner)
        nonces = [line for line in record.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(nonces) >= 2, "each protected action must persist its consumed nonce"


async def test_sc04_stopped_lease_delivery_refuses_within_declared_window(
    lease_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """No fresh lease after the first accept → bounded refusal, zero further
    business calls, prior action facts intact, failed terminal uploads."""

    async with _lease_env(
        lease_stack,
        tmp_path,
        wheel_dist,
        step6_runner_database_url,
        control_plane_overrides={"lease_refresh_wait_seconds": 0.6},
        middleware_cls=StopPullsAfterFirstAccept,
    ) as env:
        delivery_id = await _queue_renewal_delivery(lease_stack)
        terminal = await _wait_local_terminal(
            lease_stack, env.runner, delivery_id, step6_runner_database_url, {"failed", "reconciliation_required"}
        )
        # The first write happened; the second was refused inside the
        # declared window and the graph stopped — the read never ran.
        assert len(_write_calls(env.calls, 3)) == 1
        assert len(_write_calls(env.calls, 7)) == 0
        assert len(_read_calls(env.calls)) == 0

        state = await asyncio.to_thread(_host_task_state, env.runner, delivery_id, step6_runner_database_url)
        assert (state.extra or {}).get("last_error") is not None or terminal == "failed"
        projected = await _wait_projected_state(
            lease_stack, delivery_id, env.runner, {"failed", "reconciliation_required"}
        )
        assert projected in {"failed", "reconciliation_required"}


async def test_sc04_naturally_expired_lease_refuses_first_action_then_recovery(
    lease_stack, tmp_path, wheel_dist, step6_runner_database_url, monkeypatch
) -> None:
    """An issued-but-unconsumed lease expires naturally before the first
    protected action: the run refuses on the expired lease with zero
    business calls, the nonce stays unconsumed, the action ledger holds no
    claim, and the central projection reflects the local refusal. After
    issuance and pulls recover, only the new task executes — the refused
    task never re-executes."""

    from hecate.channel.api import managed as managed_api

    original_ttl = managed_api.DEFAULT_LEASE_TTL_SECONDS
    # A 0.6s issuance TTL against a 1.5s execution-start hold: the lease
    # dies of age, not of a refusal race.
    monkeypatch.setattr(managed_api, "DEFAULT_LEASE_TTL_SECONDS", 0.6)
    NaturalExpiryWindow.arm(hold_seconds=1.5)
    async with _lease_env(
        lease_stack,
        tmp_path,
        wheel_dist,
        step6_runner_database_url,
        control_plane_overrides={"lease_refresh_wait_seconds": 0.6},
        middleware_cls=NaturalExpiryWindow,
    ) as env:
        delivery_id = await _queue_renewal_delivery(lease_stack)
        terminal = await _wait_local_terminal(
            lease_stack, env.runner, delivery_id, step6_runner_database_url, {"failed", "reconciliation_required"}
        )

        # Zero business calls: expiry refuses before the first dispatch,
        # so neither write nor read ever reached the boundary.
        assert env.calls == [], "an expired lease must not reach the business API"

        # The delivered task's recorded lease window has passed.
        faulted = NaturalExpiryWindow.lease_of_delivery
        assert faulted is not None and faulted.get("nonce"), "the delivery lease must be captured"
        assert datetime.fromisoformat(faulted["exp"]) <= datetime.now(UTC)

        # The nonce was never consumed: absent from the persistent record.
        assert faulted["nonce"] not in _consumed_nonces(env.runner)

        # The action ledger holds no claim or success fact for the run.
        actions, run_id = await asyncio.to_thread(_fault_run_facts, env.runner, delivery_id, step6_runner_database_url)
        assert actions == [], "a lease-refused action must never claim the ledger"

        # Local evidence attributes the refusal to the expired lease.
        denial = await asyncio.to_thread(_expired_lease_denial, env.runner, run_id)
        assert denial is not None, "the refused dispatch must leave a denial evidence record"

        # The refused terminal projects to the platform over the events
        # path (kept alive during the fault).
        projected = await _wait_projected_state(
            lease_stack, delivery_id, env.runner, {"failed", "reconciliation_required"}
        )
        assert projected in {"failed", "reconciliation_required"}

        # Recovery: normal issuance TTL and pulls again. Only the new task
        # executes, once per expected write.
        monkeypatch.setattr(managed_api, "DEFAULT_LEASE_TTL_SECONDS", original_ttl)
        NaturalExpiryWindow.resume()
        second = await _queue_renewal_delivery(lease_stack)
        assert (
            await _wait_local_terminal(lease_stack, env.runner, second, step6_runner_database_url, {"succeeded"})
            == "succeeded"
        )
        assert len(_write_calls(env.calls, 3)) == 1
        assert len(_write_calls(env.calls, 7)) == 1
        assert len(_read_calls(env.calls)) == 1

        # The refused task was not re-executed by the recovered renewal:
        # exactly the new task's two writes consumed leases, the old
        # nonce is still unconsumed, its ledger still holds no claim, and
        # its terminal state is unchanged.
        consumed = _consumed_nonces(env.runner)
        assert len(consumed) == 2, f"exactly the recovery task's two writes may consume: {consumed}"
        assert faulted["nonce"] not in consumed
        actions_after, _ = await asyncio.to_thread(_fault_run_facts, env.runner, delivery_id, step6_runner_database_url)
        assert actions_after == []
        old_state = await asyncio.to_thread(_host_task_state, env.runner, delivery_id, step6_runner_database_url)
        assert old_state.lifecycle_state.value in {"failed", "reconciliation_required"}
        assert terminal in {"failed", "reconciliation_required"}


async def test_sc04_scope_violation_refuses_without_waiting_or_business_call(
    lease_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """A domain outside the lease scope refuses at once: no renewal wait, no
    business call, no read after it."""

    async with _lease_env(
        lease_stack,
        tmp_path,
        wheel_dist,
        step6_runner_database_url,
        control_plane_overrides={"data_domains": ["domain_a", "domain_b"]},
        managed_scope=["domain_a"],
    ) as env:
        delivery_id = await _queue_renewal_delivery(lease_stack, domain="domain_b")
        await _wait_local_terminal(
            lease_stack, env.runner, delivery_id, step6_runner_database_url, {"failed", "reconciliation_required"}
        )
        assert env.calls == [], "out-of-scope dispatches must not reach the business API"


async def test_sc04_host_restart_keeps_renewal_healthy(
    lease_stack, tmp_path, wheel_dist, step6_runner_database_url
) -> None:
    """The nonce record persists across a restart and fresh leases keep
    arming actions — persistence never blocks legitimate renewal."""

    async with _lease_env(lease_stack, tmp_path, wheel_dist, step6_runner_database_url) as env:
        first = await _queue_renewal_delivery(lease_stack)
        await _wait_local_terminal(lease_stack, env.runner, first, step6_runner_database_url, {"succeeded"})
        record = _nonce_record(env.runner)
        after_first = len([line for line in record.read_text(encoding="utf-8").splitlines() if line.strip()])
        assert after_first >= 2

        env.runner = env.runner.restart(kill_previous=True)
        assert env.runner.request("GET", "/healthz", token=None)[0] == 200

        second = await _queue_renewal_delivery(lease_stack)
        await _wait_local_terminal(lease_stack, env.runner, second, step6_runner_database_url, {"succeeded"})
        after_second = len([line for line in record.read_text(encoding="utf-8").splitlines() if line.strip()])
        assert after_second >= after_first + 2
        assert len(_write_calls(env.calls, 3)) == 2
        assert len(_write_calls(env.calls, 7)) == 2
        assert len(_read_calls(env.calls)) == 2
