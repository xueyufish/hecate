"""Integration tests: audit events carry the real operator identity.

Wires the audit queue around the real FastAPI app so the full chain is
exercised: auth dependency writes ``request.state.auth_context`` (or the
failure reason), the audit middleware records it — for JWT, database API
key, and rejected-credential requests (p1-audit-cost-a2a).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
from httpx import AsyncClient

from hecate.core.middleware.audit import set_audit_queue
from hecate.enterprise.auth.password import hash_password
from hecate.enterprise.auth.token import create_access_token
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.organization import OrganizationModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole
from hecate.ops.audit.store import AuditEvent


async def _seed_workspace(db_session, role: WorkspaceRole = WorkspaceRole.ADMIN):
    """Create user/org/workspace/member; return them plus nothing else."""
    suffix = uuid.uuid4().hex[:8]
    user = UserModel(email=f"audit-{suffix}@example.com", hashed_password=hash_password("securepass123"))
    db_session.add(user)
    await db_session.flush()
    org = OrganizationModel(name=f"Org {suffix}", slug=f"org-{suffix}", owner_id=user.id)
    db_session.add(org)
    await db_session.flush()
    ws = WorkspaceModel(org_id=org.id, name=f"WS {suffix}", slug=f"ws-{suffix}")
    db_session.add(ws)
    await db_session.flush()
    member = WorkspaceMemberModel(user_id=user.id, workspace_id=ws.id, role=role)
    db_session.add(member)
    await db_session.flush()
    await db_session.commit()
    return user, org, ws, member


@pytest.mark.usefixtures("setup_database")
class TestAuditIdentityIntegration:
    async def test_jwt_operation_audits_real_operator(self, anonymous_client: AsyncClient, db_session) -> None:
        user, org, ws, _member = await _seed_workspace(db_session)
        token = create_access_token(user.id, org.id, ws.id, "admin")
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        set_audit_queue(queue)
        try:
            resp = await anonymous_client.post(
                "/api/agents",
                json={"name": "Audit Agent", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 201
            event = queue.get_nowait()
            assert event.user_id == user.id
            assert event.org_id == org.id
            assert event.metadata["auth_method"] == "jwt"
        finally:
            set_audit_queue(None)

    async def test_db_api_key_operation_audits_real_operator(self, anonymous_client: AsyncClient, db_session) -> None:
        user, _org, ws, _member = await _seed_workspace(db_session)
        raw_key = "hcat_audit_integration_key"
        db_session.add(
            ApiKeyModel(
                name="audit key",
                key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                key_prefix="hcat_aud",
                scope=ApiKeyScope.WORKSPACE,
                workspace_id=ws.id,
                org_id=ws.org_id,
                created_by=user.id,
                is_active=True,
            )
        )
        await db_session.commit()
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        set_audit_queue(queue)
        try:
            resp = await anonymous_client.post(
                "/api/agents",
                json={"name": "Audit Agent", "model_config": {"model": "gpt-4o"}, "mode": "chat"},
                headers={"Authorization": f"Bearer {raw_key}"},
            )
            assert resp.status_code == 201
            event = queue.get_nowait()
            assert event.user_id == user.id
            assert event.metadata["auth_method"] == "api_key"
        finally:
            set_audit_queue(None)

    async def test_rejected_credentials_recorded_with_failure_type(self, anonymous_client: AsyncClient) -> None:
        raw_credential = "hcat-definitely-invalid-token"
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue()
        set_audit_queue(queue)
        try:
            resp = await anonymous_client.get(
                "/api/agents",
                headers={"Authorization": f"Bearer {raw_credential}"},
            )
            assert resp.status_code == 401
            event = queue.get_nowait()
            assert event.metadata["auth_failure"] == "invalid_credentials"
            # The failure is attributed to the anonymous sentinel, never to
            # a real identity, and the raw credential never reaches the event.
            assert event.user_id == uuid.UUID(int=0)
            assert raw_credential not in str(event.metadata)
        finally:
            set_audit_queue(None)
