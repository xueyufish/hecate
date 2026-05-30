"""Tests for orchestration template API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hecate.core.deps import verify_api_key
from hecate.main import app


@pytest.fixture(autouse=True)
def _override_auth():
    """Override API key auth for all tests in this module."""

    async def _verify() -> str:
        return "test-key"

    app.dependency_overrides[verify_api_key] = _verify
    yield
    app.dependency_overrides.pop(verify_api_key, None)


@pytest.fixture(autouse=True)
def _clear_template_cache():
    """Clear template cache before each test."""
    from hecate.api.management import orchestration_templates as mod

    mod._template_cache = None
    yield
    mod._template_cache = None


def _headers():
    return {"Authorization": "Bearer test-key"}


def test_list_templates():
    """GET /api/orchestration-templates returns template list."""
    client = TestClient(app)
    response = client.get("/api/orchestration-templates", headers=_headers())

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert isinstance(data["items"], list)


def test_list_templates_has_preview():
    """Each template item includes preview with node/edge counts."""
    client = TestClient(app)
    response = client.get("/api/orchestration-templates", headers=_headers())

    assert response.status_code == 200
    items = response.json()["items"]
    if items:
        item = items[0]
        assert "preview" in item
        assert "total_nodes" in item["preview"]
        assert "agent_nodes" in item["preview"]
        assert "total_edges" in item["preview"]


def test_get_template_detail():
    """GET /api/orchestration-templates/{id} returns full Graph DSL."""
    client = TestClient(app)
    list_response = client.get("/api/orchestration-templates", headers=_headers())
    items = list_response.json()["items"]
    if not items:
        pytest.skip("No templates available")

    template_id = items[0]["id"]
    response = client.get(f"/api/orchestration-templates/{template_id}", headers=_headers())

    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "nodes" in data
    assert "edges" in data


def test_get_template_not_found():
    """GET /api/orchestration-templates/{id} returns 404 for missing template."""
    client = TestClient(app)
    response = client.get("/api/orchestration-templates/nonexistent", headers=_headers())

    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "NOT_FOUND"


def test_list_templates_no_auth():
    """GET /api/orchestration-templates returns 401 without API key."""
    clean_app = TestClient(app)
    app.dependency_overrides.pop(verify_api_key, None)
    response = clean_app.get("/api/orchestration-templates")
    assert response.status_code in (401, 403)
