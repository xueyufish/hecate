"""Tests for AuditMiddleware — path exclusion, event creation, queue handling."""

from __future__ import annotations

import asyncio
import uuid

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from hecate.core.auth_context import AuthContext
from hecate.core.middleware.audit import (
    AuditMiddleware,
    _map_path_to_action,
    set_audit_queue,
)
from hecate.ops.audit.store import AuditEvent


async def _dummy_endpoint(request: object) -> JSONResponse:
    return JSONResponse({"ok": True})


class TestMapPathToAction:
    def test_post_creates_action(self) -> None:
        assert _map_path_to_action("/api/agents/123", "POST") == "api.agents.create"

    def test_put_updates_action(self) -> None:
        assert _map_path_to_action("/api/agents/123", "PUT") == "api.agents.update"

    def test_patch_updates_action(self) -> None:
        assert _map_path_to_action("/api/agents/123", "PATCH") == "api.agents.update"

    def test_delete_deletes_action(self) -> None:
        assert _map_path_to_action("/api/agents/123", "DELETE") == "api.agents.delete"

    def test_short_path_falls_through(self) -> None:
        assert _map_path_to_action("/api/agents", "GET") == "api.get"

    def test_non_api_path(self) -> None:
        assert _map_path_to_action("/v1/chat/completions", "POST") == "api.post"


class TestAuditMiddleware:
    def _create_app(self, queue: asyncio.Queue[AuditEvent]) -> Starlette:
        app = Starlette(
            routes=[
                Route("/api/test", _dummy_endpoint),
                Route("/health", _dummy_endpoint),
            ],
        )
        app.add_middleware(AuditMiddleware)
        set_audit_queue(queue)
        return app

    def test_captures_api_request(self) -> None:
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        app = self._create_app(queue)
        client = TestClient(app)

        response = client.get("/api/test")
        assert response.status_code == 200

        event = queue.get_nowait()
        assert event.action == "api.get"
        assert event.request_path == "/api/test"
        assert event.request_method == "GET"
        assert event.response_status == 200
        assert event.success is True

    def test_skips_health_endpoint(self) -> None:
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        app = self._create_app(queue)
        client = TestClient(app)

        response = client.get("/health")
        assert response.status_code == 200
        assert queue.empty()

    def test_skips_options_requests(self) -> None:
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        app = self._create_app(queue)
        client = TestClient(app)

        # OPTIONS to /api/test — Starlette returns 405 since no OPTIONS handler
        # The middleware should still skip it regardless of status code
        response = client.options("/api/test")
        # Starlette returns 405 for OPTIONS without explicit handler
        assert response.status_code == 405
        assert queue.empty()

    def test_no_queue_set_does_not_crash(self) -> None:
        set_audit_queue(None)  # type: ignore[arg-type]
        app = Starlette(routes=[Route("/api/test", _dummy_endpoint)])
        app.add_middleware(AuditMiddleware)
        client = TestClient(app)

        response = client.get("/api/test")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Identity attribution + auth-failure recording (p1-audit-cost-a2a)
# ---------------------------------------------------------------------------


def _jwt_ctx() -> AuthContext:
    return AuthContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-00000000000a"),
        org_id=uuid.UUID("00000000-0000-0000-0000-00000000000b"),
        workspace_id=uuid.UUID("00000000-0000-0000-0000-00000000000c"),
        role=None,
        auth_method="jwt",
        api_key_scope=None,
    )


class TestAuditIdentityAttribution:
    def _app_with_state(self, queue: asyncio.Queue[AuditEvent], state: dict) -> Starlette:
        async def _state_endpoint(request: object) -> JSONResponse:
            for key, value in state.items():
                setattr(request.state, key, value)
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/api/test", _state_endpoint)])
        app.add_middleware(AuditMiddleware)
        set_audit_queue(queue)
        return app

    def test_auth_context_reaches_event_metadata(self) -> None:
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        app = self._app_with_state(queue, {"auth_context": _jwt_ctx()})
        client = TestClient(app)

        client.get("/api/test")

        event = queue.get_nowait()
        assert event.user_id == uuid.UUID("00000000-0000-0000-0000-00000000000a")
        assert event.metadata["auth_method"] == "jwt"

    def test_auth_failure_reaches_event_metadata_without_credential(self) -> None:
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        raw_credential = "hcat-secret-token-value"
        app = self._app_with_state(
            queue,
            {"auth_failure": "invalid_credentials", "seen_credential": raw_credential},
        )
        client = TestClient(app)

        client.get("/api/test")

        event = queue.get_nowait()
        assert event.metadata["auth_failure"] == "invalid_credentials"
        # The event payload never carries the raw credential.
        assert raw_credential not in str(event.metadata)
