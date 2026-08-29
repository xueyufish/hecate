"""Auth — pluggable authentication provider framework.

This subpackage holds the abstract interface (AuthProvider) and the
always-on base implementations (api_key, jwt). Enterprise SSO providers
(OIDC / SAML / LDAP) live in hecate-enterprise (see packages/).
"""

from __future__ import annotations

from hecate.auth.api_key_provider import APIKeyAuthProvider
from hecate.auth.jwt_provider import JWTAuthProvider
from hecate.auth.provider import AuthProvider

__all__ = [
    "APIKeyAuthProvider",
    "AuthProvider",
    "JWTAuthProvider",
]
