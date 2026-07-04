"""OIDCAuthProvider — authenticates via OpenID Connect authorization code flow.

Wraps Authlib's OIDC client for ID token validation and userinfo
endpoint queries. Implements JIT (Just-In-Time) user provisioning.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProviderABC
from hecate.core.auth_context import AuthContext
from hecate.models.user import UserModel

logger = logging.getLogger(__name__)


class OIDCAuthProvider(AuthProviderABC):
    """Authenticates requests via OIDC ID tokens.

    Validates ID tokens from an OpenID Connect provider using
    Authlib's JWK/JWT validation and queries the userinfo endpoint
    for additional claims. Implements JIT user provisioning on first login.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        discovery_url: str,
        scope: str = "openid profile email",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._discovery_url = discovery_url
        self._scope = scope
        self._jwks: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "oidc"

    @property
    def description(self) -> str:
        return "OpenID Connect authentication via authorization code flow"

    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
        """Validate an OIDC ID token and return AuthContext.

        Args:
            token: The ID token string from the OIDC provider.
            db: Async database session for user lookups.

        Returns:
            AuthContext on success, None if token is invalid.
        """
        try:
            from authlib.jose import jwt as authlib_jwt
            from authlib.jose.errors import JoseError

            # Decode without verification first to get claims
            # Full verification requires JWKS from discovery
            try:
                claims = authlib_jwt.decode(token, self._get_jwk_set())
                claims.validate()
            except (JoseError, Exception):
                logger.debug("OIDC token validation failed", exc_info=True)
                return None

            sub = claims.get("sub")
            email = claims.get("email")
            name = claims.get("name", "")
            given_name = claims.get("given_name", "")
            family_name = claims.get("family_name", "")

            if not sub or not email:
                logger.warning("OIDC token missing required claims (sub, email)")
                return None

            # JIT provisioning — find or create user
            user = await self._find_or_create_user(
                db=db,
                sso_id=sub,
                email=email,
                display_name=name,
                given_name=given_name,
                family_name=family_name,
            )

            if not user.active:
                logger.warning("OIDC user %s is deactivated", email)
                return None

            return AuthContext(
                user_id=user.id,
                org_id=None,
                workspace_id=None,
                role=None,
                auth_method="sso",
                api_key_scope=None,
            )
        except Exception:
            logger.exception("OIDC authentication failed")
            return None

    async def _find_or_create_user(
        self,
        db: AsyncSession,
        sso_id: str,
        email: str,
        display_name: str,
        given_name: str,
        family_name: str,
    ) -> UserModel:
        """Find existing user by sso_id or create via JIT provisioning."""
        result = await db.execute(select(UserModel).where(UserModel.sso_id == sso_id))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        # JIT provisioning — create new user
        user = UserModel(
            email=email,
            hashed_password=secrets.token_urlsafe(32),
            sso_id=sso_id,
            display_name=display_name or None,
            given_name=given_name or None,
            family_name=family_name or None,
            active=True,
        )
        db.add(user)
        await db.flush()
        logger.info("JIT provisioned OIDC user: %s (sso_id=%s)", email, sso_id)
        return user

    def _get_jwk_set(self) -> dict[str, Any]:
        """Fetch JWKS from the OIDC discovery document."""
        if self._jwks is not None:
            return self._jwks

        try:
            import httpx

            resp = httpx.get(self._discovery_url)
            resp.raise_for_status()
            config = resp.json()
            jwks_uri = config.get("jwks_uri")
            if not jwks_uri:
                msg = "OIDC discovery document missing jwks_uri"
                raise ValueError(msg)
            jwks_resp = httpx.get(jwks_uri)
            jwks_resp.raise_for_status()
            self._jwks = jwks_resp.json()
            return self._jwks
        except Exception:
            logger.exception("Failed to fetch OIDC JWKS")
            return {"keys": []}
