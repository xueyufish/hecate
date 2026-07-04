"""Tests for OIDCAuthProvider — initialization, JIT provisioning, error handling."""

from __future__ import annotations

import pytest

from hecate.auth.oidc_provider import OIDCAuthProvider
from hecate.auth.provider import AuthProviderABC


class TestOIDCAuthProvider:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            AuthProviderABC()  # type: ignore[abstract]

    def test_name(self) -> None:
        provider = OIDCAuthProvider(
            client_id="test",
            client_secret="test",  # noqa: S106
            discovery_url="https://example.com/.well-known/openid-configuration",
        )
        assert provider.name == "oidc"

    def test_description(self) -> None:
        provider = OIDCAuthProvider(
            client_id="test",
            client_secret="test",  # noqa: S106
            discovery_url="https://example.com/.well-known/openid-configuration",
        )
        assert "OpenID Connect" in provider.description

    async def test_invalid_token_returns_none(self) -> None:
        provider = OIDCAuthProvider(
            client_id="test",
            client_secret="test",  # noqa: S106
            discovery_url="https://example.com/.well-known/openid-configuration",
        )
        result = await provider.authenticate("invalid-token", None)  # type: ignore[arg-type]
        assert result is None

    async def test_empty_token_returns_none(self) -> None:
        provider = OIDCAuthProvider(
            client_id="test",
            client_secret="test",  # noqa: S106
            discovery_url="https://example.com/.well-known/openid-configuration",
        )
        result = await provider.authenticate("", None)  # type: ignore[arg-type]
        assert result is None
