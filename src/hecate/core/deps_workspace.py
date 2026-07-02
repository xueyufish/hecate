"""Workspace-aware authentication dependencies.

Provides ``get_auth_context()`` and RBAC dependency functions that replace
the legacy ``verify_api_key()`` and ``get_current_user_id()`` dependencies
with a unified AuthContext resolution mechanism.

Authentication flow:
1. Extract Bearer token from request header.
2. Try JWT decode → resolve claims (sub, org_id, workspace_id, role).
3. Try API key hash lookup in database → resolve scope + workspace.
4. Fallback to env-var API key (deprecated) → system scope.
5. Raise 401 if all methods fail.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.workspace_member import WorkspaceRole
from hecate.services.auth.token import decode_access_token

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer()


def _hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def _resolve_api_key(
    raw_key: str,
    db: AsyncSession,
) -> AuthContext | None:
    """Attempt to resolve a raw token as a database-backed API key."""
    key_hash = _hash_key(raw_key)
    result = await db.execute(
        select(ApiKeyModel).where(
            ApiKeyModel.key_hash == key_hash,
            ApiKeyModel.is_active.is_(True),
            ApiKeyModel.deleted.is_(False),
        )
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return None

    # Check expiration
    from datetime import UTC, datetime

    if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
        return None

    # Update last_used_at
    api_key.last_used_at = datetime.now(UTC)
    await db.flush()

    if api_key.scope == ApiKeyScope.SYSTEM:
        return AuthContext(
            user_id=api_key.created_by,
            org_id=None,
            workspace_id=None,
            role=None,
            auth_method="api_key",
            api_key_scope="system",
        )
    return AuthContext(
        user_id=api_key.created_by,
        org_id=api_key.org_id,
        workspace_id=api_key.workspace_id,
        role=WorkspaceRole.ADMIN,
        auth_method="api_key",
        api_key_scope="workspace",
    )


async def _resolve_env_api_key(raw_key: str) -> AuthContext | None:
    """Attempt to resolve a raw token as an env-var API key (deprecated)."""
    if raw_key not in settings.api_keys_list:
        return None

    logger.warning(
        "API key from HECATE_API_KEYS env var is deprecated. "
        "Migrate to database-backed API keys via POST /api/api-keys."
    )
    return AuthContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthContext:
    """Resolve the full authentication context for a request.

    Tries, in order:
    1. JWT access token (workspace-scoped).
    2. Database-backed API key (system or workspace scoped).
    3. Environment variable API key (system scope, deprecated).

    Returns:
        AuthContext with full identity and authorization state.

    Raises:
        HTTPException: 401 if no authentication method succeeds.
    """
    token = credentials.credentials

    # 1. Try JWT
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
        org_id_raw = payload.get("org_id")
        workspace_id_raw = payload.get("workspace_id")
        role_raw = payload.get("role")

        return AuthContext(
            user_id=user_id,
            org_id=uuid.UUID(org_id_raw) if org_id_raw else None,
            workspace_id=uuid.UUID(workspace_id_raw) if workspace_id_raw else None,
            role=WorkspaceRole(role_raw) if role_raw else None,
            auth_method="jwt",
            api_key_scope=None,
        )
    except (JWTError, ValueError, KeyError):
        pass

    # 2. Try database API key
    ctx = await _resolve_api_key(token, db)
    if ctx is not None:
        return ctx

    # 3. Try env-var API key (deprecated)
    ctx = await _resolve_env_api_key(token)
    if ctx is not None:
        return ctx

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid API key or token",
                "details": None,
            }
        },
    )


def _role_level(role: WorkspaceRole) -> int:
    """Convert role to numeric level for comparison."""
    levels = {
        WorkspaceRole.VIEWER: 0,
        WorkspaceRole.EDITOR: 1,
        WorkspaceRole.ADMIN: 2,
    }
    return levels.get(role, -1)


async def require_workspace_viewer(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Require at least viewer role in the current workspace."""
    if ctx.is_system_scope:
        return ctx
    if ctx.role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": "Not a member of this workspace", "details": None}},
        )
    return ctx


async def require_workspace_editor(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Require at least editor role in the current workspace."""
    if ctx.is_system_scope:
        return ctx
    if ctx.role is None or _role_level(ctx.role) < _role_level(WorkspaceRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": "Editor role required", "details": None}},
        )
    return ctx


async def require_workspace_admin(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Require admin role in the current workspace."""
    if ctx.is_system_scope:
        return ctx
    if ctx.role != WorkspaceRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "FORBIDDEN", "message": "Admin role required", "details": None}},
        )
    return ctx
