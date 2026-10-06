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
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote

from hecate_durable.contracts.durable import IdempotencyConflictError
from hecate_durable.contracts.references import BackendRef, RefKind

from . import contract
from .durable import DurableRuntime
from .engine import EvidenceUnavailableError, ExecutionEngine, RunState
from .evidence import OUTCOME_DENIED, EvidenceStore
from .profile import Profile, resolve_identity

_MAX_BODY_FALLBACK = 1 << 20
logger = logging.getLogger(__name__)


class _ProblemError(Exception):
    def __init__(self, status: int, type_: str, title: str, detail: str) -> None:
        self.status = status
        self.type = type_
        self.title = title
        self.detail = detail


class RunnerServer:
    """Owns the engine, evidence, and the HTTP surface."""

    def __init__(
        self,
        profile: Profile,
        engine: ExecutionEngine,
        evidence: EvidenceStore,
        durable: DurableRuntime | None = None,
    ) -> None:
        self._profile = profile
        self._engine = engine
        self._evidence = evidence
        self._durable = durable
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
            self._engine.begin_shutdown()
            self._shutdown_event.set()

        asyncio.run_coroutine_threadsafe(_set(), self._loop)

    async def wait_shutdown(self) -> None:
        """Runs on the runner loop: wait for the shutdown request, then stop
        the HTTP server (serve_forever exit runs in an executor thread)."""

        await self._shutdown_event.wait()
        if self._http is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._http.shutdown)

    def wait_shutdown_sync(self, timeout: float | None = None) -> None:
        """Blocking variant for the calling thread (main/tests)."""

        future = asyncio.run_coroutine_threadsafe(self.wait_shutdown(), self._loop)
        future.result(timeout=timeout)

    def _schedule_redrive(self, task_ref) -> None:
        """Re-drive a woken standalone task until the serial slot takes it.

        The wake requeued the task on a new attempt run; the slot may be
        busy, so retry until the engine dispatches, the task moves on, or
        the host closes. Managed-issuer tasks are left to the managed
        scheduler's own continuous loop.
        """

        from .managed import MANAGED_ISSUER

        if task_ref.issuer_domain == MANAGED_ISSUER:
            return

        async def _redrive() -> None:
            attempts = 0
            while not self._engine.closing and attempts < 600:
                record = await asyncio.to_thread(self._durable.store.get_task_state, task_ref)
                if record is None or record.lifecycle_state.value != "queued":
                    return
                run_ref = await asyncio.to_thread(self._durable.store.run_for_task, task_ref)
                if run_ref is None:
                    return
                if self._engine.resume(task_ref, run_ref, {}) is not None:
                    return
                attempts += 1
                await asyncio.sleep(0.2)
            logger.warning("woken task %s could not be re-driven; it stays queued", task_ref.id)

        asyncio.run_coroutine_threadsafe(_redrive(), self._loop)

    def close(self) -> None:
        """Close execution, listener and loop without leaving orphaned tasks."""
        if self._loop.is_closed():
            return
        self._run_coro(self._engine.close())
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)
        self._loop.close()

    # -- helpers -----------------------------------------------------------

    def _run_coro(self, coro, timeout: float = 60.0) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def _build_handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(10.0)

            def log_message(self, fmt: str, *args) -> None:  # silence default stderr noise
                pass

            def _send_json(self, status: int, payload: dict, content_type: str = "application/json") -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("X-Contract-Version", contract.CONTRACT_VERSION)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_problem(self, status: int, type_: str, title: str, detail: str) -> None:
                # Error paths may reject before reading a request body. Closing
                # prevents its bytes being parsed as a subsequent HTTP request.
                self.close_connection = True
                self._send_json(
                    status,
                    {"type": f"urn:hecate:problem:{type_}", "title": title, "detail": detail, "status": status},
                    "application/problem+json",
                )

            def _send_problem_document(
                self,
                status: int,
                type_: str,
                title: str,
                document: dict[str, Any],
            ) -> None:
                self.close_connection = True
                payload = {"type": type_, "title": title, "status": status, **document}
                self._send_json(status, payload, "application/problem+json")

            def _identity(self) -> dict | None:
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return None
                identity = resolve_identity(server._profile.identities, auth[len("Bearer ") :])
                if identity is None:
                    return None
                return {"principal": identity.principal, "role": identity.role, "domains": identity.domains}

            def _read_body(self) -> bytes:
                if self.headers.get("Transfer-Encoding"):
                    raise _ProblemError(400, "invalid-request", "Invalid framing", "chunked bodies are unsupported")
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    raise _ProblemError(400, "invalid-request", "Invalid framing", "invalid Content-Length") from None
                if length < 0:
                    raise _ProblemError(400, "invalid-request", "Invalid framing", "negative Content-Length")
                cap = server._profile.config.max_request_bytes or _MAX_BODY_FALLBACK
                if length > cap:
                    raise _ProblemError(413, "payload-too-large", "Payload too large", f"body exceeds {cap} bytes")
                return self.rfile.read(length) if length else b""

            def _authorized_run(self, run_id: str, identity: dict):
                state = server._engine.get_state(run_id)
                if state is None and server._durable is not None:
                    run_ref = BackendRef(
                        kind=RefKind.RUN,
                        issuer_domain=contract.ISSUER_DOMAIN,
                        id=run_id,
                    )
                    association = server._durable.association_for_run(run_ref)
                    if association is not None:
                        if association.key.subject != identity["principal"]:
                            server._evidence.append(
                                "denial",
                                identity["principal"],
                                run_id,
                                OUTCOME_DENIED,
                                {"reason": "forbidden"},
                            )
                            raise _ProblemError(
                                403,
                                "forbidden",
                                "Request denied",
                                "run is outside the trusted identity scope",
                            )
                        record = server._durable.store.get_task_state(association.task_ref)
                        state = RunState(
                            run_id=run_id,
                            status=record.lifecycle_state.value,
                            principal=association.key.subject,
                            domains=(),
                            task_ref=association.task_ref,
                            run_ref=association.run_ref,
                            received_at=record.recorded_at,
                        )
                if state is None:
                    raise _ProblemError(404, "run-not-found", "Run not found", "run does not resolve")
                if state.principal != identity["principal"] or not set(state.domains).issubset(identity["domains"]):
                    server._evidence.append(
                        "denial", identity["principal"], run_id, OUTCOME_DENIED, {"reason": "forbidden"}
                    )
                    raise _ProblemError(403, "forbidden", "Request denied", "run is outside the trusted identity scope")
                return state

            def _handle_wake(self, action: str, raw_ref: str, identity: dict, body: dict) -> None:
                """Apply one wake command (provide-input/resume) for a waiting run.

                The caller must be the run's recorded principal and present
                the wait's one-time token; the command receipt is idempotent
                by command_id. An applied wake requeues the task on a new
                attempt run, which the server then re-drives through the
                serial slot.
                """

                import asyncio as asyncio_mod

                if server._durable is None:
                    raise _ProblemError(
                        503,
                        "not-durable",
                        "Wake requires the durable profile",
                        "persistent waiting needs the durable task ledger",
                    )
                run_id, issuer_domain = contract.parse_run_path_segment(raw_ref)
                if issuer_domain is not None and issuer_domain != contract.ISSUER_DOMAIN:
                    raise _ProblemError(404, "run-not-found", "Run not found", "unknown run issuer")
                run_ref = BackendRef(kind=RefKind.RUN, issuer_domain=contract.ISSUER_DOMAIN, id=run_id)
                association = server._durable.association_for_run(run_ref)
                task_ref = (
                    association.task_ref if association is not None else server._durable.store.task_for_run(run_ref)
                )
                if task_ref is None:
                    raise _ProblemError(404, "run-not-found", "Run not found", "run does not resolve")
                persisted = server._durable.store.get_task_input(task_ref) or {}
                trusted = persisted.get("_host_identity") or {}
                if trusted.get("principal") != identity["principal"]:
                    server._evidence.append(
                        "denial", identity["principal"], run_id, OUTCOME_DENIED, {"reason": "forbidden"}
                    )
                    raise _ProblemError(403, "forbidden", "Request denied", "run is outside the trusted identity scope")

                command_id = str(body.get("command_id") or "")
                wait_token = str(body.get("wait_token") or "")
                if not command_id or not wait_token:
                    raise _ProblemError(
                        422, "invalid-wake", "Invalid wake command", "command_id and wait_token are required"
                    )
                input_payload = body.get("input")
                if action == "provide-input":
                    if input_payload is not None and not isinstance(input_payload, dict):
                        raise _ProblemError(422, "invalid-wake", "Invalid wake command", "input must be an object")
                else:
                    input_payload = None

                kind = "provide_input" if action == "provide-input" else "resume"

                async def _apply():
                    return await asyncio_mod.to_thread(
                        server._durable.apply_wake,
                        command_id=command_id,
                        kind=kind,
                        issuer=identity["principal"],
                        task_ref=task_ref,
                        wait_token=wait_token,
                        input_payload=input_payload,
                    )

                receipt, reason = server._run_coro(_apply())
                if receipt.state.value == "applied":
                    server._evidence.append(
                        "wake", identity["principal"], run_id, "applied", {"command_id": command_id, "kind": kind}
                    )
                    server._schedule_redrive(task_ref)
                else:
                    server._evidence.append(
                        "wake", identity["principal"], run_id, "rejected", {"command_id": command_id, "reason": reason}
                    )
                payload = {"run_ref": f"runs/{run_id}", "command_id": command_id, "state": receipt.state.value}
                if reason is not None:
                    payload["reason"] = reason
                self._send_json(202, payload)

            def _deny(self, type_: str, detail: str, status: int, principal: str = "anonymous") -> None:
                server._evidence.append("denial", principal, self.path, OUTCOME_DENIED, {"reason": type_})
                self._send_problem(status, type_, "Request denied", detail)

            def do_GET(self) -> None:  # noqa: N802 - http.server API
                try:
                    path = self.path.split("?", 1)[0]
                    if path == "/healthz":
                        self._send_json(
                            200,
                            {
                                "ready": not server._engine.closing,
                                **server._profile.capabilities_summary(),
                                **server._evidence.policy_summary(),
                            },
                        )
                        return
                    if path == "/capabilities":
                        payload = contract.capabilities(
                            server._engine.capabilities(),
                            durable=server._durable is not None,
                        )
                        payload.update(server._engine.capabilities())
                        self._send_json(200, payload)
                        return
                    identity = self._identity()
                    if identity is None:
                        self._deny("unauthenticated", "missing or unknown credential", 401)
                        return
                    if path == "/v1/evidence":
                        params = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                        outcome_values = params.get("outcome") or []
                        principal_values = params.get("principal") or []
                        if principal_values and principal_values[0] != identity["principal"]:
                            self._deny(
                                "forbidden",
                                "evidence is outside the trusted identity scope",
                                403,
                                identity["principal"],
                            )
                            return
                        records = server._evidence.query(
                            outcome=outcome_values[0] if outcome_values else None,
                            principal=identity["principal"],
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
                    if parts and parts[0] == "tasks":
                        if server._durable is None:
                            self._send_problem(404, "not-found", "Not found", "no durable profile on this host")
                            return
                        if len(parts) == 1 and parts[0] == "tasks":
                            records = server._durable.store.list_tasks()
                            items = []
                            for r in records:
                                run_ref = server._durable.store.run_for_task(r.task_ref)
                                items.append(
                                    {
                                        "task_ref": r.task_ref.id,
                                        "state": r.lifecycle_state.value,
                                        "revision": r.revision,
                                        "run_ref": f"runs/{run_ref.id}" if run_ref is not None else None,
                                    }
                                )
                            self._send_json(200, {"tasks": items})
                            return
                        task_id = parts[1]
                        record = server._durable.task_state(task_id)
                        if record is None:
                            raise _ProblemError(404, "task-not-found", "Task not found", "task does not resolve")
                        if len(parts) == 3 and parts[2] == "actions":
                            run_ref = server._durable.store.run_for_task(record.task_ref)
                            actions = server._durable.run_actions(run_ref) if run_ref is not None else []
                            self._send_json(200, {"task_ref": task_id, "actions": actions})
                            return
                        if len(parts) == 3 and parts[2] == "events":
                            run_ref = server._durable.store.run_for_task(record.task_ref)
                            if run_ref is None:
                                self._send_json(200, {"cursor": 0, "events": [], "terminal": True})
                                return
                            params = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                            try:
                                cursor = int((params.get("cursor") or ["0"])[0])
                                if cursor < 0:
                                    raise ValueError
                            except ValueError:
                                raise _ProblemError(
                                    400, "invalid-cursor", "Invalid cursor", "cursor must be a non-negative integer"
                                ) from None
                            page = server._durable.read_events(run_ref, cursor=cursor)
                            self._send_json(
                                200,
                                {
                                    "cursor": page.next_cursor,
                                    "events": [envelope.to_dict() for envelope in page.events],
                                },
                            )
                            return
                        if len(parts) == 2:
                            run_ref = server._durable.store.run_for_task(record.task_ref)
                            self._send_json(
                                200,
                                {
                                    "task_ref": task_id,
                                    "state": record.lifecycle_state.value,
                                    "revision": record.revision,
                                    "run_ref": f"runs/{run_ref.id}" if run_ref is not None else None,
                                },
                            )
                            return
                        self._send_problem(404, "not-found", "Not found", f"no route for {path}")
                        return

                    if len(parts) in (2, 3) and parts[0] == "runs":
                        raw_ref = unquote(parts[1])
                        run_id, issuer_domain = contract.parse_run_path_segment(raw_ref)
                        formal = issuer_domain is not None or self.headers.get("X-Contract-Version") is not None
                        if issuer_domain is not None and issuer_domain != contract.ISSUER_DOMAIN:
                            self._send_problem(404, "run-not-found", "Run not found", "unknown run issuer")
                            return
                        state = self._authorized_run(run_id, identity)
                        if len(parts) == 2:
                            if formal:
                                self._send_json(
                                    200,
                                    contract.run_status(run_id, state, state.run_ref),
                                )
                                return
                            payload = {
                                "run_ref": f"runs/{run_id}",
                                "status": state.status,
                                "result_ref": state.result_ref,
                                "error": state.error,
                            }
                            if state.status in ("waiting_input", "waiting_approval") and server._durable is not None:
                                # step6c authorized wait view: the wake token
                                # is only visible to the run's recorded
                                # principal (identity was verified above).
                                wait = server._durable.wait_of(state.task_ref)
                                if wait is not None:
                                    payload["wait"] = {
                                        "wake_kind": wait.get("wake_kind"),
                                        "contract_ref": wait.get("contract_ref"),
                                        "wait_token": wait.get("wait_token"),
                                        "wait_expires_at": wait.get("wait_expires_at"),
                                    }
                            self._send_json(200, payload)
                            return
                        if parts[2] == "events":
                            params = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                            cursor_values = params.get("cursor")
                            cursor_value = cursor_values[0] if cursor_values else None
                            if formal:
                                durable_page = None
                                if server._durable is not None and state.run_ref is not None:
                                    try:
                                        cursor_int = int(cursor_value) if cursor_value is not None else 0
                                        if cursor_int < 0:
                                            raise ValueError
                                    except ValueError:
                                        raise _ProblemError(
                                            400,
                                            "invalid-cursor",
                                            "Invalid cursor",
                                            "cursor is not a valid continuation",
                                        ) from None
                                    durable_page = server._durable.read_execution_events(
                                        state.run_ref,
                                        cursor=cursor_int,
                                    )
                                try:
                                    self._send_json(
                                        200,
                                        contract.event_page(
                                            state,
                                            cursor=cursor_value,
                                            durable_page=durable_page,
                                        ),
                                    )
                                except contract.ContractValidationError as exc:
                                    raise _ProblemError(
                                        400,
                                        "invalid-cursor",
                                        "Invalid cursor",
                                        str(exc),
                                    ) from exc
                                return
                            if server._durable is not None and state.run_ref is not None:
                                # Durable profile: events come from the
                                # persistent log — cursors survive restarts.
                                params = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                                try:
                                    cursor = int((params.get("cursor") or ["0"])[0])
                                    if cursor < 0:
                                        raise ValueError
                                except ValueError:
                                    raise _ProblemError(
                                        400,
                                        "invalid-cursor",
                                        "Invalid cursor",
                                        "cursor must be a non-negative integer",
                                    ) from None
                                page = server._durable.read_events(state.run_ref, cursor=cursor)
                                self._send_json(
                                    200,
                                    {
                                        "cursor": page.next_cursor,
                                        "events": [envelope.to_dict() for envelope in page.events],
                                        "terminal": state.status != "running",
                                    },
                                )
                                return
                            params = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                            try:
                                cursor = int((params.get("cursor") or ["0"])[0])
                                if cursor < 0 or cursor > len(state.events):
                                    raise ValueError
                            except ValueError:
                                raise _ProblemError(
                                    400, "invalid-cursor", "Invalid cursor", "cursor is outside the event range"
                                ) from None
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
                            if formal:
                                self._send_json(200, contract.artifact_page(run_id, state, state.run_ref))
                                return
                            self._send_json(200, {"run_ref": f"runs/{run_id}", "artifacts": state.events})
                            return
                    if len(parts) == 3 and parts[0] == "runs" and parts[2] in ("provide-input", "resume"):
                        raw = self._read_body()
                        parsed = json.loads(raw.decode("utf-8")) if raw else {}
                        if not isinstance(parsed, dict):
                            raise _ProblemError(400, "invalid-json", "Invalid JSON", "wake body must be an object")
                        self._handle_wake(parts[2], unquote(parts[1]), identity, parsed)
                        return

                    self._send_problem(404, "not-found", "Not found", f"no route for {path}")
                except _ProblemError as problem:
                    self._send_problem(problem.status, problem.type, problem.title, problem.detail)
                except Exception as exc:  # noqa: BLE001 - map to internal-error problem
                    logger.warning("Runner GET failed", exc_info=exc)
                    self._send_problem(
                        500, "internal-error", "Internal error", "request failed; inspect local service logs"
                    )

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                try:
                    path = self.path.split("?", 1)[0]
                    parts = [p for p in path.split("/") if p]

                    if parts == ["admin", "shutdown"]:
                        body = self._read_body()
                        request = json.loads(body or b"{}")
                        if not isinstance(request, dict) or not isinstance(request.get("token", ""), str):
                            raise _ProblemError(
                                400, "invalid-request", "Invalid request", "shutdown token must be a string"
                            )
                        token = request.get("token", "")
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
                        if server._engine.closing:
                            self._send_problem(503, "shutting-down", "Runner closing", "new runs are not accepted")
                            return
                        body = self._read_body()
                        request = json.loads(body or b"{}")
                        formal_request = contract.is_execution_request(request)
                        formal_request_body = dict(request) if formal_request else {}
                        if formal_request:
                            try:
                                request = contract.parse_execution_request(
                                    request,
                                    idempotency_header=self.headers.get("Idempotency-Key"),
                                )
                            except contract.ContractValidationError as exc:
                                self._send_problem(
                                    400,
                                    "invalid-request",
                                    "Invalid execution request",
                                    str(exc),
                                )
                                return
                        # Self-reported role/domain claims are ignored by design:
                        # only the server-side identity mapping grants scope.
                        try:
                            server._engine.validate_run_input(request)
                        except ValueError as exc:
                            self._deny("invalid-request", str(exc), 422, identity["principal"])
                            return
                        idempotency_key = self.headers.get("Idempotency-Key") or None
                        try:
                            run_id, state = server._run_coro(
                                server._engine.submit(
                                    identity["principal"],
                                    request,
                                    tuple(identity["domains"]),
                                    idempotency_key=idempotency_key,
                                )
                            )
                        except IdempotencyConflictError as exc:
                            if formal_request:
                                self._send_problem_document(
                                    409,
                                    "https://hecate.dev/contracts/errors/version_conflict",
                                    "Version conflict",
                                    {
                                        "code": "version_conflict",
                                        "request_ref": formal_request_body["run_ref"],
                                        "message": "idempotency key already registered with different content",
                                        "detail_ns": {
                                            "reason": "idempotency_key_content_mismatch",
                                            "registered_digest": exc.registered_digest,
                                        },
                                    },
                                )
                                return
                            self._send_problem(
                                409,
                                "idempotency-conflict",
                                "Idempotency conflict",
                                f"key already registered with a different request digest ({exc.registered_digest})",
                            )
                            return
                        except EvidenceUnavailableError as exc:
                            self._send_problem(
                                503,
                                "evidence-unavailable",
                                "Local evidence store unwritable",
                                f"new protected runs are refused: {exc}",
                            )
                            return
                        if state is None:
                            self._send_problem(503, "busy", "Runner busy", "serial preview: another run is in flight")
                            return
                        if formal_request:
                            response = contract.submit_receipt(run_id, state, state.run_ref)
                        else:
                            response = {"run_ref": f"runs/{run_id}", "status": state.status}
                            if state.replayed:
                                response["replayed"] = True
                        self._send_json(202, response)
                        return

                    if len(parts) == 3 and parts[0] == "runs" and parts[2] == "cancel":
                        raw_ref = unquote(parts[1])
                        run_id, issuer_domain = contract.parse_run_path_segment(raw_ref)
                        formal = issuer_domain is not None or self.headers.get("X-Contract-Version") is not None
                        if issuer_domain is not None and issuer_domain != contract.ISSUER_DOMAIN:
                            self._send_problem(404, "run-not-found", "Run not found", "unknown run issuer")
                            return
                        state = self._authorized_run(run_id, identity)
                        command_id = None
                        if server._durable is not None and state.task_ref is not None and state.run_ref is not None:
                            import uuid as uuid_mod

                            command_id = f"cancel-{uuid_mod.uuid4()}"
                            server._durable.record_cancel(
                                command_id=command_id,
                                issuer=identity["principal"],
                                task_ref=state.task_ref,
                                run_ref=state.run_ref,
                            )

                        async def cancel() -> bool:
                            return server._engine.request_cancel(run_id, command_id=command_id)

                        if server._run_coro(cancel()):
                            if formal:
                                payload = contract.cancel_receipt(
                                    run_id,
                                    accepted=True,
                                    state=state,
                                    run_ref=state.run_ref,
                                )
                            else:
                                payload = {
                                    "run_ref": f"runs/{run_id}",
                                    "cancel": "requested",
                                    "command_id": command_id,
                                    "note": "cooperative at tool boundaries; applied only on actual effect",
                                }
                            if command_id is not None:
                                payload["command_id"] = command_id
                            self._send_json(202, payload)
                        else:
                            if command_id is not None:
                                server._durable.cancel_rejected(command_id)
                            if formal:
                                payload = contract.cancel_receipt(
                                    run_id,
                                    accepted=False,
                                    state=state,
                                    run_ref=state.run_ref,
                                )
                                if command_id is not None:
                                    payload["command_id"] = command_id
                                self._send_json(202, payload)
                            else:
                                self._send_json(
                                    200, {"run_ref": f"runs/{run_id}", "cancel": "no-op", "status": state.status}
                                )
                        return

                    if len(parts) == 3 and parts[0] == "runs" and parts[2] in ("provide-input", "resume"):
                        raw = self._read_body()
                        parsed = json.loads(raw.decode("utf-8")) if raw else {}
                        if not isinstance(parsed, dict):
                            raise _ProblemError(400, "invalid-json", "Invalid JSON", "wake body must be an object")
                        self._handle_wake(parts[2], unquote(parts[1]), identity, parsed)
                        return

                    self._send_problem(404, "not-found", "Not found", f"no route for {path}")
                except _ProblemError as problem:
                    self._send_problem(problem.status, problem.type, problem.title, problem.detail)
                except json.JSONDecodeError as exc:
                    self._send_problem(400, "invalid-json", "Invalid JSON", str(exc))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Runner POST failed", exc_info=exc)
                    self._send_problem(
                        500, "internal-error", "Internal error", "request failed; inspect local service logs"
                    )

        return Handler
