"""Tests for A2A client methods."""

from __future__ import annotations

from hecate.a2a.client.client import A2AClient


def test_a2a_client_init() -> None:
    """Test A2A client initialization."""
    client = A2AClient(agent_url="https://agent.example.com", api_key="test-key")
    assert client._agent_url == "https://agent.example.com"
    assert client._api_key == "test-key"


def test_a2a_client_headers_with_key() -> None:
    """Test A2A client headers include API key."""
    client = A2AClient(agent_url="https://agent.example.com", api_key="test-key")
    headers = client._get_headers()
    assert headers["X-API-Key"] == "test-key"
    assert headers["Content-Type"] == "application/json"


def test_a2a_client_headers_without_key() -> None:
    """Test A2A client headers without API key."""
    client = A2AClient(agent_url="https://agent.example.com")
    headers = client._get_headers()
    assert "X-API-Key" not in headers


def test_a2a_client_parse_task() -> None:
    """Test A2A client task parsing."""
    client = A2AClient(agent_url="https://agent.example.com")
    task = client._parse_task(
        {
            "id": "task-123",
            "contextId": "ctx-456",
            "status": {"state": "completed"},
        }
    )
    assert task.id == "task-123"
    assert task.context_id == "ctx-456"
    assert task.status.state.value == "completed"
