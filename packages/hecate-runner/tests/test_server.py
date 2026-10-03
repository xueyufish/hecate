"""HTTP surface, identity, and engine integration tests (tasks 3.1—3.3, 4.1—4.3).

Runs the real RunnerServer in-process against a stub business API and
asserts: endpoint success/error shapes (problem+json), server-side
identity (forged claims ignored, unknown credentials denied with
evidence), argument validation before dispatch, serial-busy semantics,
deterministic stub execution with events and evidence, and the
unsupported capability declarations.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest_runner import write_profile
from hecate_runner.engine import ExecutionEngine
from hecate_runner.evidence import EvidenceStore
from hecate_runner.profile import Profile, TrustedIdentity, load_profile
from hecate_runner.server import RunnerServer
from hecate_runner.tools import BusinessApiToolDispatcher

OTHER_TOKEN = "other-token"


@pytest.fixture()
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Profile:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown-token-value")
    loaded = load_profile(write_profile(tmp_path))
    return replace(
        loaded,
        identities=loaded.identities
        + (
            TrustedIdentity("other-reader", "read_only", ("domain_a",), hashlib.sha256(b"other-token").hexdigest()),
            TrustedIdentity("app-reader", "read_only", ("domain_b",), hashlib.sha256(b"narrow-token").hexdigest()),
        ),
    )


@pytest.fixture()
def business_api():
    """In-process stub of the business App's inventory API."""

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
            principal_domains = request.get("domains", [])
            asked_domain = (request.get("arguments") or {}).get("domain")
            if asked_domain not in principal_domains:
                self._send(403, {"detail": "cross-domain read denied"})
                return
            if asked_domain == "domain_fail":
                self._send(422, {"detail": "business rule rejected"})
                return
            self._send(200, {"result": {"sku": "SKU-A1", "quantity": 100}, "outcome": "ok"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", calls
    server.shutdown()
    server.server_close()


@pytest.fixture()
def running_server(profile: Profile, business_api):
    base_url, _ = business_api
    evidence = EvidenceStore(profile.config.evidence_dir)
    dispatcher = BusinessApiToolDispatcher(base_url)

    async def dispatch(tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        return await dispatcher(tool_name, arguments, principal, domains)

    engine = ExecutionEngine(profile, evidence, dispatch)
    server = RunnerServer(profile, engine, evidence)
    httpd = server.serve()
    port = httpd.server_address[1]

    def caller(
        method: str, path: str, body: dict | None = None, token: str | None = "reader-secret-token"
    ) -> tuple[int, dict]:
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{port}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    caller.engine = engine
    caller.evidence = evidence
    caller.server = server
    yield caller
    server.close()
    httpd.shutdown()
    httpd.server_close()


def _submit_and_wait(caller, arguments: dict, token: str | None = "reader-secret-token") -> tuple[int, dict, dict]:
    status, submit = caller(
        "POST", "/runs", {"input": {"prompt": "check stock"}, "tool_arguments": arguments}, token=token
    )
    if status != 202:
        return status, submit, {}
    caller.engine.wait_for_sync(submit["run_ref"].split("/")[1])
    events = caller("GET", f"/runs/{submit['run_ref'].split('/')[1]}/events")[1]
    return status, submit, events


def test_health_reports_profile(running_server) -> None:
    status, body = running_server("GET", "/healthz", token=None)
    assert status == 200
    assert body["ready"] is True
    assert body["backend_type"] == "pregel"
    assert body["model_source"] == "stub"
    assert body["read_tools"] == ["query_inventory"]


def test_capabilities_declare_unsupported(running_server) -> None:
    status, body = running_server("GET", "/capabilities", token=None)
    assert status == 200
    assert body["submit"] == "enforced"
    assert body["durable_tasks"].startswith("unsupported")
    assert body["write_tools"].startswith("unsupported")
    assert body["event_stream"].startswith("unsupported")


def test_unauthenticated_request_denied_with_evidence(running_server) -> None:
    status, body = running_server("POST", "/runs", {"input": {}}, token=None)
    assert status == 401
    assert body["type"].endswith("unauthenticated")
    records = running_server.evidence.query(outcome="denied", principal="anonymous")
    assert any(r.detail["reason"] == "unauthenticated" for r in records)


def test_forged_role_claim_is_ignored(running_server) -> None:
    """Admin claims in the body must not widen the server-verified scope."""

    status, submit, events = _submit_and_wait(
        running_server,
        {"domain": "domain_b", "sku": "SKU-B1", "role": "admin", "domains": ["domain_a", "domain_b"]},
    )
    assert status == 202
    tool_results = [e for e in events["events"] if e.get("type") == "tool_result"]
    assert tool_results, "expected a tool_result event"
    assert tool_results[0]["outcome"]["status"] == "authorization"


def test_authorized_read_succeeds_with_evidence(running_server) -> None:
    status, submit, events = _submit_and_wait(running_server, {"domain": "domain_a", "sku": "SKU-A1"})
    assert status == 202
    run_id = submit["run_ref"].split("/")[1]
    run = running_server("GET", f"/runs/{run_id}")[1]
    assert run["status"] == "succeeded"
    assert run["result_ref"] == f"runs/{run_id}/artifacts"
    tool_results = [e for e in events["events"] if e.get("type") == "tool_result"]
    assert tool_results[0]["outcome"]["status"] == "ok"
    assert tool_results[0]["outcome"]["result"] == {"sku": "SKU-A1", "quantity": 100}
    records = running_server("GET", "/v1/evidence?outcome=ok")[1]["records"]
    assert any(r["ref"] == run_id and r["detail"]["model_source"] == "stub" for r in records)


def test_only_verified_scope_and_business_parameters_reach_api(running_server, business_api):
    _, calls = business_api
    _submit_and_wait(
        running_server,
        {
            "domain": "domain_a",
            "sku": "SKU-A1",
            "role": "admin",
            "principal": "someone-else",
            "domains": ["domain_b"],
        },
    )
    assert calls[-1] == {
        "principal": "app-reader",
        "domains": ["domain_a"],
        "arguments": {"domain": "domain_a", "sku": "SKU-A1"},
    }


def test_invalid_arguments_rejected_before_dispatch(running_server, business_api) -> None:
    _, calls = business_api
    calls.clear()
    # tool_arguments not an object -> validation failure, no dispatch
    status, body = running_server("POST", "/runs", {"input": {}, "tool_arguments": ["not", "an", "object"]})
    assert status == 422
    assert calls == [], "business API must not be called when arguments are invalid"
    records = running_server("GET", "/v1/evidence?outcome=denied")[1]["records"]
    assert any(r["detail"]["reason"] == "invalid-request" for r in records)


def test_serial_run_refuses_second_submission(running_server) -> None:

    # Occupy the engine lock directly, then submit: expect 503 busy.
    engine = running_server.engine

    running_server.server._run_coro(engine._lock.acquire())
    try:
        status, body = running_server("POST", "/runs", {"tool_arguments": {"domain": "domain_a", "sku": "SKU-A1"}})
    finally:
        engine._loop.call_soon_threadsafe(engine._lock.release)
    assert status == 503
    assert body["type"].endswith("busy")


def test_unknown_run_is_404(running_server) -> None:
    status, body = running_server("GET", "/runs/does-not-exist")
    assert status == 404
    assert body["type"].endswith("run-not-found")


def test_shutdown_requires_token(running_server) -> None:
    status, body = running_server("POST", "/admin/shutdown", {"token": "wrong"})
    assert status == 403
    records = running_server.evidence.query(outcome="denied", principal="anonymous")
    assert any(r.detail["reason"] == "forbidden" for r in records)


@pytest.mark.parametrize("suffix", ["", "/events", "/artifacts", "/cancel"])
@pytest.mark.parametrize("token, expected", [(None, 401), ("other-token", 403), ("narrow-token", 403)])
def test_run_views_and_cancel_are_scoped(running_server, suffix, token, expected) -> None:
    _, submit, _ = _submit_and_wait(running_server, {"domain": "domain_a", "sku": "SKU-A1"})
    method = "POST" if suffix == "/cancel" else "GET"
    status, body = running_server(method, "/" + submit["run_ref"] + suffix, token=token)
    assert status == expected
    assert "events" not in body and "artifacts" not in body


def test_evidence_queries_cannot_widen_principal_scope(running_server) -> None:
    _submit_and_wait(running_server, {"domain": "domain_a", "sku": "SKU-A1"})
    status, body = running_server("GET", "/v1/evidence", token=OTHER_TOKEN)
    assert status == 200
    assert body["records"] == []
    assert running_server("GET", "/v1/evidence?principal=app-reader", token=OTHER_TOKEN)[0] == 403


@pytest.mark.parametrize("arguments", [{}, {"domain": "domain_a"}, {"domain": 1, "sku": "x"}, [], None, False])
def test_schema_validation_precedes_execution(running_server, business_api, arguments) -> None:
    _, calls = business_api
    status, _ = running_server("POST", "/runs", {"tool_arguments": arguments})
    assert status == 422
    assert calls == []
    assert not running_server.engine._runs


@pytest.mark.parametrize("cursor", ["invalid", "-1", "100000"])
def test_bad_event_cursor_is_a_client_error(running_server, cursor) -> None:
    _, submit, _ = _submit_and_wait(running_server, {"domain": "domain_a", "sku": "SKU-A1"})
    status, body = running_server("GET", "/" + submit["run_ref"] + "/events?cursor=" + cursor)
    assert status == 400
    assert body["type"].endswith("invalid-cursor")


def test_shutdown_refuses_new_runs(running_server) -> None:
    running_server.server._run_coro(_begin_shutdown(running_server.engine))
    status, health = running_server("GET", "/healthz", token=None)
    assert status == 200 and not health["ready"]
    assert running_server("POST", "/runs", {"tool_arguments": {"domain": "domain_a", "sku": "x"}})[0] == 503


async def _begin_shutdown(engine) -> None:
    engine.begin_shutdown()
