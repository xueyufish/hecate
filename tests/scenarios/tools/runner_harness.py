"""Clean-install harness for the SC01/SC02 standalone-consumption scenarios.

Builds the ``hecate-runtime``, ``hecate-durable``, and ``hecate-runner``
wheels, installs them into a fresh uv venv (no repo source path, no
editable install), writes a
profile, starts a stub business API plus the runner as a subprocess from a
temporary working directory, and yields an HTTP client. Everything
environment-specific (path normalization, startup polling, timeouts) is
centralized here so the scenario tests stay declarative.

The harness requires ``uv`` on PATH; without it, tests skip with an
explicit reason. CI guarantees uv and runs these files, so CI is the
authority — a local skip is never a completion claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
READER_TOKEN = "reader-secret-token"
ENTRY_NAME = "agents/summary/main.json"
STARTUP_TIMEOUT_SECONDS = 60.0


def uv_available() -> bool:
    return shutil.which("uv") is not None


def _build_wheels(dist_dir: Path) -> tuple[Path, Path, Path]:
    for package in ("hecate-runtime", "hecate-durable", "hecate-runner"):
        result = subprocess.run(
            ["uv", "build", "--package", package, "--out-dir", str(dist_dir)],
            check=False,
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=300,
        )
        if result.returncode:
            diagnostics = (result.stderr or result.stdout).decode("utf-8", errors="replace")
            raise RuntimeError(f"wheel build failed for {package}: {diagnostics}")
    runtime_wheel = next(dist_dir.glob("hecate_runtime-*.whl"))
    durable_wheel = next(dist_dir.glob("hecate_durable-*.whl"))
    runner_wheel = next(dist_dir.glob("hecate_runner-*.whl"))
    return runtime_wheel, durable_wheel, runner_wheel


def _write_profile(
    profile_dir: Path,
    *,
    business_api_port: int,
    manifest_tools: list[dict] | None = None,
    durable: dict | None = None,
    tool_allowlist: list[str] | None = None,
    tool_schemas: dict[str, dict] | None = None,
    control_plane: dict | None = None,
    managed_secret: bytes | None = None,
) -> None:
    """Materialize a preview (or durable) profile with absolute local paths.

    ``tool_schemas`` maps schema refs to JSON Schemas: each named file is
    written under ``files/`` with its digest recorded in the manifest so
    the runner's startup verification passes.
    """

    profile_dir.mkdir(parents=True, exist_ok=True)
    tools = tool_allowlist or ["query_inventory"]
    entry_name = ENTRY_NAME
    entry_content = json.dumps({"kind": "entry", "tools": tools}).encode()
    (profile_dir / "files" / "agents/summary").mkdir(parents=True, exist_ok=True)
    (profile_dir / "files" / entry_name).write_bytes(entry_content)

    files = [{"path": entry_name, "sha256": hashlib.sha256(entry_content).hexdigest(), "size": len(entry_content)}]
    for ref, schema in (tool_schemas or {}).items():
        schema_bytes = json.dumps(schema).encode()
        schema_path = profile_dir / "files" / ref
        schema_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path.write_bytes(schema_bytes)
        files.append({"path": ref, "sha256": hashlib.sha256(schema_bytes).hexdigest(), "size": len(schema_bytes)})

    manifest: dict = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": entry_name,
        "files": files,
    }
    if manifest_tools is not None:
        manifest["tools"] = manifest_tools
    (profile_dir / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    config: dict = {
        "host": "127.0.0.1",
        "port": 0,
        "evidence_dir": "evidence",
        "business_api_base": f"http://127.0.0.1:{business_api_port}",
        "model": {"backend": "stub"},
        "tool_allowlist": tools,
        "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
    }
    if durable is not None:
        config["durable"] = durable
    if control_plane is not None:
        config["control_plane"] = dict(control_plane)
    (profile_dir / "runner.json").write_text(json.dumps(config), encoding="utf-8")
    (profile_dir / "identity.json").write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "principal": "app-reader",
                        "role": "read_only",
                        "domains": ["domain_a"],
                        "credential": "file:secrets/app-reader-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (profile_dir / "secrets").mkdir(exist_ok=True)
    (profile_dir / "secrets/app-reader-token").write_text(READER_TOKEN, encoding="utf-8")
    if managed_secret is not None:
        (profile_dir / "secrets/managed-secret").write_bytes(managed_secret)


def _start_business_api() -> tuple[ThreadingHTTPServer, list[dict]]:
    """Stub of the business App's inventory API; owns its own rules."""

    calls: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            request = json.loads(self.rfile.read(length) or b"{}")
            calls.append(request)
            domain = (request.get("arguments") or {}).get("domain")
            if domain not in (request.get("domains") or []):
                self._send(403, {"detail": "cross-domain read denied by business API"})
                return
            if domain == "domain_fail":
                self._send(422, {"detail": "business rule rejected"})
                return
            inventory = {
                "domain_a": {"SKU-A1": {"name": "示例商品A1", "quantity": 100}},
                "domain_b": {"SKU-B1": {"name": "示例商品B1", "quantity": 7}},
            }
            record = inventory.get(domain, {}).get((request.get("arguments") or {}).get("sku", ""))
            if record is None:
                self._send(422, {"detail": "unknown sku"})
                return
            self._send(200, {"result": record, "outcome": "ok"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, calls


class RunnerInstance:
    """A runner subprocess with its profile and an HTTP client."""

    def __init__(
        self, workdir: Path, profile_dir: Path, python_exe: Path, process: subprocess.Popen, port: int
    ) -> None:
        self.workdir = workdir
        self.profile_dir = profile_dir
        self.python_exe = python_exe
        self.process = process
        self.port = port

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        token: str | None = READER_TOKEN,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def wait_run(self, run_id: str, timeout: float = 30.0) -> tuple[int, dict]:
        """Poll until the run leaves queued/running (or the timeout expires)."""

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, run = self.request("GET", f"/runs/{run_id}")
            if status != 200:
                return status, run
            if run.get("status") not in {"queued", "running"}:
                return status, run
            time.sleep(0.2)
        return self.request("GET", f"/runs/{run_id}")

    def stop(self, *, kill: bool = False) -> None:
        if self.process.poll() is None:
            if kill:
                self.process.kill()
            else:
                self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)

    def restart(self, *, kill_previous: bool = False) -> RunnerInstance:
        """Hard-stop this process (kill by default: a crash, not a graceful
        shutdown) and start a fresh runner over the SAME profile and durable
        database; returns the new instance."""

        self.stop(kill=kill_previous or True)
        return start_runner_from(
            self.workdir,
            self.profile_dir,
            self.python_exe,
        )


def start_runner(
    tmp_root: Path,
    *,
    manifest_tools: list[dict] | None = None,
    durable: bool = False,
    tool_allowlist: list[str] | None = None,
    tool_schemas: dict[str, dict] | None = None,
    control_plane: dict | None = None,
    managed_secret: bytes | None = None,
    durable_database_url: str | None = None,
) -> tuple[RunnerInstance, ThreadingHTTPServer, list[dict]]:
    """Build, install, profile, launch.

    Returns (runner, business_api_server, business_api_calls); the caller
    stops the runner via ``RunnerInstance.stop()`` and shuts the business
    API down via ``stop_business_api`` (or rely on daemon threads in tests).
    ``durable=True`` additionally wires the persistent task/action ledger
    and checkpoints (a file database inside the workdir) and admits
    write/approval tools.
    """

    if not uv_available():
        raise RuntimeError("uv is required for the clean-install harness")

    workdir = Path(tempfile.mkdtemp(prefix="sc-runner-", dir=tmp_root))
    dist_dir = workdir / "dist"
    venv_dir = workdir / "venv"
    runtime_wheel, durable_wheel, runner_wheel = _build_wheels(dist_dir)

    subprocess.run(["uv", "venv", str(venv_dir), "--seed"], check=True, capture_output=True, timeout=120)
    python_exe = venv_dir / "Scripts" / "python.exe" if os.name == "nt" else venv_dir / "bin" / "python"
    # The runner wheel declares hecate-durable; the local wheel satisfies it
    # here the same way the clean-install CI job provides it (the package is
    # not published to PyPI).
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python_exe),
            str(runtime_wheel),
            str(durable_wheel),
            str(runner_wheel),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    if durable_database_url is not None and durable_database_url.startswith("postgresql"):
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python_exe), "psycopg[binary]>=3.2"],
            check=True,
            capture_output=True,
            timeout=300,
        )

    business_server, business_calls = _start_business_api()
    profile_dir = workdir / "profile"
    durable_config = None
    if durable:
        durable_config = {
            "database_url": durable_database_url or f"sqlite:///{(workdir / 'host.db').as_posix()}",
            "workspace": "sc",
        }
    _write_profile(
        profile_dir,
        business_api_port=business_server.server_address[1],
        manifest_tools=manifest_tools,
        durable=durable_config,
        tool_allowlist=tool_allowlist,
        tool_schemas=tool_schemas,
        control_plane=control_plane,
        managed_secret=managed_secret,
    )

    runner = start_runner_from(workdir, profile_dir, python_exe)
    return runner, business_server, business_calls


def start_runner_from(workdir: Path, profile_dir: Path, python_exe: Path) -> RunnerInstance:
    """Launch one runner subprocess for an existing workdir/profile/venv
    and wait for health (used for restarts over the same durable state)."""

    port_file = workdir / "port.txt"
    if port_file.is_file():
        port_file.unlink()
    log_file = workdir / "runner.log"
    env = {
        **{key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}},
        "RUNNER_SHUTDOWN_TOKEN": "sc-shutdown-token",
        "RUNNER_PORT_FILE": str(port_file),
    }
    log_handle = log_file.open("a", encoding="utf-8")
    process = subprocess.Popen(
        [str(python_exe), "-I", "-m", "hecate_runner", "--profile", str(profile_dir)],
        cwd=str(workdir),  # no repo source path
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_handle.close()
            output = log_file.read_text(encoding="utf-8", errors="replace")
            raise RuntimeError(f"runner exited during startup (code {process.returncode}): {output[-2000:]}")
        if port_file.is_file():
            port = int(port_file.read_text(encoding="utf-8").strip())
            instance = RunnerInstance(workdir, profile_dir, python_exe, process, port)
            status, body = instance.request("GET", "/healthz", token=None)
            if status == 200 and body.get("ready"):
                log_handle.close()
                return instance
        time.sleep(0.3)
    log_handle.close()
    process.kill()
    raise RuntimeError(f"runner did not become healthy in {STARTUP_TIMEOUT_SECONDS}s")


def stop_business_api(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()
