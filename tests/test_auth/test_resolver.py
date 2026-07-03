"""Tests for auth resolver — provider iteration and fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hecate.auth.provider import AuthProviderABC
from hecate.auth.resolver import get_registered_providers, register_auth_providers, resolve_auth_context
from hecate.core.auth_context import AuthContext


class _MockProvider(AuthProviderABC):
    def __init__(self, name: str, return_value: AuthContext | None) -> None:
        self._name = name
        self._return_value = return_value

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Mock {self._name}"

    async def authenticate(self, token: str, db: object) -> AuthContext | None:
        return self._return_value


@pytest.fixture(autouse=True)
def _clear_providers() -> None:
    """Clear registered providers before each test."""
    register_auth_providers()
    yield
    register_auth_providers()


class TestResolver:
    async def test_first_provider_succeeds(self) -> None:
        import uuid

        ctx = AuthContext(
            user_id=uuid.UUID(int=0),
            org_id=None,
            workspace_id=None,
            role=None,
            auth_method="jwt",
            api_key_scope=None,
        )
        register_auth_providers(
            _MockProvider("jwt", ctx),
            _MockProvider("api_key", None),
        )

        credentials = MagicMock()
        credentials.credentials = "valid-token"
        result = await resolve_auth_context(credentials, None)  # type: ignore[arg-type]
        assert result.auth_method == "jwt"

    async def test_second_provider_succeeds(self) -> None:
        import uuid

        ctx = AuthContext(
            user_id=uuid.UUID(int=0),
            org_id=None,
            workspace_id=None,
            role=None,
            auth_method="api_key",
            api_key_scope="system",
        )
        register_auth_providers(
            _MockProvider("jwt", None),
            _MockProvider("api_key", ctx),
        )

        credentials = MagicMock()
        credentials.credentials = "valid-key"
        result = await resolve_auth_context(credentials, None)  # type: ignore[arg-type]
        assert result.auth_method == "api_key"

    async def test_all_providers_fail_raises_401(self) -> None:
        register_auth_providers(
            _MockProvider("jwt", None),
            _MockProvider("api_key", None),
        )

        credentials = MagicMock()
        credentials.credentials = "invalid"
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await resolve_auth_context(credentials, None)  # type: ignore[arg-type]

    def test_get_registered_providers(self) -> None:
        p1 = _MockProvider("jwt", None)
        p2 = _MockProvider("api_key", None)
        register_auth_providers(p1, p2)
        providers = get_registered_providers()
        assert len(providers) == 2
        assert providers[0].name == "jwt"
