"""Tests for collaboration pattern API endpoints."""

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


def _headers():
    return {"Authorization": "Bearer test-key"}


def test_list_patterns_returns_items():
    """GET /api/collaboration-patterns returns pattern list."""
    client = TestClient(app)
    response = client.get("/api/collaboration-patterns", headers=_headers())

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 6


def test_list_patterns_has_required_fields():
    """Each pattern item has id, name, description, parameters, preview."""
    client = TestClient(app)
    response = client.get("/api/collaboration-patterns", headers=_headers())

    assert response.status_code == 200
    items = response.json()["items"]
    for item in items:
        assert "id" in item
        assert "name" in item
        assert "description" in item
        assert "parameters" in item
        assert "preview" in item
        assert isinstance(item["parameters"], dict)


def test_generate_sequential_pattern():
    """POST /api/collaboration-patterns/sequential/generate returns Graph DSL."""
    client = TestClient(app)
    response = client.post(
        "/api/collaboration-patterns/sequential/generate",
        json={
            "stages": [
                {"id": "s1", "model": "gpt-4o", "system_prompt": "Step 1"},
                {"id": "s2", "model": "gpt-4o", "system_prompt": "Step 2"},
            ],
        },
        headers=_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 2


def test_generate_invalid_pattern_returns_422():
    """POST with unknown pattern returns 422."""
    client = TestClient(app)
    response = client.post(
        "/api/collaboration-patterns/unknown/generate",
        json={},
        headers=_headers(),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "INVALID_PATTERN"


def test_generate_missing_params_returns_422():
    """POST with missing required params returns 422."""
    client = TestClient(app)
    response = client.post(
        "/api/collaboration-patterns/sequential/generate",
        json={},
        headers=_headers(),
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "INVALID_CONFIG"


def test_template_listing_includes_pattern_type():
    """GET /api/orchestration-templates includes pattern_type field."""
    from hecate.api.management import orchestration_templates as mod

    mod._template_cache = None

    client = TestClient(app)
    response = client.get("/api/orchestration-templates", headers=_headers())

    assert response.status_code == 200
    items = response.json()["items"]

    sequential = next((i for i in items if i["id"] == "sequential-pipeline"), None)
    if sequential:
        assert sequential["pattern_type"] == "sequential"

    broadcast = next((i for i in items if i["id"] == "broadcast-pipeline"), None)
    if broadcast:
        assert broadcast["pattern_type"] == "broadcast"

    fan_out = next((i for i in items if i["id"] == "fan-out-pipeline"), None)
    if fan_out:
        assert fan_out["pattern_type"] == "parallel"


def test_new_templates_loadable():
    """Negotiation and debate templates are loadable via API."""
    from hecate.api.management import orchestration_templates as mod

    mod._template_cache = None

    client = TestClient(app)
    response = client.get("/api/orchestration-templates", headers=_headers())

    assert response.status_code == 200
    items = response.json()["items"]
    template_ids = {i["id"] for i in items}

    assert "negotiation" in template_ids
    assert "debate" in template_ids

    # Verify full template loads
    for tid in ("negotiation", "debate"):
        detail_resp = client.get(f"/api/orchestration-templates/{tid}", headers=_headers())
        assert detail_resp.status_code == 200
        data = detail_resp.json()
        assert "nodes" in data
        assert "edges" in data
