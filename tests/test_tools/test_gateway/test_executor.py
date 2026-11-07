"""Tests for REST tool execution via httpx MockTransport."""

from __future__ import annotations

import asyncio
import json
import traceback

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


class TestRequestTargetBoundary:
    """Path parameters are data: the request target cannot be re-routed."""

    @pytest.mark.parametrize(
        "value",
        [
            "../admin?scope=other#fragment",
            "..%2Fadmin",
            "%2e%2e/admin",
            "1/2",
            "/absolute",
            "a?x=1",
            "a#f",
            "a%23f",
            r"..\admin",
            ".",
            "..",
            "%252e%252e",
            "%25252fadmin",
            "\x00",
        ],
    )
    async def test_path_escape_is_rejected_before_sending(self, value: str) -> None:
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        executor = RestToolExecutor(client=client)
        with pytest.raises(GatewayError, match="single path segment"):
            await executor.execute(_make_tool(), _make_target(), {"contact_id": value, "expand": "x"})
        assert handler.requests == []

    @pytest.mark.parametrize("value", ["张三", "Ada Lovelace", "release..v1", "50%", "{other}"])
    async def test_ordinary_segment_data_is_encoded(self, value: str) -> None:
        handler = RecordingHandler()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await RestToolExecutor(client=client).execute(_make_tool(), _make_target(), {"contact_id": value})
        assert handler.requests[0].url.path == f"/contacts/{value}"
        assert not handler.requests[0].url.query

    @pytest.mark.parametrize(
        "path", ["/../../admin", "/%252e%252e/admin", "/contacts?account=other", "/contacts#other"]
    )
    async def test_unsafe_persisted_projection_is_rejected(self, path: str) -> None:
        tool = _make_tool()
        tool.parameters["x-gateway"]["path"] = path
        handler = RecordingHandler()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GatewayError):
                await RestToolExecutor(client=client).execute(tool, _make_target(), {"contact_id": "42"})
        assert handler.requests == []

    @pytest.mark.parametrize(
        "metadata",
        [
            {"method": None},
            {"method": "CONNECT"},
            {"path": []},
            {"path_params": "contact_id"},
            {"path_params": ["other"]},
            {"body_params": False},
            {"path": "/contacts/{contact_id}/{missing}"},
        ],
    )
    async def test_malformed_persisted_metadata_is_a_gateway_error(self, metadata: dict) -> None:
        tool = _make_tool()
        tool.parameters["x-gateway"].update(metadata)
        handler = RecordingHandler()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GatewayError):
                await RestToolExecutor(client=client).execute(tool, _make_target(), {"contact_id": "42"})
        assert handler.requests == []

    @pytest.mark.parametrize(
        "base",
        [
            "https://user:synthetic@api.example.com/v2",
            "https://api.example.com/v2?token=synthetic",
            "https://api.example.com/v2#part",
            "https://api.example.com/v2/../admin",
            "http://127.attacker.example.com",
            "https://api.example.com:bad",
        ],
    )
    async def test_unsafe_persisted_target_is_rejected(self, base: str) -> None:
        target = _make_target()
        target.base_url = base
        handler = RecordingHandler()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(GatewayError, match="unusable base_url") as error:
                await RestToolExecutor(client=client).execute(_make_tool(), target, {"contact_id": "42"})
        assert "synthetic" not in str(error.value)
        assert handler.requests == []

    async def test_base_path_prefix_is_preserved(self) -> None:
        target = _make_target()
        target.base_url = "https://api.example.com/v2"
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await RestToolExecutor(client=client).execute(_make_tool(), target, {"contact_id": "c1", "expand": "x"})
        assert handler.requests[0].url.path == "/v2/contacts/c1"
        assert handler.requests[0].url.params["expand"] == "x"

    @pytest.mark.parametrize("prefix", ["/tenant%2Fid", "/my%20app"])
    async def test_registered_base_path_encoding_is_preserved(self, prefix: str) -> None:
        target = _make_target()
        target.base_url = "https://api.example.com" + prefix
        handler = RecordingHandler()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await RestToolExecutor(client=client).execute(_make_tool(), target, {"contact_id": "42"})
        assert handler.requests[0].url.raw_path == prefix.encode("ascii") + b"/contacts/42"


class TestRedirectContainment:
    """Credentials must never reach a second target via a redirect."""

    async def test_upstream_redirect_is_a_structured_failure(self) -> None:
        class _Redirect:
            requests: list[httpx.Request] = []

            def __call__(self, request: httpx.Request) -> httpx.Response:
                type(self).requests.append(request)
                return httpx.Response(302, headers={"Location": "https://other.example.com/x"})

        handler = _Redirect()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with pytest.raises(RestExecutionError) as exc_info:
            await RestToolExecutor(client=client).execute(_make_tool(), _make_target(), {"contact_id": "c1"})
        assert exc_info.value.failure_class == "redirect_not_followed"
        assert exc_info.value.status_code == 302
        assert len(handler.requests) == 1  # the second target saw nothing

    async def test_caller_redirect_defaults_are_overridden_per_request(self) -> None:
        requests = []

        def redirect(request):
            requests.append(request)
            return httpx.Response(302, headers={"location": "https://other.example.com/x"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
            with pytest.raises(RestExecutionError) as error:
                await RestToolExecutor(client=client).execute(_make_tool(), _make_target(), {"contact_id": "c1"})
            assert error.value.failure_class == "redirect_not_followed"
            assert client.follow_redirects is True and not client.is_closed
        assert len(requests) == 1


class TestCredentialIdentityBoundary:
    """Reserved identity names come only from the verified envelope."""

    async def test_credential_headers_cannot_forge_identity_case_insensitively(self) -> None:
        target = _make_target()
        target.credentials = {
            "headers": {
                "X-HeCate-Actor": "forged",
                "authorization": "Bearer super-secret",
                "X-Gateway-Target": "crm",
            }
        }
        handler = RecordingHandler()
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await RestToolExecutor(client=client).execute(
            _make_tool(),
            target,
            {"contact_id": "c1"},
            identity_headers={"x-hecate-actor": "verified-actor"},
        )
        sent = handler.requests[0].headers
        assert sent["x-hecate-actor"] == "verified-actor"
        assert sent["authorization"] == "Bearer super-secret"
        assert sent["x-gateway-target"] == "crm"

    async def test_client_defaults_cannot_supply_identity_or_a_different_host(self) -> None:
        handler = RecordingHandler()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"X-HeCate-Actor": "forged", "X-HeCate-On-Behalf-Of": "forged-user", "Host": "other.example"},
        ) as client:
            await RestToolExecutor(client=client).execute(_make_tool(), _make_target(), {"contact_id": "42"})
            assert client.headers["x-hecate-actor"] == "forged"
        sent = handler.requests[0].headers
        assert "x-hecate-actor" not in sent and "x-hecate-on-behalf-of" not in sent
        assert sent["host"] == "api.example.com"


class TestClientLifecycle:
    """Owned clients close after success, failure and cancellation."""

    @pytest.mark.parametrize("outcome", ["success", "upstream_error", "connection_failure", "cancel"])
    async def test_owned_client_closes_on_every_exit(self, monkeypatch, outcome) -> None:
        entered = asyncio.Event()
        built = []
        real_init = httpx.AsyncClient.__init__

        async def handler(request):
            if outcome == "cancel":
                entered.set()
                await asyncio.Event().wait()
            if outcome == "connection_failure":
                raise httpx.ReadError("synthetic transport failure", request=request)
            return httpx.Response(500 if outcome == "upstream_error" else 200, json={"ok": True})

        def initialize(client, **kwargs):
            real_init(client, transport=httpx.MockTransport(handler), **kwargs)
            built.append(client)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", initialize)
        execute = RestToolExecutor().execute(_make_tool(), _make_target(), {"contact_id": "42"})
        if outcome == "cancel":
            task = asyncio.create_task(execute)
            await asyncio.wait_for(entered.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "success":
            assert await execute == {"ok": True}
        else:
            with pytest.raises(RestExecutionError) as error:
                await execute
            assert error.value.failure_class == outcome
        assert len(built) == 1 and built[0].is_closed

    async def test_shared_client_identity_is_isolated_per_request(self) -> None:
        seen = {}

        async def handler(request):
            await asyncio.sleep(0)
            seen[request.url.path] = request.headers.get("x-hecate-actor")
            return httpx.Response(200, json={"ok": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"x-hecate-actor": "client-default"},
        ) as client:
            executor = RestToolExecutor(client=client)
            await asyncio.gather(
                executor.execute(_make_tool(), _make_target(), {"contact_id": "a"}, {"x-hecate-actor": "actor-a"}),
                executor.execute(_make_tool(), _make_target(), {"contact_id": "b"}, {"x-hecate-actor": "actor-b"}),
            )
            assert not client.is_closed
            assert client.headers["x-hecate-actor"] == "client-default"
        assert seen == {"/contacts/a": "actor-a", "/contacts/b": "actor-b"}

    async def test_transport_diagnostics_redact_credential_material(self, caplog) -> None:
        def fail(request):
            raise httpx.ReadError("upstream echoed super-secret", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            with pytest.raises(RestExecutionError) as error:
                await RestToolExecutor(client=client).execute(_make_tool(), _make_target(), {"contact_id": "42"})
        assert error.value.failure_class == "connection_failure"
        assert "super-secret" not in str(error.value)
        assert "super-secret" not in caplog.text
        assert "super-secret" not in "".join(traceback.format_exception(error.value))
