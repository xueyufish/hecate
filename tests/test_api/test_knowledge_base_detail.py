"""Tests for the single knowledge-base detail endpoint (GET /knowledge-bases/{kb_id}).

Covers:
- Existing KB returns its record
- Non-existent KB returns 404 with the error envelope
- Soft-deleted KB returns 404
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient


async def _create_kb(client: AsyncClient, name: str = "detail-kb") -> dict:
    resp = await client.post(
        "/api/knowledge-bases",
        json={"name": name, "description": "kb detail test"},
    )
    assert resp.status_code == 201
    return resp.json()


async def test_get_knowledge_base_returns_record(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-detail-hit")

    resp = await client.get(f"/api/knowledge-bases/{kb['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == kb["id"]
    assert body["name"] == "kb-detail-hit"


async def test_get_knowledge_base_missing_returns_404(client: AsyncClient) -> None:
    resp = await client.get(f"/api/knowledge-bases/{uuid.uuid4()}")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "NOT_FOUND"


async def test_get_knowledge_base_deleted_returns_404(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-detail-deleted")
    resp = await client.delete(f"/api/knowledge-bases/{kb['id']}")
    assert resp.status_code == 204

    resp = await client.get(f"/api/knowledge-bases/{kb['id']}")
    assert resp.status_code == 404


async def test_upload_document_ingests_text(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-upload-hit")

    resp = await client.post(
        f"/api/knowledge-bases/{kb['id']}/upload",
        files={"file": ("notes.txt", "Hecate 是企业级 Agent 平台。".encode(), "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "notes.txt"
    assert body["parsing_status"] == "completed"
    assert body["chunk_count"] >= 1

    # The KB list must reflect the document count.
    listed = await client.get("/api/knowledge-bases")
    entry = next(k for k in listed.json()["items"] if k["id"] == kb["id"])
    assert entry["document_count"] == 1

    docs = await client.get(f"/api/knowledge-bases/{kb['id']}/documents")
    items = docs.json()["items"]
    assert any(d["filename"] == "notes.txt" for d in items)


async def test_upload_honors_kb_chunk_config(client: AsyncClient) -> None:
    """A KB-configured small chunk size must change the chunking output."""
    body = "Hecate 是企业级多租户 Agent 平台。" * 40  # ~700 chars
    resp = await client.post(
        "/api/knowledge-bases",
        json={"name": "kb-tiny-chunks", "chunk_strategy": "fixed", "chunk_size": 128, "chunk_overlap": 0},
    )
    assert resp.status_code == 201
    kb = resp.json()

    resp = await client.post(
        f"/api/knowledge-bases/{kb['id']}/upload",
        files={"file": ("doc.txt", body.encode(), "text/plain")},
    )
    assert resp.status_code == 200
    # 700 chars at 128/0 must produce many more chunks than the 1000-char
    # default would — proving the per-KB config reached the chunker.
    assert resp.json()["chunk_count"] >= 4


async def test_upload_document_unsupported_format_415(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-upload-bad")

    resp = await client.post(
        f"/api/knowledge-bases/{kb['id']}/upload",
        files={"file": ("image.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert resp.status_code == 415
    detail = resp.json()["detail"]
    assert detail["error"]["code"] == "UNSUPPORTED_FORMAT"


async def test_delete_document_removes_from_listing(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-delete-doc")
    up = await client.post(
        f"/api/knowledge-bases/{kb['id']}/upload",
        files={"file": ("to-delete.txt", b"temp content", "text/plain")},
    )
    assert up.status_code == 200
    doc_id = up.json()["id"]

    resp = await client.delete(f"/api/knowledge-bases/{kb['id']}/documents/{doc_id}")
    assert resp.status_code == 204

    docs = await client.get(f"/api/knowledge-bases/{kb['id']}/documents")
    assert all(d["id"] != doc_id for d in docs.json()["items"])

    # Second delete finds nothing — soft-deleted documents are invisible.
    resp = await client.delete(f"/api/knowledge-bases/{kb['id']}/documents/{doc_id}")
    assert resp.status_code == 404


async def test_delete_document_missing_returns_404(client: AsyncClient) -> None:
    kb = await _create_kb(client, "kb-delete-missing")
    resp = await client.delete(f"/api/knowledge-bases/{kb['id']}/documents/{uuid.uuid4()}")
    assert resp.status_code == 404
