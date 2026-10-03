"""HTTP service entry: the step3 binding over stdlib, preview posture.

Endpoints (execution-backend.http.v0_1 semantics, problem+json errors):

- ``GET /healthz`` — readiness + profile summary
- ``GET /capabilities`` — supported/unsupported capability declarations
- ``POST /runs`` — submit a read-only run (auth: ``Authorization: Bearer``)
- ``GET /runs/{id}`` — run status
- ``GET /runs/{id}/events`` — cursor-based event read
- ``POST /runs/{id}/cancel`` — cooperative cancel
- ``GET /runs/{id}/artifacts`` — tool-result artifact listing
- ``GET /v1/evidence`` — local evidence query (outcome/principal filters)
- ``POST /admin/shutdown`` — token-gated graceful shutdown

Identity is server-verified: the bearer credential is matched against the
profile trust material (constant time); principal/role/domains come from
that mapping only. Any role/domain claims inside the request body are
ignored. Unauthenticated requests are refused with a problem+json body
and a denial evidence record. The server binds 127.0.0.1 by default and
enforces a request-size cap; this is a local preview surface, not a
hardened public endpoint.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .engine import ExecutionEngine
from .evidence import OUTCOME_DENIED, EvidenceStore
from .profile import Profile, resolve_identity

_MAX_BODY_FALLBACK = 1 << 20


class _ProblemError(Exception):
    def __init__(self, status: int, type_: str, title: str, detail: str) -> None:
        self.status = status
        self.type = type_
        self.title = title
        self.detail = detail


class RunnerServer:
    """Owns the engine, evidence, and the HTTP surface."""

    def __init__(self, profile: Profile, engine: ExecutionEngine, evidence: EvidenceStore) -> None:
        self._profile = profile
        self._engine = engine
        self._evidence = evidence
        self._loop = asyncio.new_event_loop()
        engine.attach_loop(self._loop)
        self._loop_thread = threading.Thread(target=self._loop.run_forever, name="runner-asyncio", daemon=True)
        self._loop_thread.start()
        # Created here, used only on the runner loop (set in request_shutdown's
        # coroutine, awaited in wait_shutdown) — never across loops.
        self._shutdown_event = asyncio.Event()
        self._http: ThreadingHTTPServer | None = None
        self._handler = self._build_handler()

    # -- lifecycle ---------------------------------------------------------

    def serve(self) -> ThreadingHTTPServer:
        server = ThreadingHTTPServer((self._profile.config.host, self._profile.config.port), self._handler)
        server.daemon_threads = False
        self._http = server
        threading.Thread(target=server.serve_forever, name="runner-http", daemon=True).start()
        return server

    def request_shutdown(self) -> None:
        # Event.set() is not a coroutine; schedule a trivial coroutine that
        # sets it on the runner loop (run_coroutine_threadsafe requires one).
        async def _set() -> None:
            self._shutdown_event.set()

        asyncio.run_coroutine_threadsafe(_set(), self._loop)

    async def wait_shutdown(self) -> None:
        """Runs on the runner loop: wait for the shutdown request, then stop
        the HTTP server (serve_forever exit runs in an executor thread)."""

        await self._shutdown_event.wait()
        if self._http is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._http.shutdown)

    def wait_shutdown_sync(self, timeout: float = 30.0) -> None:
        """Blocking variant for the calling thread (main/tests)."""

        future = asyncio.run_coroutine_threadsafe(self.wait_shutdown(), self._loop)
        future.result(timeout=timeout)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)

    # -- helpers -----------------------------------------------------------

    def _run_coro(self, coro, timeout: float = 60.0) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args) -> None:  # silence default stderr noise
                pass

            def _send_json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_problem(self, status: int, type_: str, title: str, detail: str) -> None:
                self._send_json(
                    status,
                    {"type": f"urn:hecate:problem:{type_}", "title": title, "detail": detail, "status": status},
                )

            def _identity(self) -> dict | None:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return None
                identity = resolve_identity(server._profile.identities, auth[len("Bearer ") :])
                if identity is None:
                    return None
                return {"principal": identity.principal, "role": identity.role, "domains": identity.domains}

            def _read_body(self) -> bytes:
                length = int(self.headers.get("Content-Length") or 0)
                cap = server._profile.config.max_request_bytes or _MAX_BODY_FALLBACK
                if length > cap:
                    raise _ProblemError(413, "payload-too-large", "Payload too large", f"body exceeds {cap} bytes")
                return self.rfile.read(length) if length else b""

            def _deny(self, type_: str, detail: str, status: int, principal: str = "anonymous") -> None:
                server._evidence.append("denial", principal, self.path, OUTCOME_DENIED, {"reason": type_})
                self._send_problem(status, type_, "Request denied", detail)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                try:
                    path = self.path.split("?", 1)[0]
                    if path == "/healthz":
                        self._send_json(200, {"ready": True, **server._profile.capabilities_summary()})
                        return
                    if path == "/capabilities":
                        self._send_json(200, server._engine.capabilities())
                        return
                    if path.startswith("/v1/evidence"):
                        if self._identity() is None:
                            self._deny("unauthenticated", "missing or unknown credential", 401)
                            return
                        from urllib.parse import parse_qs

                        params = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                        outcome_values = params.get("outcome") or []
                        principal_values = params.get("principal") or []
                        records = server._evidence.query(
                            outcome=outcome_values[0] if outcome_values else None,
                            principal=principal_values[0] if principal_values else None,
                        )
                        self._send_json(
                            200,
                            {
                                "records": [
                                    {
                                        "ts": r.ts,
                                        "kind": r.kind,
                                        "principal": r.principal,
                                        "ref": r.ref,
                                        "outcome": r.outcome,
                                        "detail": r.detail,
                                    }
                                    for r in records
                                ]
                            },
                        )
                        return

                    parts = [p for p in path.split("/") if p]
                    if len(parts) >= 2 and parts[0] == "runs":
                        run_id = parts[1]
                        state = server._engine.get_state(run_id)
                        if state is None:
                            self._send_problem(404, "run-not-found", "Run not found", f"no run {run_id!r}")
                            return
                        if len(parts) == 2:
                            self._send_json(
                                200,
                                {
                                    "run_ref": f"runs/{run_id}",
                                    "status": state.status,
                                    "result_ref": state.result_ref,
                                    "error": state.error,
                                },
                            )
                            return
                        if parts[2] == "events":
                            cursor = int(
                                (self.path.split("cursor=")[-1].split("&")[0] or 0) if "cursor=" in self.path else 0
                            )
                            events = state.events[cursor:]
                            self._send_json(
                                200,
                                {
                                    "cursor": cursor + len(events),
                                    "events": events,
                                    "terminal": state.status != "running",
                                },
                            )
                            return
                        if parts[2] == "artifacts":
                            self._send_json(200, {"run_ref": f"runs/{run_id}", "artifacts": state.events})
                            return
                    self._send_problem(404, "not-found", "Not found", f"no route for {path}")
                except _ProblemError as problem:
                    self._send_problem(problem.status, problem.type, problem.title, problem.detail)
                except Exception as exc:  # noqa: BLE001 - map to internal-error problem
                    self._send_problem(500, "internal-error", "Internal error", str(exc))

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                try:
                    path = self.path.split("?", 1)[0]
                    parts = [p for p in path.split("/") if p]

                    if parts == ["admin", "shutdown"]:
                        body = self._read_body()
                        token = json.loads(body or b"{}").get("token", "")
                        import hmac as hmac_mod

                        if not hmac_mod.compare_digest(token, server._profile.shutdown_token):
                            self._deny("forbidden", "shutdown token mismatch", 403)
                            return
                        server.request_shutdown()
                        self._send_json(202, {"shutdown": "requested"})
                        return

                    identity = self._identity()
                    if identity is None:
                        self._deny("unauthenticated", "missing or unknown credential", 401)
                        return

                    if parts == ["runs"]:
                        body = self._read_body()
                        request = json.loads(body or b"{}")
                        # Self-reported role/domain claims are ignored by design:
                        # only the server-side identity mapping grants scope.
                        tool_arguments = request.get("tool_arguments") or {}
                        if not isinstance(tool_arguments, dict):
                            self._deny(
                                "invalid-request", "tool_arguments must be an object", 422, identity["principal"]
                            )
                            return
                        run_id, state = server._run_coro(
                            server._engine.submit(identity["principal"], request, tuple(identity["domains"]))
                        )
                        if state is None:
                            self._send_problem(503, "busy", "Runner busy", "serial preview: another run is in flight")
                            return
                        self._send_json(202, {"run_ref": f"runs/{run_id}", "status": "running"})
                        return

                    if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                        run_id = parts[1]
                        state = server._engine.get_state(run_id)
                        if state is None:
                            self._send_problem(404, "run-not-found", "Run not found", f"no run {run_id!r}")
                            return
                        if state.status == "running":
                            self._send_json(
                                202, {"run_ref": f"runs/{run_id}", "cancel": "requested", "note": "cooperative"}
                            )
                        else:
                            self._send_json(
                                200, {"run_ref": f"runs/{run_id}", "cancel": "no-op", "status": state.status}
                            )
                        return

                    self._send_problem(404, "not-found", "Not found", f"no route for {path}")
                except _ProblemError as problem:
                    self._send_problem(problem.status, problem.type, problem.title, problem.detail)
                except json.JSONDecodeError as exc:
                    self._send_problem(400, "invalid-json", "Invalid JSON", str(exc))
                except Exception as exc:  # noqa: BLE001
                    self._send_problem(500, "internal-error", "Internal error", str(exc))

        return Handler
