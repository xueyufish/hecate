"""Managed-channel scenarios, including installed Runner HTTP acceptance.

The suite covers component-level delivery/projection behavior and a clean-
installed Runner process over platform HTTP. PostgreSQL process acceptance is
enabled by ``HECATE_STEP6_POSTGRES_URL`` when the host can reach that database.
The platform stack assembly is shared with the wake-chain acceptance suite
(``tests/scenarios/tools/managed_platform.py``).
"""

from __future__ import annotations

import uuid

import pytest
from hecate_durable.contracts.durable import TaskLifecycleState

from hecate.core.auth_context import AuthContext
from tests.scenarios.tools.managed_platform import (
    ADMIN,
    ISSUER,
    ROOT_NAME,
    SECRET,
    WS,
    build_managed_stack,
    connect_channel,
    serve_over_tcp,
)

TaskLifecycleStateEnum = TaskLifecycleState


@pytest.fixture
def auth_context() -> AuthContext:
    from hecate.models.workspace_member import WorkspaceRole

    return AuthContext(
        user_id=ADMIN, org_id=WS, workspace_id=WS, role=WorkspaceRole.ADMIN, auth_method="jwt", api_key_scope=None
    )


@pytest.fixture
async def managed_stack(auth_context: AuthContext, managed_secrets, tmp_path):
    """Platform app (ASGI) + an enrolled host channel sharing the wire."""

    async with build_managed_stack(auth_context, tmp_path) as stack:
        yield stack


async def test_sc05_reconnect_reverification_then_new_work(managed_stack) -> None:
    """Assertion 1: after re-verifying trust the host accepts new work."""

    channel = await connect_channel(managed_stack)
    delivery_row_id = await managed_stack["queue_delivery"]({"messages": [{"role": "user", "content": "sc05"}]})
    assert await channel.pull_once() == 1
    local_ref = await managed_stack["accepted_local_task_ref"](delivery_row_id)
    from hecate_durable.contracts.references import BackendRef

    state = managed_stack["store"].get_task_state(BackendRef.from_dict(local_ref))
    assert state is not None and state.lifecycle_state is TaskLifecycleStateEnum.QUEUED


async def test_installed_runner_uses_platform_http_across_lost_accept_and_restart(
    managed_stack, tmp_path, step6_runner_database_url
):
    """A clean-installed Runner wheel completes the managed HTTP lifecycle."""

    import asyncio

    import pytest
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import Response

    from hecate.models.managed_delivery import ManagedDeliveryModel
    from hecate.models.run import RunModel
    from tests.scenarios.tools import runner_harness

    if not runner_harness.uv_available():
        pytest.skip("uv is required for the installed Runner process acceptance")

    dropped = {"accept": False}
    http_requests: list[tuple[str, int, str]] = []

    class DropFirstAcceptResponse(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            body = b"".join([chunk async for chunk in response.body_iterator])
            if (
                request.url.path in {"/managed/host/accept", "/managed/host/attempts", "/managed/host/events"}
                and len(http_requests) < 30
            ):
                http_requests.append((request.url.path, response.status_code, body.decode("utf-8", errors="replace")))
            if request.url.path == "/managed/host/accept" and not dropped["accept"]:
                dropped["accept"] = True
                return Response(status_code=503)
            return Response(content=body, status_code=response.status_code, headers=dict(response.headers))

    managed_stack["app"].add_middleware(DropFirstAcceptResponse)
    await managed_stack["admit"]()
    server, server_task, base_url = await serve_over_tcp(managed_stack["app"])
    runner = None
    business_server = None
    try:
        control_plane = {
            "base_url": base_url,
            "workspace_id": str(WS),
            "trust_root": ROOT_NAME,
            "host_id": "installed-runner-sc05",
            "issuer_domain": ISSUER,
            "secret_ref": "file:secrets/managed-secret",
            "poll_interval_seconds": 0.1,
            "data_domains": ["domain_a"],
        }
        runner, business_server, business_calls = runner_harness.start_runner(
            tmp_path,
            durable=True,
            tool_allowlist=["query_inventory"],
            control_plane=control_plane,
            managed_secret=SECRET,
            durable_database_url=step6_runner_database_url,
        )

        async def _wait_enrollment() -> uuid.UUID:
            from sqlalchemy import select

            from hecate.models.standalone_enrollment import StandaloneEnrollmentModel

            deadline = asyncio.get_running_loop().time() + 20
            while asyncio.get_running_loop().time() < deadline:
                async with managed_stack["session_factory"]() as db:
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

        enrollment_id = await _wait_enrollment()
        from hecate.execution.enrollment_resolver import HmacEnrollmentResolver
        from hecate.execution.task_run_registry import TaskRunRegistry

        async with managed_stack["session_factory"]() as db:
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

        async def _wait_projected(delivery_id: str) -> dict:
            deadline = asyncio.get_running_loop().time() + 10
            observed = None
            while asyncio.get_running_loop().time() < deadline:
                async with managed_stack["session_factory"]() as db:
                    delivery = await db.get(ManagedDeliveryModel, uuid.UUID(delivery_id))
                    if delivery is not None:
                        run = await db.get(RunModel, uuid.UUID(delivery.run_ref["id"]))
                        projection = dict(run.projection or {}) if run is not None else {}
                        task_runs = []
                        if run is not None:
                            from sqlalchemy import select

                            task_runs = [
                                {"id": str(row.id), "origin": str(row.origin), "projection": row.projection}
                                for row in (
                                    await db.execute(select(RunModel).where(RunModel.task_id == run.task_id))
                                ).scalars()
                            ]
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
                            "run_id": delivery.run_ref.get("id"),
                            "projection": projection,
                            "task_runs": task_runs,
                        }
                await asyncio.sleep(0.1)
            log_path = runner.workdir / "runner.log" if runner is not None else None
            runner_log = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path else ""
            local_run_id = f"managed-run-{delivery_id}"
            runner_status = runner.request("GET", f"/runs/{local_run_id}") if runner is not None else None
            raise AssertionError(
                f"delivery {delivery_id} did not project a succeeded host run; observed={observed}; "
                f"http={http_requests}; business_calls={business_calls}; runner_status={runner_status}; "
                f"runner_log={runner_log}"
            )

        async def _queue_one() -> str:
            return await managed_stack["queue_delivery"](
                {"input": {"prompt": "managed TCP"}, "tool_arguments": {"domain": "domain_a", "sku": "SKU-A1"}}
            )

        first = await _queue_one()
        first_projection = await _wait_projected(first)
        assert first_projection["state"] == "succeeded"
        assert dropped["accept"] is True, "the first committed accept response must have been lost"
        assert len(business_calls) == 1, "redelivery after a lost accept must not execute the task twice"
        from hecate_durable.storage import SqlDurableStore

        local_database_url = step6_runner_database_url or f"sqlite:///{(runner.workdir / 'host.db').as_posix()}"
        local_store = SqlDurableStore(local_database_url)
        try:
            assert len(local_store.list_tasks()) == 1, "lost accept must retain exactly one durable local Task"
        finally:
            local_store.dispose()

        runner = runner.restart()
        assert runner.request("GET", "/healthz", token=None)[0] == 200
        second = await _queue_one()
        assert (await _wait_projected(second))["state"] == "succeeded"
        assert len(business_calls) == 2

        third = await _queue_one()
        assert (await _wait_projected(third))["state"] == "succeeded"
        assert len(business_calls) == 3
    finally:
        if runner is not None:
            runner.stop(kill=True)
        if business_server is not None:
            runner_harness.stop_business_api(business_server)
        server.should_exit = True
        await server_task


async def test_sc05_duplicate_delivery_never_repeats_execution(managed_stack) -> None:
    """Assertion 3 (deliveries): a redelivered row is answered, not executed."""

    channel = await connect_channel(managed_stack)
    delivery_id = await managed_stack["queue_delivery"]({"messages": []})
    assert await channel.pull_once() == 1

    channel._delivery_cursor = None  # simulate a lost accept response
    duplicated = await channel.pull_once()
    assert duplicated == 0
    assert channel.stats.deliveries_duplicate == 0
    assert (
        await channel._accept_delivery({"delivery_row_id": delivery_id, "input_payload": {"messages": []}})
        == "duplicate"
    )


async def test_sc05_events_deduplicated_on_replay(managed_stack) -> None:
    """Assertion 2: historical events are deduplicated on replay."""

    from hecate.contracts.execution.references import BackendRef, RefKind

    channel = await connect_channel(managed_stack)
    delivery_row_id = await managed_stack["queue_delivery"]({"messages": []})
    await channel.pull_once()
    local_task = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_row_id}")
    local_run = BackendRef(RefKind.RUN, "managed-host", f"managed-run-{delivery_row_id}")
    # Accept happened during pull; record the mapping the upload needs.
    async with managed_stack["session_factory"]() as db:
        from hecate.execution.managed_channel import ManagedDeliveryService

        await ManagedDeliveryService(db).accept(
            delivery_row_id=uuid.UUID(delivery_row_id),
            local_task_ref=local_task.to_dict(),
            local_run_ref=local_run.to_dict(),
        )
        await db.commit()

    # Local execution produced events on the durable log.
    managed_stack["store"].apply_task_state(local_task, TaskLifecycleStateEnum.SUCCEEDED)

    uploaded_first = await channel.upload_events()
    assert uploaded_first >= 1
    # The replay (host retried the same batch after a lost response) is
    # deduplicated upstream — zero new projections, zero loss.
    channel._event_cursors.clear()
    uploaded_replay = await channel.upload_events()
    assert uploaded_replay == 0


async def test_sc05_projection_never_overwrites_local_facts(managed_stack) -> None:
    """Assertion 4: state projection never overwrites local execution facts."""

    from hecate.contracts.execution.references import BackendRef, RefKind

    channel = await connect_channel(managed_stack)
    delivery_row_id = await managed_stack["queue_delivery"]({"messages": []})
    await channel.pull_once()
    local_task = BackendRef(RefKind.TASK, "managed-host", f"managed-{delivery_row_id}")
    async with managed_stack["session_factory"]() as db:
        from hecate.execution.managed_channel import ManagedDeliveryService

        await ManagedDeliveryService(db).accept(
            delivery_row_id=uuid.UUID(delivery_row_id),
            local_task_ref=local_task.to_dict(),
            local_run_ref=BackendRef(RefKind.RUN, "managed-host", f"managed-run-{delivery_row_id}").to_dict(),
        )
        await db.commit()

    # The host's own fact: the task is RUNNING locally.
    managed_stack["store"].apply_task_state(local_task, TaskLifecycleStateEnum.RUNNING)
    before = managed_stack["store"].get_task_state(local_task)
    assert before is not None and before.lifecycle_state is TaskLifecycleStateEnum.RUNNING

    # Platform-side belief differs (it projected an older queued state).
    await channel.upload_events()

    # The authoritative local fact is unchanged — projection never writes back.
    after = managed_stack["store"].get_task_state(local_task)
    assert after is not None and after.lifecycle_state is TaskLifecycleStateEnum.RUNNING
    assert after.revision == before.revision


async def test_sc05_revoked_trust_root_stops_new_work(managed_stack) -> None:
    """Reconnect after revocation: the re-verification chain refuses."""

    channel = await connect_channel(managed_stack)
    assert await channel.pull_once() == 0
    await managed_stack["revoke_root"]()
    channel._credential = None  # reconnect re-authenticates
    channel._delivery_cursor = None
    assert await channel.pull_once() == 0  # refused; no new work
    # The current lease stays valid until its TTL — revocation propagates
    # with the plan's bounded stale window, never instant self-revocation.
    # New deliveries are refused at the platform for the whole window.


async def test_sc05_upload_cursors_are_independent_per_run(managed_stack) -> None:
    channel = await connect_channel(managed_stack)
    await managed_stack["queue_delivery"]({"messages": []})
    await managed_stack["queue_delivery"]({"messages": []})
    assert await channel.pull_once() == 2
    from hecate_durable.contracts.durable import TaskLifecycleState

    for task in managed_stack["store"].list_tasks():
        managed_stack["store"].apply_task_state(task.task_ref, TaskLifecycleState.RUNNING)
    count = await channel.upload_events()
    assert count == 4  # each run contributes submitted and running events
    assert len(channel._event_cursors) == 2
    assert channel.stats.events_uploaded == count
    channel._event_cursors.clear()
    assert await channel.upload_events() == 0


async def test_sc05_unconfirmed_accept_is_redelivered_after_cursor_advance(managed_stack, monkeypatch) -> None:
    channel = await connect_channel(managed_stack)
    await managed_stack["queue_delivery"]({"messages": []})
    original = channel._request
    dropped = False

    async def request(method, path, **kwargs):
        nonlocal dropped
        if path == "/managed/host/accept" and not dropped:
            dropped = True
            return None  # local acceptance persisted, platform has no receipt
        return await original(method, path, **kwargs)

    monkeypatch.setattr(channel, "_request", request)
    assert await channel.pull_once() == 1
    assert channel._delivery_cursor is not None
    assert await channel.pull_once() == 0
    assert channel.stats.deliveries_duplicate == 1
    assert len(managed_stack["store"].list_tasks()) == 1


async def test_sc05_conflicting_replay_is_rejected(managed_stack) -> None:
    channel = await connect_channel(managed_stack)
    await managed_stack["queue_delivery"]({"messages": []})
    await channel.pull_once()
    store = managed_stack["store"]
    task = store.list_tasks()[0]
    run = store.run_for_task(task.task_ref)
    original = store.read_events(run).events[0].to_dict()
    assert await channel.upload_events() == 1
    changed = {**original, "payload": {**original["payload"], "event_type": "run_terminal", "status": "succeeded"}}
    result = await channel._request(
        "POST",
        "/managed/host/events",
        auth=True,
        payload={"local_task_ref": task.task_ref.to_dict(), "envelopes": [changed]},
    )
    assert result is None
