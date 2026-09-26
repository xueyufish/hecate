"""Workspace-aware authentication dependencies.

Provides ``get_auth_context()`` and RBAC dependency functions that replace
the legacy ``verify_api_key()`` and ``get_current_user_id()`` dependencies
with a unified AuthContext resolution mechanism.

Authentication flow:
1. Extract Bearer token from request header.
2. Try registered auth providers (JWT, API key) via AuthProvider.
3. Fallback to env-var API key (deprecated) → system scope.
4. Raise 401 if all methods fail.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.enterprise.auth.api_key_provider import APIKeyAuthProvider
from hecate.enterprise.auth.jwt_provider import JWTAuthProvider
from hecate.enterprise.auth.resolver import get_registered_providers, register_auth_providers, resolve_auth_context
from hecate.models.user import UserModel
from hecate.models.workspace_member import WorkspaceRole

logger = logging.getLogger(__name__)

security_scheme = HTTPBearer(auto_error=False)


def _ensure_providers() -> None:
    """Register built-in auth providers (JWT first, then API key).

    Checks the registry itself instead of a one-shot flag so a chain that
    was cleared at runtime (tests, future plugin swaps) self-heals on the
    next authentication instead of silently rejecting every token.
    """
    if not get_registered_providers():
        register_auth_providers(JWTAuthProvider(), APIKeyAuthProvider())


def _hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def _resolve_env_api_key(raw_key: str) -> AuthContext | None:
    """Resolve a raw token against deploy-time bootstrap key lists.

    Both ``HECATE_API_KEYS`` (deprecated) and ``PLATFORM_ADMIN_API_KEYS``
    establish a system-scope identity; they are the ONLY sources of
    system scope (see the ``platform-admin`` spec).
    """
    if raw_key in settings.api_keys_list:
        logger.warning(
            "API key from HECATE_API_KEYS env var is deprecated. "
            "Migrate to database-backed API keys via POST /api/api-keys."
        )
        return _env_system_context()

    if _is_platform_admin_token(raw_key):
        logger.info("Authenticated via PLATFORM_ADMIN_API_KEYS bootstrap token")
        return _env_system_context()

    return None


def _env_system_context() -> AuthContext:
    """Build the system-scope AuthContext for env bootstrap keys."""
    return AuthContext(
        user_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        org_id=None,
        workspace_id=None,
        role=None,
        auth_method="api_key",
        api_key_scope="system",
    )


async def authenticate_bearer(token: str | None, db: AsyncSession) -> AuthContext | None:
    """Resolve a raw bearer token through the shared provider chain.

    Single source of truth for bearer authentication, shared by the
    REST dependency (``get_auth_context``) and the MCP transport
    middleware: registered providers (JWT, database API key) first,
    then env bootstrap keys. Returns None when nothing accepts the
    token — callers decide the error surface (401 response etc.).
    """
    if not token:
        return None

    _ensure_providers()
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    try:
        return await resolve_auth_context(credentials, db)
    except HTTPException:
        pass

    return await _resolve_env_api_key(token)


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthContext:
    """Resolve the full authentication context for a request.

    Delegates to :func:`authenticate_bearer` — the provider chain shared
    with the MCP transport middleware.

    Returns:
        AuthContext with full identity and authorization state.

    Raises:
        HTTPException: 401 if no credentials are presented or no
            authentication method succeeds.
    """
    if credentials is None:
        raise _unauthorized()

    ctx = await authenticate_bearer(credentials.credentials, db)
    if ctx is not None:
        return ctx

    raise _unauthorized()


def _unauthorized() -> HTTPException:
    """Build the canonical 401 error response for this module."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "UNAUTHORIZED",
                "message": "Invalid API key or token",
                "details": None,
            }
        },
    )


def _is_platform_admin_token(raw_key: str) -> bool:
    """Constant-time compare a raw bearer token against configured admin keys."""
    raw_bytes = raw_key.encode()
    return any(
        hmac.compare_digest(raw_bytes, configured.encode()) for configured in settings.platform_admin_api_keys_list
    )


async def ensure_platform_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_scheme)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Raise 403 unless the request carries a platform admin identity.

    Platform admin resolution is deploy-time bootstrap only (see the
    ``platform-admin`` spec): a bearer token in ``PLATFORM_ADMIN_API_KEYS``
    (constant-time compared) or a JWT-authenticated user whose email is in
    ``PLATFORM_ADMIN_EMAILS``. Database-issued system-scope API keys do not
    qualify — their issuance history predates this gate.
    """
    if credentials is not None and _is_platform_admin_token(credentials.credentials):
        return

    result = await db.execute(select(UserModel).where(UserModel.id == ctx.user_id))
    user = result.scalar_one_or_none()
    if user is not None and user.email.lower() in settings.platform_admin_emails_list:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "FORBIDDEN",
                "message": "Platform admin required",
                "details": None,
            }
        },
    )


async def require_platform_admin(
    checked: Annotated[None, Depends(ensure_platform_admin)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthContext:
    """Dependency form of the platform admin gate; returns the AuthContext."""
    return ctx


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
