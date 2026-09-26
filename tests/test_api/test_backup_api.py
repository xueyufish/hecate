"""Tests for backup REST API endpoint authorization.

Covers the platform-admin gate on ``/api/system/*`` (see the ``backup-api``
and ``platform-admin`` specs): anonymous requests get 401, authenticated
non-platform-admin callers get 403 with no backup/restore function invoked,
and platform admins (deploy-time token or email allowlist) can operate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


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


def _admin_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Anonymous access — 401 on every endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anonymous_requests_are_unauthorized(anonymous_client: AsyncClient) -> None:
    """Every backup/restore endpoint returns 401 without credentials."""
    endpoints: list[tuple[str, str, dict | None]] = [
        ("POST", "/api/system/backups", {"scope": "all"}),
        ("GET", "/api/system/backups", None),
        ("GET", f"/api/system/backups/{uuid.uuid4()}", None),
        ("POST", f"/api/system/backups/{uuid.uuid4()}/verify", None),
        ("POST", "/api/system/restore", {"backup_id": str(uuid.uuid4()), "confirm": True}),
    ]
    for method, url, payload in endpoints:
        response = await anonymous_client.request(method, url, json=payload)
        assert response.status_code == 401, f"{method} {url} expected 401"
        detail = response.json()["detail"]
        assert detail["error"]["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# Authenticated non-platform-admin — 403, operations never invoked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_admin_is_forbidden_on_all_endpoints(client: AsyncClient) -> None:
    """Workspace admin credentials do not cross the platform boundary."""
    create_mock = AsyncMock()
    list_mock = AsyncMock()
    restore_mock = AsyncMock()
    verify_mock = AsyncMock()

    endpoints: list[tuple[str, str, dict | None]] = [
        ("POST", "/api/system/backups", {"scope": "all"}),
        ("GET", "/api/system/backups", None),
        ("GET", f"/api/system/backups/{uuid.uuid4()}", None),
        ("POST", f"/api/system/backups/{uuid.uuid4()}/verify", None),
        ("POST", "/api/system/restore", {"backup_id": str(uuid.uuid4()), "confirm": True}),
    ]
    with (
        patch("hecate.ops.backup.orchestrator.create_backup", new=create_mock),
        patch("hecate.ops.backup.orchestrator.list_backups", new=list_mock),
        patch("hecate.ops.backup.restore.restore_backup", new=restore_mock),
        patch("hecate.ops.backup.verification.verify_backup", new=verify_mock),
    ):
        for method, url, payload in endpoints:
            response = await client.request(method, url, json=payload)
            assert response.status_code == 403, f"{method} {url} expected 403"
            detail = response.json()["detail"]
            assert detail["error"]["code"] == "FORBIDDEN"

    for mock in (create_mock, list_mock, restore_mock, verify_mock):
        mock.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_admin_token_is_forbidden(client: AsyncClient, platform_admin_token: str) -> None:
    """A non-matching bearer token does not grant platform admin."""
    response = await client.post(
        "/api/system/backups",
        json={"scope": "all"},
        headers=_admin_headers("not-the-admin-token"),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_db_system_api_key_is_not_platform_admin(client: AsyncClient) -> None:
    """A database-issued system-scope API key does not satisfy the gate."""
    from hecate.core.auth_context import AuthContext
    from hecate.core.deps_workspace import get_auth_context
    from hecate.main import app

    system_ctx = AuthContext(
        user_id=uuid.UUID(int=0),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )

    async def override_system_ctx() -> AuthContext:
        return system_ctx

    app.dependency_overrides[get_auth_context] = override_system_ctx
    with patch("hecate.ops.backup.orchestrator.create_backup", new_callable=AsyncMock) as mock_create:
        response = await client.post("/api/system/backups", json={"scope": "all"})
    assert response.status_code == 403
    mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# Platform admin via deploy-time token — operations execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_admin_token_can_create_backup(
    client: AsyncClient, platform_admin_token: str, mock_backup_record
) -> None:
    """POST /api/system/backups executes for a platform admin token."""
    with patch(
        "hecate.ops.backup.orchestrator.create_backup",
        new_callable=AsyncMock,
        return_value=mock_backup_record,
    ):
        response = await client.post(
            "/api/system/backups", json={"scope": "all"}, headers=_admin_headers(platform_admin_token)
        )

    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "all"
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_platform_admin_token_can_list_and_get_backups(
    client: AsyncClient, platform_admin_token: str, mock_backup_record
) -> None:
    """GET backup list and detail execute for a platform admin token."""
    headers = _admin_headers(platform_admin_token)

    with patch(
        "hecate.ops.backup.orchestrator.list_backups",
        new_callable=AsyncMock,
        return_value=[mock_backup_record],
    ):
        response = await client.get("/api/system/backups", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) == 1

    with patch("hecate.ops.api.backup.async_session_factory") as mock_factory:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_backup_record)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        response = await client.get(f"/api/system/backups/{mock_backup_record.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(mock_backup_record.id)


@pytest.mark.asyncio
async def test_platform_admin_token_can_verify_backup(client: AsyncClient, platform_admin_token: str) -> None:
    """POST verify executes for a platform admin token."""
    mock_result = {"matched": True, "mismatches": []}
    with patch(
        "hecate.ops.backup.verification.verify_backup",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        response = await client.post(
            f"/api/system/backups/{uuid.uuid4()}/verify",
            headers=_admin_headers(platform_admin_token),
        )

    assert response.status_code == 200
    assert response.json()["matched"] is True


@pytest.mark.asyncio
async def test_platform_admin_token_can_restore_with_confirm(client: AsyncClient, platform_admin_token: str) -> None:
    """POST restore with confirm=true executes for a platform admin token."""
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
            json={"backup_id": str(uuid.uuid4()), "scope": "pg", "confirm": True},
            headers=_admin_headers(platform_admin_token),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_restore_requires_confirm_even_for_platform_admin(client: AsyncClient, platform_admin_token: str) -> None:
    """confirm=false is rejected with 400 even for a platform admin."""
    with patch("hecate.ops.backup.restore.restore_backup", new_callable=AsyncMock) as mock_restore:
        response = await client.post(
            "/api/system/restore",
            json={"backup_id": str(uuid.uuid4()), "confirm": False},
            headers=_admin_headers(platform_admin_token),
        )

    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]
    mock_restore.assert_not_called()


# ---------------------------------------------------------------------------
# Platform admin via email allowlist — operations execute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_platform_admin_email_allowlist_can_create_backup(
    client: AsyncClient,
    platform_admin_email: str,
    db_session,
    test_user_id,
    mock_backup_record,
) -> None:
    """A JWT user on the email allowlist passes the gate without a token."""
    from hecate.enterprise.auth.password import hash_password
    from hecate.models.user import UserModel

    db_session.add(
        UserModel(id=test_user_id, email=platform_admin_email, hashed_password=hash_password("test-password"))
    )
    await db_session.flush()

    with patch(
        "hecate.ops.backup.orchestrator.create_backup",
        new_callable=AsyncMock,
        return_value=mock_backup_record,
    ):
        response = await client.post("/api/system/backups", json={"scope": "all"})

    assert response.status_code == 200
    assert response.json()["scope"] == "all"


@pytest.mark.asyncio
async def test_email_not_on_allowlist_is_forbidden(
    client: AsyncClient, platform_admin_email: str, db_session, test_user_id
) -> None:
    """An authenticated user whose email is not allowlisted gets 403."""
    from hecate.enterprise.auth.password import hash_password
    from hecate.models.user import UserModel

    db_session.add(
        UserModel(
            id=test_user_id,
            email="someone-else@hecate.dev",
            hashed_password=hash_password("test-password"),
        )
    )
    await db_session.flush()

    response = await client.post("/api/system/backups", json={"scope": "all"})
    assert response.status_code == 403
