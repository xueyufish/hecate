"""Console entry point: ``hecate-runner --profile <dir> [--business-api <url>]``.

Loads the profile (fail-fast on any violation), assembles the engine and
evidence store, serves HTTP until an authorized shutdown arrives, then
reports in-flight state honestly. With the durable profile configured, the
host additionally opens the persistent task/action ledger and, before
serving, reconciles non-terminal tasks from previous processes: replayable
work is re-driven through the ledger-gated graph while claimed writes stop
safely into ``reconciliation_required``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from .durable import DurableRuntime
from .engine import ExecutionEngine
from .evidence import EvidenceStore
from .profile import Profile, ProfileError, load_profile
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

    evidence = EvidenceStore(profile.config.evidence_dir)
    dispatcher = BusinessApiToolDispatcher(business_api)

    async def dispatch(tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        # principal/domains travel with the run from the server-verified
        # identity (engine.submit); request-body claims never reach here.
        return await dispatcher(tool_name, arguments, principal, domains)

    durable: DurableRuntime | None = None
    if profile.config.durable is not None:
        from hecate_durable.storage import SqlDurableStore

        store = SqlDurableStore(profile.config.durable.database_url)
        store.create_schema()
        durable = DurableRuntime(store, workspace=profile.config.durable.workspace)

    engine = ExecutionEngine(profile, evidence, dispatch, durable=durable)
    server = RunnerServer(profile, engine, evidence, durable=durable)

    if durable is not None:
        _schedule_pending_resumes(durable, engine, server)

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
        f"tools={list(profile.config.tool_allowlist)}, durable={durable is not None})",
        flush=True,
    )
    try:
        server.wait_shutdown_sync()
    except KeyboardInterrupt:
        server.request_shutdown()
        server.wait_shutdown_sync()
    finally:
        httpd.server_close()
        server.close()
        if durable is not None:
            durable.store.dispose()
    print("hecate-runner: shutdown complete", flush=True)
    return 0


def _schedule_pending_resumes(durable: DurableRuntime, engine: ExecutionEngine, server: RunnerServer) -> None:
    """Re-drive non-terminal tasks from previous processes (replay path).

    ``reconciliation_required`` tasks stay put — converging them is an
    explicit operator action, never a startup side effect. The serial host
    resumes one task at a time; the loop drains the backlog as the slot
    frees, then exits. Per-action safety comes from the ledger gate inside
    normal dispatch: succeeded actions backfill their recorded result,
    claimed writes stop into ``reconciliation_required``.
    """

    import asyncio

    from hecate_durable.contracts.durable import TaskLifecycleState

    async def _resume_loop() -> None:
        unresumable: set[str] = set()
        while not engine.closing:
            pending = [
                record
                for record, _input in durable.pending_tasks()
                if record.lifecycle_state is not TaskLifecycleState.RECONCILIATION_REQUIRED
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
                if engine.resume(record.task_ref, run_ref, dict(run_input or {})) is not None:
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
