"""Workspace-aware authentication dependencies.

Provides ``get_auth_context()`` and RBAC dependency functions that replace
the legacy ``verify_api_key()`` and ``get_current_user_id()`` dependencies
with a unified AuthContext resolution mechanism.

Authentication flow:
1. Extract Bearer token from request header.
2. Try registered auth providers (JWT, API key) via AuthProviderABC.
3. Fallback to env-var API key (deprecated) → system scope.
4. Raise 401 if all methods fail.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.api_key_provider import APIKeyAuthProvider
from hecate.auth.jwt_provider import JWTAuthProvider
from hecate.auth.resolver import register_auth_providers, resolve_auth_context
from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.models.workspace_member import WorkspaceRole

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer()

# Register built-in auth providers (JWT first, then API key)
_providers_registered = False


def _ensure_providers() -> None:
    """Register built-in auth providers on first use."""
    global _providers_registered
    if not _providers_registered:
        register_auth_providers(JWTAuthProvider(), APIKeyAuthProvider())
        _providers_registered = True


def _hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


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

    Delegates to registered auth providers (JWT, API key) via the
    AuthProviderABC framework. Falls back to env-var API key (deprecated)
    if no provider succeeds.

    Returns:
        AuthContext with full identity and authorization state.

    Raises:
        HTTPException: 401 if no authentication method succeeds.
    """
    _ensure_providers()

    # Try registered providers (JWT + API key)
    try:
        return await resolve_auth_context(credentials, db)
    except HTTPException:
        pass

    # Fallback: env-var API key (deprecated)
    token = credentials.credentials
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
