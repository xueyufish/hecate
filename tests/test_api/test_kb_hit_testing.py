"""Tests for knowledge base hit testing API endpoints."""

from __future__ import annotations

import uuid

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def kb_id(client: AsyncClient) -> str:
    """Create a knowledge base and return its ID."""
    resp = await client.post(
        "/api/knowledge-bases",
        json={"name": "test-kb", "description": "test"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_search_knowledge_base(client: AsyncClient, kb_id: str) -> None:
    """Test POST /api/knowledge-bases/{id}/search returns scored results."""
    resp = await client.post(
        f"/api/knowledge-bases/{kb_id}/search",
        json={"query": "machine learning", "mode": "hybrid", "limit": 5},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["query"] == "machine learning"
    assert result["mode"] == "hybrid"
    assert "results" in result
    assert "total" in result


async def test_search_with_score_breakdown(client: AsyncClient, kb_id: str) -> None:
    """Test search results include dense_score and sparse_score fields."""
    resp = await client.post(
        f"/api/knowledge-bases/{kb_id}/search",
        json={"query": "test", "mode": "hybrid"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    if results:
        assert "dense_score" in results[0]
        assert "sparse_score" in results[0]
        assert "score" in results[0]
        assert "content" in results[0]


async def test_search_404_for_missing_kb(client: AsyncClient) -> None:
    """Test search returns 404 for nonexistent knowledge base."""
    fake_id = str(uuid.uuid4())
    resp = await client.post(
        f"/api/knowledge-bases/{fake_id}/search",
        json={"query": "test"},
    )
    assert resp.status_code == 404


async def test_search_422_for_empty_query(client: AsyncClient, kb_id: str) -> None:
    """Test search returns 422 for empty query string."""
    resp = await client.post(
        f"/api/knowledge-bases/{kb_id}/search",
        json={"query": ""},
    )
    assert resp.status_code == 422


async def test_search_default_mode(client: AsyncClient, kb_id: str) -> None:
    """Test search defaults to hybrid mode."""
    resp = await client.post(
        f"/api/knowledge-bases/{kb_id}/search",
        json={"query": "test"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "hybrid"


async def test_list_chunks(client: AsyncClient, kb_id: str) -> None:
    """Test GET /api/knowledge-bases/{id}/chunks returns paginated chunks."""
    resp = await client.get(
        f"/api/knowledge-bases/{kb_id}/chunks",
        params={"page": 1, "page_size": 10},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert "items" in result
    assert "total" in result
    assert isinstance(result["items"], list)
    assert isinstance(result["total"], int)


async def test_list_chunks_404(client: AsyncClient) -> None:
    """Test chunks returns 404 for nonexistent knowledge base."""
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"/api/knowledge-bases/{fake_id}/chunks")
    assert resp.status_code == 404


async def test_compare_modes(client: AsyncClient, kb_id: str) -> None:
    """Test POST /api/knowledge-bases/{id}/compare returns all 3 modes."""
    resp = await client.post(
        f"/api/knowledge-bases/{kb_id}/compare",
        json={"query": "API authentication", "limit": 3},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert "dense" in result
    assert "sparse" in result
    assert "hybrid" in result
    assert result["query"] == "API authentication"


async def test_compare_modes_404(client: AsyncClient) -> None:
    """Test compare returns 404 for nonexistent knowledge base."""
    fake_id = str(uuid.uuid4())
    resp = await client.post(
        f"/api/knowledge-bases/{fake_id}/compare",
        json={"query": "test"},
    )
    assert resp.status_code == 404
