"""Auth — pluggable authentication provider framework.

This subpackage holds the abstract interface (``AuthProvider``), the
always-on base implementations (``api_key``, ``jwt``, ``password``,
``token``), and the in-main-package resolver. SSO providers
(OIDC / SAML / LDAP) ship in the ``hecate-enterprise`` workspace
package (see ``packages/``) and register themselves via the
``hecate.auth_providers`` entry-point group.

History: moved from ``src/hecate/auth/`` (and ``services/auth/``) into
this enterprise domain directory during Phase R-complete. The package
name and module layout are intentionally aligned with
``hecate_enterprise.auth.*`` so that future plugins can match the
shape without translation.
"""

from __future__ import annotations

from hecate.enterprise.auth.api_key_provider import APIKeyAuthProvider
from hecate.enterprise.auth.jwt_provider import JWTAuthProvider
from hecate.enterprise.auth.provider import AuthProvider

__all__ = [
    "APIKeyAuthProvider",
    "AuthProvider",
    "JWTAuthProvider",
]
