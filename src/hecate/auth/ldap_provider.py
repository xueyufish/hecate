"""LDAPAuthProvider — authenticates via LDAP bind authentication.

Uses ldap3 for async LDAP operations including user search and
bind authentication. Implements JIT user provisioning on first login.
"""

from __future__ import annotations

import base64
import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProviderABC
from hecate.core.auth_context import AuthContext
from hecate.models.user import UserModel

logger = logging.getLogger(__name__)


class LDAPAuthProvider(AuthProviderABC):
    """Authenticates requests via LDAP bind authentication.

    Searches for the user DN using a configurable filter, then
    attempts to bind with the user's credentials. Implements JIT
    user provisioning on first successful LDAP bind.
    """

    def __init__(
        self,
        server_url: str,
        base_dn: str,
        bind_dn: str,
        bind_password: str,
        search_filter: str = "(uid={})",
        use_ssl: bool = True,
    ) -> None:
        self._server_url = server_url
        self._base_dn = base_dn
        self._bind_dn = bind_dn
        self._bind_password = bind_password
        self._search_filter = search_filter
        self._use_ssl = use_ssl

    @property
    def name(self) -> str:
        return "ldap"

    @property
    def description(self) -> str:
        return "LDAP bind authentication"

    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
        """Authenticate via LDAP bind and return AuthContext.

        Args:
            token: Base64-encoded "username:password" string.
            db: Async database session for user lookups.

        Returns:
            AuthContext on success, None if bind fails.
        """
        try:
            from ldap3 import AUTO_BIND_NO_TLS, AUTO_BIND_TLS_BEFORE_BIND, Connection, Server

            # Decode credentials
            try:
                decoded = base64.b64decode(token).decode("utf-8")
                if ":" not in decoded:
                    return None
                username, password = decoded.split(":", 1)
            except Exception:
                logger.debug("LDAP: invalid credential format")
                return None

            if not username or not password:
                return None

            # Connect to LDAP server with service account to search for user DN
            server = Server(self._server_url, use_ssl=self._use_ssl)
            auto_bind = AUTO_BIND_TLS_BEFORE_BIND if self._use_ssl else AUTO_BIND_NO_TLS

            try:
                conn = Connection(
                    server,
                    user=self._bind_dn,
                    password=self._bind_password,
                    auto_bind=auto_bind,
                )
            except Exception:
                logger.warning("LDAP: failed to connect to %s", self._server_url)
                return None

            # Search for user DN
            search_filter = self._search_filter.format(username)
            conn.search(
                search_base=self._base_dn,
                search_filter=search_filter,
                attributes=["cn", "mail", "givenName", "sn", "displayName"],
            )

            if not conn.entries:
                logger.debug("LDAP: user %s not found", username)
                conn.unbind()
                return None

            user_entry = conn.entries[0]
            user_dn = str(user_entry.entry_dn)
            mail = str(user_entry.mail) if hasattr(user_entry, "mail") and user_entry.mail else None
            email = mail or f"{username}@ldap.local"
            dn_attr = (
                str(user_entry.displayName) if hasattr(user_entry, "displayName") and user_entry.displayName else ""
            )  # noqa: E501
            gn_attr = str(user_entry.givenName) if hasattr(user_entry, "givenName") and user_entry.givenName else ""  # noqa: E501
            fn_attr = str(user_entry.sn) if hasattr(user_entry, "sn") and user_entry.sn else ""

            # Try to bind with user credentials
            try:
                user_conn = Connection(
                    server,
                    user=user_dn,
                    password=password,
                    auto_bind=auto_bind,
                )
                user_conn.unbind()
            except Exception:
                logger.debug("LDAP: bind failed for %s", user_dn)
                conn.unbind()
                return None

            conn.unbind()

            # JIT provisioning
            user = await self._find_or_create_user(
                db=db,
                sso_id=username,
                email=email,
                display_name=dn_attr,
                given_name=gn_attr,
                family_name=fn_attr,
            )

            if not user.active:
                logger.warning("LDAP user %s is deactivated", username)
                return None

            return AuthContext(
                user_id=user.id,
                org_id=None,
                workspace_id=None,
                role=None,
                auth_method="ldap",
                api_key_scope=None,
            )
        except Exception:
            logger.exception("LDAP authentication failed")
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

        # JIT provisioning
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
        logger.info("JIT provisioned LDAP user: %s (sso_id=%s)", email, sso_id)
        return user
