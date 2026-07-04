"""APIKeyAuthProvider — authenticates via database-backed API keys.

Wraps the existing ``_resolve_api_key()`` logic as an
AuthProviderABC implementation.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProviderABC
from hecate.core.auth_context import AuthContext
from hecate.models.api_key import ApiKeyModel, ApiKeyScope
from hecate.models.user import UserModel
from hecate.models.workspace_member import WorkspaceRole

logger = logging.getLogger(__name__)


def _hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


class APIKeyAuthProvider(AuthProviderABC):
    """Authenticates requests via database-backed API keys.

    Looks up the API key hash in the database and constructs
    an :class:`AuthContext` from the key's scope and workspace.
    """

    @property
    def name(self) -> str:
        return "api_key"

    @property
    def description(self) -> str:
        return "Database-backed API key authentication"

    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
        """Look up an API key in the database and return AuthContext.

        Args:
            token: The raw API key string.
            db: Async database session for key lookups.

        Returns:
            AuthContext on success, None if key is not found or expired.
        """
        key_hash = _hash_key(token)
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
        if api_key.expires_at is not None and api_key.expires_at < datetime.now(UTC):
            return None

        # Reject inactive users
        user_result = await db.execute(select(UserModel).where(UserModel.id == api_key.created_by))
        user = user_result.scalar_one_or_none()
        if user is None or not user.active:
            return None

        # Update last_used_at
        api_key.last_used_at = datetime.now(UTC)
        await db.flush()

        scope: Literal["system", "workspace"] = "system" if api_key.scope == ApiKeyScope.SYSTEM else "workspace"

        if api_key.scope == ApiKeyScope.SYSTEM:
            return AuthContext(
                user_id=api_key.created_by,
                org_id=None,
                workspace_id=None,
                role=None,
                auth_method="api_key",
                api_key_scope=scope,
            )
        return AuthContext(
            user_id=api_key.created_by,
            org_id=api_key.org_id,
            workspace_id=api_key.workspace_id,
            role=WorkspaceRole.ADMIN,
            auth_method="api_key",
            api_key_scope=scope,
        )
