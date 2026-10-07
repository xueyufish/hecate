"""Console entry point: ``hecate-runner --profile <dir> [--business-api <url>]``.

Loads the profile (fail-fast on any violation), assembles the engine and
evidence store, serves HTTP until an authorized shutdown arrives, then
reports in-flight state honestly. With the durable profile configured, the
host additionally opens the persistent task/action ledger and, before
serving, reconciles non-terminal tasks from previous processes: replayable
work is re-driven through the ledger-gated graph while claimed writes stop
safely into ``reconciliation_required``. With ``control_plane`` configured,
the host joins the managed closed loop: the channel registers and pulls
persisted deliveries into the receive queue (idempotent accept), a serial
scheduler drives accepted tasks through the same engine slot, execution
events and terminal results upload back to the platform, and shutdown
drains honestly (no new pulls, in-flight convergence, best-effort final
upload).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .durable import DurableRuntime
from .engine import EvidenceUnavailableError, ExecutionEngine
from .evidence import EvidenceStore
from .managed import (
    LeaseGate,
    ManagedChannel,
    ManagedExecutionScheduler,
    ManagedIdentity,
    apply_managed_command,
    managed_principal_for,
)
from .profile import Profile, ProfileError, load_profile, resolve_secret_ref
from .server import RunnerServer
from .tools import BusinessApiToolDispatcher

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hecate-runner", description="Standalone Hecate execution host (preview)")
    parser.add_argument("--profile", required=True, type=Path, help="profile directory (manifest, config, identity)")
    parser.add_argument(
        "--business-api",
        default=None,
        help="base URL of the business App's HTTP API (default: value from runner.json 'business_api_base')",
    )
    args = parser.parse_args(argv)

    try:
        profile: Profile = load_profile(args.profile)
    except ProfileError as exc:
        print(f"hecate-runner: profile error: {exc}", file=sys.stderr)
        return 2

    # Managed assembly (step6a): control_plane + durable is the closed loop
    # (channel + persistent receive queue + serial scheduling + upload +
    # shutdown drain). control_plane without durable cannot persist the
    # receive queue and is refused; the standalone path below is unchanged.
    managed: tuple[ManagedChannel, ManagedExecutionScheduler] | None = None
    managed_identity: ManagedIdentity | None = None
    control_plane = profile.config.control_plane
    if control_plane is not None:
        if profile.config.durable is None:
            print(
                "hecate-runner: control_plane requires the durable profile "
                "(durable.database_url) — the managed receive queue is the persistent task ledger",
                file=sys.stderr,
            )
            return 2
        managed_identity = ManagedIdentity(
            principal=managed_principal_for(control_plane.trust_root), domains=control_plane.data_domains
        )

    business_api = args.business_api
    if business_api is None:
        import json

        raw = json.loads((profile.directory / "runner.json").read_text(encoding="utf-8"))
        business_api = raw.get("business_api_base")
    if not business_api:
        print(
            "hecate-runner: no business API configured (use --business-api or runner.json 'business_api_base')",
            file=sys.stderr,
        )
        return 2

    durable: DurableRuntime | None = None
    if profile.config.durable is not None:
        from hecate_durable.storage import SqlDurableStore

        store = SqlDurableStore(profile.config.durable.database_url)
        store.create_schema()
        durable = DurableRuntime(store, workspace=profile.config.durable.workspace)

    # Evidence store: the durable backend delegates audit retention to the
    # host's own durable database (shared engine); profile validation has
    # already refused that backend without a durable profile (fail-fast).
    evidence_cfg = profile.config.evidence
    evidence_dir = (
        evidence_cfg.dir_override
        if evidence_cfg is not None and evidence_cfg.dir_override
        else profile.config.evidence_dir
    )
    policy = None
    if evidence_cfg is not None:
        from .evidence import EvidencePolicy

        policy = EvidencePolicy(retention_days=evidence_cfg.retention_days, capacity_limit=evidence_cfg.capacity_limit)
    if evidence_cfg is not None and evidence_cfg.backend == "durable":
        if durable is None:  # unreachable: profile validation refuses this combination
            raise ProfileError("evidence.backend 'durable' requires the durable profile")
        from .evidence import evidence_backend

        evidence = evidence_backend("durable")(durable.store.engine, policy=policy)
        evidence.create_schema()
    else:
        evidence = EvidenceStore(evidence_dir, policy=policy)

    dispatcher = BusinessApiToolDispatcher(business_api)

    async def dispatch(tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        # principal/domains travel with the run from the server-verified
        # identity (engine.submit); request-body claims never reach here.
        return await dispatcher(tool_name, arguments, principal, domains)

    # The lease gate is created here and shared by the channel (pulls
    # update it) and the engine (protected dispatches check it) — one
    # authorization state for the whole managed assembly.
    channel: ManagedChannel | None = None
    lease_gate: LeaseGate | None = None
    if profile.config.control_plane is not None and durable is not None:
        secret = resolve_secret_ref(control_plane.secret_ref, profile.directory).encode("utf-8")
        lease_gate = LeaseGate(
            secret,
            deployment_domain=control_plane.trust_root,
            issuer_domain=control_plane.issuer_domain,
            host_id=control_plane.host_id,
            workspace_id=control_plane.workspace_id,
        )
        channel = ManagedChannel(
            base_url=control_plane.base_url,
            workspace_id=control_plane.workspace_id,
            trust_root=control_plane.trust_root,
            host_id=control_plane.host_id,
            secret=secret,
            issuer_domain=control_plane.issuer_domain,
            store=durable.store,
            lease_ttl_seconds=control_plane.lease_ttl_seconds,
            poll_interval_seconds=control_plane.poll_interval_seconds,
            upload_batch=control_plane.upload_batch,
            data_domains=control_plane.data_domains,
            lease_gate=lease_gate,
            execution_definition=profile.execution_definition_digest(business_api),
        )

    engine = ExecutionEngine(
        profile,
        evidence,
        dispatch,
        durable=durable,
        managed_identity=managed_identity,
        lease_gate=lease_gate,
        dispatch_binding=business_api,
    )

    if channel is not None:
        if durable is None:  # guarded by the managed profile validation above
            raise RuntimeError("managed command processing requires the durable runner")

        async def handle_managed_command(item: dict) -> dict | None:
            return await apply_managed_command(item, durable=durable, engine=engine)

        channel.set_command_handler(handle_managed_command)
        managed = (
            channel,
            ManagedExecutionScheduler(
                store=durable.store,
                engine=engine,
                associate_attempt=channel.associate_attempt,
            ),
        )
    server = RunnerServer(profile, engine, evidence, durable=durable)

    if durable is not None:
        _schedule_pending_resumes(durable, engine, server)
    if managed is not None:
        _start_managed(managed[0], managed[1], engine, server)

    httpd = server.serve()
    actual_port = httpd.server_address[1]
    # Optional port disclosure for harnesses that start the runner with an
    # OS-assigned port (profile port 0); written after bind succeeds.
    port_file = os.environ.get("RUNNER_PORT_FILE")
    if port_file:
        Path(port_file).write_text(str(actual_port), encoding="utf-8")
    print(
        f"hecate-runner: serving on {profile.config.host}:{actual_port} "
        f"(backend={profile.manifest.backend_type}, model={profile.config.model_backend}, "
        f"tools={list(profile.config.tool_allowlist)}, durable={durable is not None}, "
        f"managed={managed is not None})",
        flush=True,
    )
    try:
        server.wait_shutdown_sync()
    except KeyboardInterrupt:
        server.request_shutdown()
        server.wait_shutdown_sync()
    finally:
        httpd.server_close()
        if managed is not None:
            # Stop pulling/scheduling, converge in-flight runs honestly,
            # then one best-effort final upload so the platform projection
            # reflects the host's last known facts; a failed upload backfills
            # on the next start.
            server._run_coro(_managed_drain(managed[0], managed[1], engine))
        server.close()
        if durable is not None:
            durable.store.dispose()
    print("hecate-runner: shutdown complete", flush=True)
    return 0


def _start_managed(
    channel: ManagedChannel,
    scheduler: ManagedExecutionScheduler,
    engine: ExecutionEngine,
    server: RunnerServer,
) -> None:
    """Boot the managed loops on the runner loop (reconnect-tolerant).

    Registration retries until the platform answers — an unreachable or
    not-yet-enrolled host keeps serving locally, and once registration and
    the credential exchange succeed the pull/upload and scheduling loops
    start. Local evidence retention and the local HTTP surface are
    unaffected by the retrying (per the managed overlay contract).
    """

    import asyncio

    async def _boot() -> None:
        try:
            while not engine.closing:
                if await channel.register():
                    break
                await asyncio.sleep(channel.poll_interval)
            if not engine.closing:
                channel.start()
                scheduler.start()
        except Exception:  # noqa: BLE001 — a boot failure must not kill the loop thread
            logger.exception("managed boot failed; the host continues standalone")

    asyncio.run_coroutine_threadsafe(_boot(), server._loop)


async def _managed_drain(
    channel: ManagedChannel,
    scheduler: ManagedExecutionScheduler,
    engine: ExecutionEngine,
) -> None:
    """Graceful managed shutdown, on the runner loop."""

    await scheduler.stop()
    await channel.stop()
    await engine.close()
    try:
        await channel.upload_events()
    except Exception:  # noqa: BLE001 — shutdown must not hang on upload
        logger.warning("final managed event upload failed; events backfill on next start", exc_info=True)


def _schedule_pending_resumes(durable: DurableRuntime, engine: ExecutionEngine, server: RunnerServer) -> None:
    """Re-drive non-terminal tasks from previous processes (replay path).

    ``reconciliation_required`` tasks stay put — converging them is an
    explicit operator action, never a startup side effect. Managed-issuer
    tasks are skipped here: they belong to the managed scheduler (step6a),
    which recovers them as the same Task/Run when the channel loop is
    configured; without that configuration they are left untouched rather
    than mis-reconciled by the standalone identity path. The serial host
    resumes one task at a time; the loop drains the backlog as the slot
    frees, then exits. Per-action safety comes from the ledger gate inside
    normal dispatch: succeeded actions backfill their recorded result,
    claimed writes stop into ``reconciliation_required``.
    """

    import asyncio

    from hecate_durable.contracts.durable import TaskLifecycleState

    from .managed import MANAGED_ISSUER

    async def _resume_loop() -> None:
        unresumable: set[str] = set()
        while not engine.closing:
            pending = [
                record
                for record, _input in durable.pending_tasks()
                if record.lifecycle_state in {TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING}
                and record.task_ref.issuer_domain != MANAGED_ISSUER
                and record.task_ref.id not in unresumable
            ]
            if not pending:
                return
            resumed = False
            for record in pending:
                run_ref = durable.store.run_for_task(record.task_ref)
                if run_ref is None:
                    unresumable.add(record.task_ref.id)
                    continue
                run_input = durable.store.get_task_input(record.task_ref)
                try:
                    run_id = engine.resume(record.task_ref, run_ref, dict(run_input or {}))
                except (OSError, EvidenceUnavailableError):
                    logger.warning("startup reconcile: evidence unavailable; task retained", exc_info=True)
                    run_id = None
                if run_id is not None:
                    logger.info("startup reconcile: resumed task %s", record.task_ref.id)
                    resumed = True
                    break
            if not resumed and len(unresumable) >= len(pending):
                return
            # Slot busy or drained for now: the in-flight run releases the
            # slot through normal completion; retry shortly.
            await asyncio.sleep(1.0)

    import asyncio as asyncio_mod

    asyncio_mod.run_coroutine_threadsafe(_resume_loop(), server._loop)


if __name__ == "__main__":
    raise SystemExit(main())
