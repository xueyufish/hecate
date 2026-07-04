"""SSO authentication routes — OIDC and SAML login/callback endpoints.

Provides FastAPI routes for initiating SSO login flows and handling
callbacks from Identity Providers. Issues Hecate JWT tokens on success.
"""

from __future__ import annotations

import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.auth.oidc_provider import OIDCAuthProvider
from hecate.auth.saml_provider import SAMLAuthProvider
from hecate.core.config import settings
from hecate.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/sso", tags=["sso"])

# In-memory state store for CSRF protection (use Redis in production)
_state_store: dict[str, str] = {}


def _get_oidc_provider() -> OIDCAuthProvider | None:
    """Get OIDC provider if configured."""
    if not settings.SSO_OIDC_CLIENT_ID or not settings.SSO_OIDC_DISCOVERY_URL:
        return None
    return OIDCAuthProvider(
        client_id=settings.SSO_OIDC_CLIENT_ID,
        client_secret=settings.SSO_OIDC_CLIENT_SECRET,
        discovery_url=settings.SSO_OIDC_DISCOVERY_URL,
        scope=settings.SSO_OIDC_SCOPE,
    )


def _get_saml_provider() -> SAMLAuthProvider | None:
    """Get SAML provider if configured."""
    if not settings.SSO_SAML_SP_ENTITY_ID or not settings.SSO_SAML_IDP_SSO_URL:
        return None
    return SAMLAuthProvider(
        sp_entity_id=settings.SSO_SAML_SP_ENTITY_ID,
        sp_acs_url=settings.SSO_SAML_SP_ACS_URL,
        idp_entity_id=settings.SSO_SAML_IDP_ENTITY_ID,
        idp_sso_url=settings.SSO_SAML_IDP_SSO_URL,
        idp_x509_cert=settings.SSO_SAML_IDP_X509_CERT,
    )


@router.get("/oidc/login")
async def oidc_login() -> RedirectResponse:
    """Initiate OIDC login by redirecting to the IdP authorization endpoint.

    Returns:
        RedirectResponse to the OIDC provider's authorization URL.
    """
    provider = _get_oidc_provider()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC provider not configured",
        )

    state = secrets.token_urlsafe(32)
    _state_store[state] = "oidc"

    # Build authorization URL
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=settings.SSO_OIDC_CLIENT_ID,
        client_secret=settings.SSO_OIDC_CLIENT_SECRET,
        server_metadata_url=settings.SSO_OIDC_DISCOVERY_URL,
        client_kwargs={"scope": settings.SSO_OIDC_SCOPE},
    )

    # For now, construct URL manually since we don't have a full request context
    # In production, use oauth.oidc.authorize_redirect()
    discovery_url = settings.SSO_OIDC_DISCOVERY_URL
    import httpx

    resp = httpx.get(discovery_url)
    resp.raise_for_status()
    config = resp.json()
    authorization_endpoint = config.get("authorization_endpoint")

    params = {
        "response_type": "code",
        "client_id": settings.SSO_OIDC_CLIENT_ID,
        "scope": settings.SSO_OIDC_SCOPE,
        "state": state,
        "redirect_uri": f"{settings.SSO_SAML_SP_ACS_URL or '/auth/sso/oidc/callback'}",
    }

    return RedirectResponse(url=f"{authorization_endpoint}?{urlencode(params)}")


@router.get("/oidc/callback")
async def oidc_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Handle OIDC callback after IdP authentication.

    Args:
        code: Authorization code from the IdP.
        state: CSRF state parameter.
        db: Database session.

    Returns:
        JWT token response on success.
    """
    # Validate state
    if state not in _state_store:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter",
        )
    del _state_store[state]

    provider = _get_oidc_provider()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC provider not configured",
        )

    # Exchange code for tokens
    import httpx

    discovery_url = settings.SSO_OIDC_DISCOVERY_URL
    resp = httpx.get(discovery_url)
    resp.raise_for_status()
    config = resp.json()
    token_endpoint = config.get("token_endpoint")

    token_resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{settings.SSO_SAML_SP_ACS_URL or '/auth/sso/oidc/callback'}",
            "client_id": settings.SSO_OIDC_CLIENT_ID,
            "client_secret": settings.SSO_OIDC_CLIENT_SECRET,
        },
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()

    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC provider did not return an ID token",
        )

    # Validate ID token and get AuthContext
    ctx = await provider.authenticate(id_token, db)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OIDC authentication failed",
        )

    # Issue Hecate JWT
    from hecate.services.auth.token import create_access_token

    access_token = create_access_token(user_id=ctx.user_id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "auth_method": "sso",
    }


@router.get("/saml/login")
async def saml_login() -> RedirectResponse:
    """Initiate SAML login by redirecting to the IdP SSO URL.

    Returns:
        RedirectResponse to the SAML IdP's SSO endpoint.
    """
    provider = _get_saml_provider()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SAML provider not configured",
        )

    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth

        settings_data = provider.get_saml_settings()
        acs_url = settings.SSO_SAML_SP_ACS_URL
        acs_host = acs_url.split("//")[1].split("/")[0] if acs_url else ""
        req = {
            "https": "on",
            "http_host": acs_host,
            "script_name": "/",
        }

        auth = OneLogin_Saml2_Auth(req, settings_data)
        sso_url = auth.login()
        return RedirectResponse(url=sso_url)
    except Exception as exc:
        logger.exception("SAML login initiation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SAML login initiation failed",
        ) from exc


@router.post("/saml/acs")
async def saml_acs(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    """Handle SAML Assertion Consumer Service (ACS) callback.

    Returns:
        JWT token response on successful SAML authentication.
    """
    provider = _get_saml_provider()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SAML provider not configured",
        )

    # Get SAML response from POST body
    form = await request.form()
    saml_response = form.get("SAMLResponse")
    if not saml_response:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing SAMLResponse in POST body",
        )

    # Validate and authenticate
    ctx = await provider.authenticate(str(saml_response), db)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SAML authentication failed",
        )

    # Issue Hecate JWT
    from hecate.services.auth.token import create_access_token

    access_token = create_access_token(user_id=ctx.user_id)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "auth_method": "sso",
    }
