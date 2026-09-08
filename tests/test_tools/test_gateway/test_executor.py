"""Tests for REST tool execution via httpx MockTransport."""

from __future__ import annotations

import json

import httpx
import pytest

from hecate.models.gateway_target import GatewayTargetKind, GatewayTargetModel
from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import GatewayError, RestExecutionError
from hecate.tools.gateway.executor import RestToolExecutor


def _make_tool(name: str = "crm__getContact") -> ToolModel:
    return ToolModel(
        workspace_id=ToolModel.__table__.c.workspace_id.default.arg,
        name=name,
        description="GET /contacts/{contact_id}",
        source="rest",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "string"},
                "expand": {"type": "boolean"},
            },
            "required": ["contact_id"],
            "x-gateway": {
                "method": "GET",
                "path": "/contacts/{contact_id}",
                "path_params": ["contact_id"],
                "query_params": ["expand"],
                "body_params": [],
            },
        },
        target_id=None,
    )


def _make_post_tool() -> ToolModel:
    return ToolModel(
        workspace_id=ToolModel.__table__.c.workspace_id.default.arg,
        name="crm__createContact",
        description="POST /contacts",
        source="rest",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
            "required": ["name"],
            "x-gateway": {
                "method": "POST",
                "path": "/contacts",
                "path_params": [],
                "query_params": [],
                "body_params": ["name", "email"],
            },
        },
        target_id=None,
    )


def _make_target() -> GatewayTargetModel:
    return GatewayTargetModel(
        name="crm",
        kind=GatewayTargetKind.REST,
        base_url="https://api.example.com",
        credentials={"headers": {"Authorization": "Bearer super-secret"}},
    )


class RecordingHandler:
    """httpx MockTransport handler that records requests and replies."""

    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.payload = payload if payload is not None else {"ok": True}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(
            self.status_code,
            json=self.payload,
            headers={"content-type": "application/json"},
        )


class TestExecution:
    async def test_get_translates_path_and_query(self) -> None:
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = RestToolExecutor(client=client)
        result = await executor.execute(_make_tool(), _make_target(), {"contact_id": "42", "expand": True})
        assert result == {"ok": True}
        request = handler.requests[0]
        assert request.method == "GET"
        assert str(request.url) == "https://api.example.com/contacts/42?expand=true"

    async def test_post_translates_json_body(self) -> None:
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = RestToolExecutor(client=client)
        await executor.execute(_make_post_tool(), _make_target(), {"name": "Ada", "email": "ada@example.com"})
        request = handler.requests[0]
        assert request.method == "POST"
        assert json.loads(request.content) == {"name": "Ada", "email": "ada@example.com"}

    async def test_credentials_injected_into_request(self) -> None:
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = RestToolExecutor(client=client)
        await executor.execute(_make_tool(), _make_target(), {"contact_id": "1"})
        assert handler.requests[0].headers["authorization"] == "Bearer super-secret"

    async def test_credentials_never_in_errors(self) -> None:
        handler = RecordingHandler(status_code=500)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = RestToolExecutor(client=client)
        with pytest.raises(RestExecutionError) as exc_info:
            await executor.execute(_make_tool(), _make_target(), {"contact_id": "1"})
        assert "super-secret" not in str(exc_info.value)
        assert exc_info.value.failure_class == "upstream_error"
        assert exc_info.value.status_code == 500

    async def test_missing_path_parameter_rejected(self) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(RecordingHandler()))
        executor = RestToolExecutor(client=client)
        with pytest.raises(GatewayError, match="contact_id"):
            await executor.execute(_make_tool(), _make_target(), {})

    async def test_tool_without_projection_rejected(self) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(RecordingHandler()))
        executor = RestToolExecutor(client=client)
        tool = _make_tool()
        tool.parameters = {"type": "object", "properties": {}}
        with pytest.raises(GatewayError, match="no gateway projection"):
            await executor.execute(tool, _make_target(), {})

    async def test_connection_failure_is_structured(self) -> None:
        def raising_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        client = httpx.AsyncClient(transport=httpx.MockTransport(raising_handler))
        executor = RestToolExecutor(client=client)
        with pytest.raises(RestExecutionError, match="connection_failure"):
            await executor.execute(_make_tool(), _make_target(), {"contact_id": "1"})

    async def test_base_url_pinned_to_target(self) -> None:
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = RestToolExecutor(client=client)
        target = _make_target()
        target.spec = {
            "openapi": "3.0.0",
            "servers": [{"url": "https://evil.example.com"}],
            "paths": {},
        }
        await executor.execute(_make_tool(), target, {"contact_id": "1"})
        assert str(handler.requests[0].url).startswith("https://api.example.com/")
