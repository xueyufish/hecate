"""Tests for backup REST API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_backup_record():
    """Create a mock BackupRecord for API responses."""
    record = MagicMock()
    record.id = uuid.uuid4()
    record.backup_type = "full"
    record.scope = "all"
    record.status = "completed"
    record.storage_type = "minio"
    record.storage_path = "20260730_020000"
    record.size_bytes = 1048576
    record.checksum = "abc123"
    record.started_at = datetime(2026, 7, 30, 2, 0, 0)
    record.completed_at = datetime(2026, 7, 30, 2, 5, 0)
    record.error_message = None
    record.metadata_ = {"pg": {"agents": 10}}
    record.verified_at = None
    record.verification_status = None
    record.created_at = datetime(2026, 7, 30, 2, 0, 0)
    record.updated_at = datetime(2026, 7, 30, 2, 5, 0)
    return record


async def test_api_create_backup(client, mock_backup_record):
    """POST /api/system/backups creates a backup."""
    with patch(
        "hecate.ops.backup.orchestrator.create_backup",
        new_callable=AsyncMock,
        return_value=mock_backup_record,
    ):
        response = await client.post("/api/system/backups", json={"scope": "all"})

    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "all"
    assert data["status"] == "completed"


async def test_api_list_backups(client, mock_backup_record):
    """GET /api/system/backups returns backup list."""
    with patch(
        "hecate.ops.backup.orchestrator.list_backups",
        new_callable=AsyncMock,
        return_value=[mock_backup_record],
    ):
        response = await client.get("/api/system/backups")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["scope"] == "all"


async def test_api_get_backup_detail(client, mock_backup_record):
    """GET /api/system/backups/{id} returns backup details."""
    with patch("hecate.api.system.backup.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_backup_record)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        response = await client.get(f"/api/system/backups/{mock_backup_record.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(mock_backup_record.id)


async def test_api_get_backup_not_found(client):
    """GET /api/system/backups/{id} returns 404 for unknown backup."""
    backup_id = uuid.uuid4()

    with patch("hecate.api.system.backup.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        response = await client.get(f"/api/system/backups/{backup_id}")

    assert response.status_code == 404


async def test_api_restore_requires_confirm(client):
    """POST /api/system/restore returns 400 without confirm=true."""
    backup_id = uuid.uuid4()

    response = await client.post(
        "/api/system/restore",
        json={"backup_id": str(backup_id), "confirm": False},
    )

    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]


async def test_api_restore_with_confirm(client):
    """POST /api/system/restore with confirm=true executes restore."""
    backup_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.status = "completed"
    mock_result.details = {"scopes": {"pg": "ok"}}
    mock_result.error = None

    with patch(
        "hecate.ops.backup.restore.restore_backup",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            "/api/system/restore",
            json={"backup_id": str(backup_id), "scope": "pg", "confirm": True},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"


async def test_api_verify_backup(client):
    """POST /api/system/backups/{id}/verify triggers verification."""
    backup_id = uuid.uuid4()
    mock_result = {"matched": True, "mismatches": []}

    with patch(
        "hecate.ops.backup.verification.verify_backup",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(f"/api/system/backups/{backup_id}/verify")

    assert response.status_code == 200
    data = response.json()
    assert data["matched"] is True


async def test_api_create_backup_invalid_scope(client):
    """POST /api/system/backups rejects invalid scope."""
    response = await client.post("/api/system/backups", json={"scope": "invalid"})

    assert response.status_code == 422
