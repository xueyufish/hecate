"""Tests for ToolRegistry rest-source routing and gateway management API."""

from __future__ import annotations

import uuid

import httpx
import pytest

from hecate.core.config import settings
from hecate.models.gateway_target import (
    GatewayTargetCreateSchema,
    GatewayTargetKind,
)
from hecate.models.tool import ToolModel
from hecate.tools.gateway.executor import RestToolExecutor
from hecate.tools.gateway.targets import GatewayTargetService
from hecate.tools.tool.builtin import BuiltInToolExecutor
from hecate.tools.tool.registry import ToolRegistry


def _x_gateway() -> dict:
    return {
        "method": "GET",
        "path": "/ping",
        "path_params": [],
        "query_params": [],
        "body_params": [],
    }


def _ok_client() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestRegistryRestBranch:
    async def test_rest_tool_executes(self, db_session) -> None:
        ws = uuid.UUID(int=0)
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                workspace_id=ws,
            )
        )
        db_session.add(
            ToolModel(
                workspace_id=ws,
                name="crm__ping",
                description="GET /ping",
                source="rest",
                parameters={"type": "object", "properties": {}, "x-gateway": _x_gateway()},
                target_id=target.id,
            )
        )
        await db_session.flush()

        registry = ToolRegistry(
            db=db_session,
            builtin_executor=BuiltInToolExecutor(search_provider=None, workspace_root="~/workspaces"),
            rest_executor=RestToolExecutor(client=_ok_client()),
        )
        result = await registry.execute("crm__ping", {})
        assert result == {"ok": True}

    async def test_rest_tool_without_executor_raises(self, db_session) -> None:
        ws = uuid.UUID(int=0)
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                workspace_id=ws,
            )
        )
        db_session.add(
            ToolModel(
                workspace_id=ws,
                name="crm__ping",
                description="GET /ping",
                source="rest",
                parameters={"type": "object", "properties": {}, "x-gateway": _x_gateway()},
                target_id=target.id,
            )
        )
        await db_session.flush()

        registry = ToolRegistry(
            db=db_session,
            builtin_executor=BuiltInToolExecutor(search_provider=None, workspace_root="~/workspaces"),
        )
        with pytest.raises(RuntimeError, match="REST executor"):
            await registry.execute("crm__ping", {})

    async def test_rest_tool_with_inactive_target_raises(self, db_session) -> None:
        ws = uuid.UUID(int=0)
        service = GatewayTargetService(db_session)
        target = await service.create_target(
            GatewayTargetCreateSchema(
                name="crm",
                kind=GatewayTargetKind.REST,
                base_url="https://api.example.com",
                workspace_id=ws,
            )
        )
        target.is_active = False
        await db_session.flush()
        db_session.add(
            ToolModel(
                workspace_id=ws,
                name="crm__ping",
                description="GET /ping",
                source="rest",
                parameters={"type": "object", "properties": {}, "x-gateway": _x_gateway()},
                target_id=target.id,
            )
        )
        await db_session.flush()

        registry = ToolRegistry(
            db=db_session,
            builtin_executor=BuiltInToolExecutor(search_provider=None, workspace_root="~/workspaces"),
        )
        with pytest.raises(ValueError, match="target unavailable"):
            await registry.execute("crm__ping", {})


class TestGatewayAPI:
    async def test_disabled_gateway_returns_404(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "GATEWAY_ENABLED", False)
        response = await client.get("/api/gateway/targets")
        assert response.status_code == 404

    async def test_create_and_list_target_redacted(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "GATEWAY_ENABLED", True)
        payload = {
            "name": "crm",
            "kind": "rest",
            "base_url": "https://api.example.com",
            "spec": {
                "openapi": "3.0.0",
                "paths": {
                    "/ping": {"get": {"operationId": "ping"}},
                    "/contacts/{cid}": {
                        "get": {
                            "operationId": "getContact",
                            "parameters": [
                                {"name": "cid", "in": "path", "required": True, "schema": {"type": "string"}}
                            ],
                        }
                    },
                },
            },
            "credentials": {"headers": {"Authorization": "Bearer top-secret"}},
        }
        created = await client.post("/api/gateway/targets", json=payload)
        assert created.status_code == 201, created.text
        body = created.json()
        assert sorted(body["import"]["imported"]) == ["crm__getContact", "crm__ping"]
        assert body["import"]["warnings"] == []
        assert body["target"]["credentials"] == {"headers": {"Authorization": "***"}}
        assert "top-secret" not in created.text

        listing = await client.get("/api/gateway/targets")
        assert listing.status_code == 200
        assert "top-secret" not in listing.text
        assert listing.json()[0]["name"] == "crm"

    async def test_insecure_target_rejected_via_api(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "GATEWAY_ENABLED", True)
        response = await client.post(
            "/api/gateway/targets",
            json={"name": "bad", "kind": "rest", "base_url": "http://api.example.com"},
        )
        assert response.status_code == 422
        assert "HTTPS" in response.json()["detail"]

    async def test_deactivate_target(self, client, monkeypatch) -> None:
        monkeypatch.setattr(settings, "GATEWAY_ENABLED", True)
        created = await client.post(
            "/api/gateway/targets",
            json={"name": "tmp", "kind": "mcp", "base_url": "https://mcp.example.com"},
        )
        target_id = created.json()["target"]["id"]
        deleted = await client.delete(f"/api/gateway/targets/{target_id}")
        assert deleted.status_code == 200
        gone = await client.get(f"/api/gateway/targets/{target_id}")
        assert gone.status_code == 404
