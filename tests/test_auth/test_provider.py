"""Tests for AuthProviderABC, JWTAuthProvider, APIKeyAuthProvider."""

from __future__ import annotations

import pytest

from hecate.auth.api_key_provider import APIKeyAuthProvider
from hecate.auth.jwt_provider import JWTAuthProvider
from hecate.auth.provider import AuthProviderABC


class TestAuthProviderABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            AuthProviderABC()  # type: ignore[abstract]


class TestJWTAuthProvider:
    def test_name(self) -> None:
        provider = JWTAuthProvider()
        assert provider.name == "jwt"

    def test_description(self) -> None:
        provider = JWTAuthProvider()
        assert "JWT" in provider.description

    async def test_invalid_token_returns_none(self) -> None:
        provider = JWTAuthProvider()
        result = await provider.authenticate("invalid-token", None)  # type: ignore[arg-type]
        assert result is None

    async def test_expired_token_returns_none(self) -> None:
        provider = JWTAuthProvider()
        # Create an expired token
        from datetime import UTC, datetime, timedelta
        from uuid import UUID

        from jose import jwt

        from hecate.core.config import settings

        secret = settings.api_keys_list[0] if settings.api_keys_list else "test-secret"
        payload = {
            "sub": str(UUID(int=0)),
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(hours=1),
            "iat": datetime.now(UTC) - timedelta(hours=2),
        }
        expired_token = jwt.encode(payload, secret, algorithm="HS256")
        result = await provider.authenticate(expired_token, None)  # type: ignore[arg-type]
        assert result is None


class TestAPIKeyAuthProvider:
    def test_name(self) -> None:
        provider = APIKeyAuthProvider()
        assert provider.name == "api_key"

    def test_description(self) -> None:
        provider = APIKeyAuthProvider()
        assert "API key" in provider.description
