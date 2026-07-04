"""Tests for secret resolver — caching, fallback, dynamic credentials."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hecate.vault.resolver import clear_cache, register_providers, resolve_dynamic_credentials, resolve_secret


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_cache()
    yield
    clear_cache()


class TestResolveSecret:
    async def test_fallback_to_settings(self) -> None:
        register_providers()
        result = await resolve_secret("jwt/secret")
        # Should return from Settings fallback
        assert result is not None

    async def test_provider_returns_value(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.get_secret.return_value = "vault-secret"
        mock_provider.name = "mock"
        register_providers(mock_provider)

        result = await resolve_secret("my/secret")
        assert result == "vault-secret"

    async def test_caching(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.get_secret.return_value = "cached-value"
        mock_provider.name = "mock"
        register_providers(mock_provider)

        result1 = await resolve_secret("cached/secret")
        result2 = await resolve_secret("cached/secret")
        assert result1 == "cached-value"
        assert result2 == "cached-value"
        assert mock_provider.get_secret.call_count == 1

    async def test_no_provider_returns_none(self) -> None:
        register_providers()
        result = await resolve_secret("nonexistent/path/that/does/not/exist")
        assert result is None


class TestResolveDynamicCredentials:
    async def test_returns_credentials(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.get_dynamic_credentials.return_value = {"username": "u", "password": "p"}
        mock_provider.name = "mock"
        register_providers(mock_provider)

        result = await resolve_dynamic_credentials("db-role")
        assert result == {"username": "u", "password": "p"}

    async def test_no_provider_returns_none(self) -> None:
        register_providers()
        result = await resolve_dynamic_credentials("nonexistent-role")
        assert result is None
