"""Test-only HTTP transport for the TypeScript pilot backend.

Wraps the pilot's HTTP/JSON binding into the same ``AgentExecutionBackend``
method surface as ``StubExecutionBackend`` so the existing contract
assertions run unchanged against a real out-of-process implementation.

This module is deliberately test-only (design D4): it lives under ``tests/``,
never under ``src/hecate/``. It is a verification asset for the 0.x draft -
the production HTTP client is a later-step concern (step5d/step8), when this
transport has been proven against both the contract suite and the pilot.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from hecate.contracts.execution.capabilities import BackendCapabilities
from hecate.contracts.execution.errors import BackendErrorCode
from hecate.contracts.execution.references import BackendRef
from hecate.contracts.execution.request import ExecutionRequest
from hecate.execution.backend import (
    AgentExecutionBackend,
    CancelReceipt,
    EventPage,
    RunStatus,
    SubmitReceipt,
    backend_error,
)

CALLBACK_AUDIENCE = "pilot-backend"
_BINDING_TYPE_PREFIX = "https://hecate.dev/contracts/binding/"


class LiveTransportError(RuntimeError):
    """Binding-level problem (no contract error code) or transport failure."""


def _token() -> str:
    """Unsigned claim-set token; the pilot verifies envelope + audience only."""

    def b64(segment: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(segment).encode()).decode().rstrip("=")

    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64(
        {
            "iss": "hecate-platform",
            "aud": CALLBACK_AUDIENCE,
            "sub": "workload-1",
            "tenant": "tenant-1",
            "exp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600)),
        }
    )
    return f"{header}.{payload}.sig"


class LiveHttpBackend(AgentExecutionBackend):
    """Speak HTTP to the running pilot; same surface, same assertions."""

    def __init__(self, base_url: str) -> None:
        self._base = base_url.rstrip("/")
        self._token = _token()

    # -- transport ---------------------------------------------------------

    def _wire(self, run_ref: BackendRef) -> str:
        return urllib.parse.quote(f"{run_ref.issuer_domain}/{run_ref.id}", safe="")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body).encode()
        # Loopback pilot only: base_url is 127.0.0.1 + ephemeral port from the
        # session fixture; no other scheme or host is ever constructed here.
        request = urllib.request.Request(  # noqa: S310
            f"{self._base}{path}",
            data=payload,
            method=method,
            headers={
                "authorization": f"Bearer {self._token}",
                **({"content-type": "application/json"} if payload else {}),
                **(headers or {}),
            },
        )
        try:
            # Loopback pilot only: base_url is built from 127.0.0.1 + an
            # ephemeral port in the session fixture; no other scheme is used.
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            parsed = json.loads(raw) if raw else {}
            code = parsed.get("code")
            if code is None:
                raise LiveTransportError(f"binding-level problem from {method} {path}: {parsed}") from None
            if "request_ref" not in parsed:
                # errors.schema.json requires request_ref on every contract
                # error; a contract-coded problem without it is a schema
                # violation, not something this transport can map.
                raise LiveTransportError(
                    f"contract problem without request_ref from {method} {path}: {parsed}"
                ) from None
            raise backend_error(
                BackendErrorCode(code),
                BackendRef.from_dict(parsed["request_ref"]),
                parsed["message"],
                detail_ns=parsed.get("detail_ns") or {},
                reconciliation=parsed.get("reconciliation"),
            ) from None

    # -- AgentExecutionBackend surface -------------------------------------

    def describe_capabilities(self) -> BackendCapabilities:
        return BackendCapabilities.from_dict(self._request("GET", "/capabilities"))

    def submit(self, request: ExecutionRequest) -> SubmitReceipt:
        receipt = self._request(
            "POST",
            "/runs",
            body=request.to_dict(),
            headers={"Idempotency-Key": request.idempotency_key},
        )
        return SubmitReceipt.from_dict(receipt)

    def get_run(self, run_ref: BackendRef) -> RunStatus:
        return RunStatus.from_dict(self._request("GET", f"/runs/{self._wire(run_ref)}"))

    def read_events(self, run_ref: BackendRef, cursor: str | None = None) -> EventPage:
        suffix = f"?cursor={urllib.parse.quote(cursor)}" if cursor is not None else ""
        page = self._request("GET", f"/runs/{self._wire(run_ref)}/events{suffix}")
        return EventPage.from_dict(page)

    def list_artifacts(self, run_ref: BackendRef) -> tuple[BackendRef, ...]:
        listing = self._request("GET", f"/runs/{self._wire(run_ref)}/artifacts")
        return tuple(BackendRef.from_dict(ref) for ref in listing["artifacts"])

    def request_cancel(self, run_ref: BackendRef) -> CancelReceipt:
        receipt = self._request("POST", f"/runs/{self._wire(run_ref)}/cancel")
        return CancelReceipt.from_dict(receipt)

    # -- pilot-only vendor extension ---------------------------------------

    def advance(self, run_ref: BackendRef) -> RunStatus:
        """Checkpoint the run (pilot vendor namespace; not part of the ABC)."""

        status = self._request("POST", f"/pilot-ns/runs/{self._wire(run_ref)}/advance")
        return RunStatus.from_dict(status)
