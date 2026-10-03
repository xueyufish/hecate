"""Console entry point: ``hecate-runner --profile <dir> [--business-api <url>]``.

Loads the profile (fail-fast on any violation), assembles the engine and
evidence store, serves HTTP until an authorized shutdown arrives, then
reports in-flight state honestly — the preview has no durable recovery,
so a shutdown with a running run reports exactly that.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .engine import ExecutionEngine
from .evidence import EvidenceStore
from .profile import Profile, ProfileError, load_profile
from .server import RunnerServer
from .tools import BusinessApiToolDispatcher


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

    engine = ExecutionEngine(profile, evidence, dispatch)
    server = RunnerServer(profile, engine, evidence)
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
        f"tools={list(profile.config.tool_allowlist)})",
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
    print("hecate-runner: shutdown complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
