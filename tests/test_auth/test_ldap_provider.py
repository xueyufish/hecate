"""Tests for LDAPAuthProvider — initialization, error handling."""

from __future__ import annotations

import pytest

from hecate.auth.ldap_provider import LDAPAuthProvider
from hecate.auth.provider import AuthProviderABC


class TestLDAPAuthProvider:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            AuthProviderABC()  # type: ignore[abstract]

    def test_name(self) -> None:
        provider = LDAPAuthProvider(
            server_url="ldap://localhost",
            base_dn="dc=example,dc=com",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="password",  # noqa: S106
        )
        assert provider.name == "ldap"

    def test_description(self) -> None:
        provider = LDAPAuthProvider(
            server_url="ldap://localhost",
            base_dn="dc=example,dc=com",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="password",  # noqa: S106
        )
        assert "LDAP" in provider.description

    async def test_invalid_credentials_format_returns_none(self) -> None:
        provider = LDAPAuthProvider(
            server_url="ldap://localhost",
            base_dn="dc=example,dc=com",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="password",  # noqa: S106
        )
        # Not base64 encoded
        result = await provider.authenticate("not-base64", None)  # type: ignore[arg-type]
        assert result is None

    async def test_empty_password_returns_none(self) -> None:
        import base64

        provider = LDAPAuthProvider(
            server_url="ldap://localhost",
            base_dn="dc=example,dc=com",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="password",  # noqa: S106
        )
        creds = base64.b64encode(b"user:").decode()
        result = await provider.authenticate(creds, None)  # type: ignore[arg-type]
        assert result is None

    async def test_missing_colon_returns_none(self) -> None:
        import base64

        provider = LDAPAuthProvider(
            server_url="ldap://localhost",
            base_dn="dc=example,dc=com",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="password",  # noqa: S106
        )
        creds = base64.b64encode(b"nocolon").decode()
        result = await provider.authenticate(creds, None)  # type: ignore[arg-type]
        assert result is None
