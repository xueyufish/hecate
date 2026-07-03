"""Auth — pluggable authentication provider framework.

This subpackage defines the abstract interfaces and built-in
implementations for authentication providers.
"""

from __future__ import annotations

from hecate.auth.api_key_provider import APIKeyAuthProvider
from hecate.auth.jwt_provider import JWTAuthProvider
from hecate.auth.provider import AuthProviderABC

__all__ = [
    "APIKeyAuthProvider",
    "AuthProviderABC",
    "JWTAuthProvider",
]
