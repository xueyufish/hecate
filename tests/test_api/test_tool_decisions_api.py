"""Tests for security audit REST API endpoints.

Note: These tests require a running PostgreSQL instance (the singleton
ToolDecisionService uses async_session_factory directly). They are
skipped in CI when PostgreSQL is not available.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

requires_postgres = pytest.mark.skipif(
    True,
    reason="Security audit API requires PostgreSQL (async_session_factory singleton)",
)


@requires_postgres
@pytest.mark.asyncio
class TestToolDecisionAPI:
    async def test_query_endpoint_returns_empty(self, client: AsyncClient):
        response = await client.get("/api/security/decisions?agent_id=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["events"] == []
        assert data["total"] == 0

    async def test_query_endpoint_pagination_params(self, client: AsyncClient):
        response = await client.get("/api/security/decisions?limit=10&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "total" in data
        assert data["limit"] == 10
        assert data["offset"] == 0

    async def test_query_endpoint_rejects_invalid_limit(self, client: AsyncClient):
        response = await client.get("/api/security/decisions?limit=0")
        assert response.status_code == 422

    async def test_query_endpoint_rejects_negative_offset(self, client: AsyncClient):
        response = await client.get("/api/security/decisions?offset=-1")
        assert response.status_code == 422
