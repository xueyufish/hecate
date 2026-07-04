"""Auth provider registration — register built-in auth providers with PluginRegistry."""

from __future__ import annotations

import logging

from hecate.auth.api_key_provider import APIKeyAuthProvider
from hecate.auth.jwt_provider import JWTAuthProvider
from hecate.auth.provider import AuthProviderABC
from hecate.core.config import settings
from hecate.plugin.manifest import PluginManifest
from hecate.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)


def register_auth_providers(registry: PluginRegistry) -> int:
    """Register built-in auth providers with the plugin registry.

    Args:
        registry: The PluginRegistry to register providers with.

    Returns:
        Number of providers registered.
    """
    provider_instances: list[AuthProviderABC] = [
        JWTAuthProvider(),
        APIKeyAuthProvider(),
    ]

    if settings.SSO_OIDC_CLIENT_ID and settings.SSO_OIDC_DISCOVERY_URL:
        from hecate.auth.oidc_provider import OIDCAuthProvider

        provider_instances.append(
            OIDCAuthProvider(
                client_id=settings.SSO_OIDC_CLIENT_ID,
                client_secret=settings.SSO_OIDC_CLIENT_SECRET,
                discovery_url=settings.SSO_OIDC_DISCOVERY_URL,
                scope=settings.SSO_OIDC_SCOPE,
            )
        )

    if settings.SSO_SAML_SP_ENTITY_ID and settings.SSO_SAML_IDP_SSO_URL:
        from hecate.auth.saml_provider import SAMLAuthProvider

        provider_instances.append(
            SAMLAuthProvider(
                sp_entity_id=settings.SSO_SAML_SP_ENTITY_ID,
                sp_acs_url=settings.SSO_SAML_SP_ACS_URL,
                idp_entity_id=settings.SSO_SAML_IDP_ENTITY_ID,
                idp_sso_url=settings.SSO_SAML_IDP_SSO_URL,
                idp_x509_cert=settings.SSO_SAML_IDP_X509_CERT,
            )
        )

    if settings.SSO_LDAP_SERVER_URL and settings.SSO_LDAP_BASE_DN:
        from hecate.auth.ldap_provider import LDAPAuthProvider

        provider_instances.append(
            LDAPAuthProvider(
                server_url=settings.SSO_LDAP_SERVER_URL,
                base_dn=settings.SSO_LDAP_BASE_DN,
                bind_dn=settings.SSO_LDAP_BIND_DN,
                bind_password=settings.SSO_LDAP_BIND_PASSWORD,
                search_filter=settings.SSO_LDAP_SEARCH_FILTER,
                use_ssl=settings.SSO_LDAP_USE_SSL,
            )
        )

    count = 0
    for provider in provider_instances:
        try:
            manifest = PluginManifest(
                type="auth_provider",
                name=provider.name,
                version="1.0.0",
                api_version="1.0",
                min_platform_version="0.6.0",
                description=provider.description,
            )
            registry.register(manifest, provider)
            count += 1
        except Exception:
            logger.exception("Failed to register auth provider %s", provider.name)

    logger.info("Registered %d built-in auth providers", count)
    return count
