"""SAMLAuthProvider — authenticates via SAML 2.0 SP-initiated SSO.

Wraps python3-saml for SAML assertion validation and user
attribute extraction. Implements JIT user provisioning.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.provider import AuthProviderABC
from hecate.core.auth_context import AuthContext
from hecate.models.user import UserModel

logger = logging.getLogger(__name__)


class SAMLAuthProvider(AuthProviderABC):
    """Authenticates requests via SAML 2.0 assertions.

    Validates SAML responses from an Identity Provider using
    python3-saml's signature validation. Implements JIT user
    provisioning on first SAML login.
    """

    def __init__(
        self,
        sp_entity_id: str,
        sp_acs_url: str,
        idp_entity_id: str,
        idp_sso_url: str,
        idp_x509_cert: str,
    ) -> None:
        self._sp_entity_id = sp_entity_id
        self._sp_acs_url = sp_acs_url
        self._idp_entity_id = idp_entity_id
        self._idp_sso_url = idp_sso_url
        self._idp_x509_cert = idp_x509_cert

    @property
    def name(self) -> str:
        return "saml"

    @property
    def description(self) -> str:
        return "SAML 2.0 SP-initiated SSO authentication"

    def get_saml_settings(self) -> dict:
        """Return SAML settings dict for python3-saml."""
        return {
            "sp": {
                "entityId": self._sp_entity_id,
                "assertionConsumerService": {
                    "url": self._sp_acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
            },
            "idp": {
                "entityId": self._idp_entity_id,
                "singleSignOnService": {
                    "url": self._idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self._idp_x509_cert,
            },
        }

    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
        """Validate a SAML response and return AuthContext.

        Args:
            token: Base64-encoded SAML response from the IdP.
            db: Async database session for user lookups.

        Returns:
            AuthContext on success, None if assertion is invalid.
        """
        try:
            from onelogin.saml2.response import OneLogin_Saml2_Response

            # Decode the base64 SAML response
            saml_response_b64 = token

            # Validate the SAML response
            settings = self.get_saml_settings()
            # Create a minimal request dict for validation
            acs_host = self._sp_acs_url.split("//")[1].split("/")[0]
            acs_path = (
                self._sp_acs_url.split("//")[1].split("/", 1)[1] if "/" in self._sp_acs_url.split("//")[1] else ""
            )  # noqa: E501
            req = {
                "https": "on",
                "http_host": acs_host,
                "script_name": f"/{acs_path}",
            }

            # Validate signature and extract attributes
            saml_resp = OneLogin_Saml2_Response(settings, saml_response_b64)
            saml_resp.is_valid(req, raise_exceptions=True)

            # Extract user attributes
            name_id = saml_resp.get_nameid()
            attributes = saml_resp.get_attributes()

            if not name_id:
                logger.warning("SAML response missing NameID")
                return None

            # Extract email from attributes
            email = (
                attributes.get("email", [None])[0]
                or attributes.get("urn:oid:0.9.2342.19200300.100.1.3", [None])[0]
                or attributes.get("mail", [None])[0]
                or name_id
            )

            display_name = (
                attributes.get("displayName", [None])[0]
                or attributes.get("urn:oid:2.16.840.1.113730.3.1.241", [None])[0]
                or ""
            )

            given_name = attributes.get("givenName", [None])[0] or attributes.get("urn:oid:2.5.4.42", [None])[0] or ""

            family_name = attributes.get("sn", [None])[0] or attributes.get("urn:oid:2.5.4.4", [None])[0] or ""

            # JIT provisioning
            user = await self._find_or_create_user(
                db=db,
                sso_id=name_id,
                email=email,
                display_name=display_name,
                given_name=given_name,
                family_name=family_name,
            )

            if not user.active:
                logger.warning("SAML user %s is deactivated", email)
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
            logger.exception("SAML authentication failed")
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
        logger.info("JIT provisioned SAML user: %s (sso_id=%s)", email, sso_id)
        return user
