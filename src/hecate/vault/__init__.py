"""Vault — secret provider abstraction for enterprise secret management."""

from __future__ import annotations

from hecate.vault.provider import SecretProviderABC
from hecate.vault.resolver import resolve_dynamic_credentials, resolve_secret

__all__ = [
    "SecretProviderABC",
    "resolve_dynamic_credentials",
    "resolve_secret",
]
