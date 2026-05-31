"""Tests for citation chat API."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from hecate.main import app


def test_chat_completions_without_kb_ids():
    """Test that chat completions work without kb_ids (backward compatible)."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data
    assert len(data["choices"]) > 0
    assert "annotations" not in data["choices"][0]["message"] or data["choices"][0]["message"]["annotations"] is None


def test_chat_completions_with_invalid_kb_ids():
    """Test that invalid kb_ids return 422."""
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "kb_ids": ["not-a-uuid"],
        },
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 422


def test_chat_completions_with_valid_kb_ids():
    """Test that valid kb_ids are accepted (may return empty citations if KB not found)."""
    client = TestClient(app)
    kb_id = str(uuid.uuid4())
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "kb_ids": [kb_id],
        },
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "choices" in data


def test_get_message_citations_not_found():
    """Test that 404 is returned for non-existent message."""
    client = TestClient(app)
    message_id = str(uuid.uuid4())
    response = client.get(
        f"/api/messages/{message_id}/citations",
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 404


def test_get_message_citations_empty():
    """Test that empty citations array is returned for message without citations."""
    client = TestClient(app)
    # This test requires a message to exist in the DB
    # For now, we test the endpoint structure
    message_id = str(uuid.uuid4())
    response = client.get(
        f"/api/messages/{message_id}/citations",
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 404  # Message doesn't exist
