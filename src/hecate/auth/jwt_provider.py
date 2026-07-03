"""JWTAuthProvider — authenticates via JWT access tokens.

Wraps the existing ``decode_access_token()`` function as an
AuthProviderABC implementation.
"""

from __future__ import annotations

import logging
import uuid

from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProviderABC
from hecate.core.auth_context import AuthContext
from hecate.models.workspace_member import WorkspaceRole
from hecate.services.auth.token import decode_access_token

logger = logging.getLogger(__name__)


class JWTAuthProvider(AuthProviderABC):
    """Authenticates requests via JWT access tokens.

    Decodes the token using the existing ``decode_access_token()``
    function and constructs an :class:`AuthContext` from the claims.
    """

    @property
    def name(self) -> str:
        return "jwt"

    @property
    def description(self) -> str:
        return "JWT access token authentication"

    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
        """Decode a JWT token and return AuthContext.

        Args:
            token: The raw JWT string.
            db: Async database session (unused for JWT, but required by interface).

        Returns:
            AuthContext on success, None if token is invalid or expired.
        """
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
            return None
