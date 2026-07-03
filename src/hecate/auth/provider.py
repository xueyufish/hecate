"""AuthProviderABC — abstract interface for authentication providers.

All auth providers — built-in or third-party — must implement this
interface to be registered with the PluginRegistry under type="auth_provider".
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext


class AuthProviderABC(ABC):
    """Abstract base class for authentication providers.

    Each provider attempts to authenticate a raw token and returns
    an :class:`AuthContext` on success or ``None`` on failure.

    Subclasses must define:

    - :pyattr:`name` — short identifier (e.g., ``"jwt"``, ``"api_key"``)
    - :pyattr:`description` — human-readable explanation
    - :pymeth:`authenticate` — async authentication logic

    Example::

        class JWTAuthProvider(AuthProviderABC):
            @property
            def name(self) -> str:
                return "jwt"

            @property
            def description(self) -> str:
                return "JWT access token authentication"

            async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
                try:
                    payload = decode_access_token(token)
                    return AuthContext(...)
                except JWTError:
                    return None
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this auth provider (e.g., ``"jwt"``, ``"api_key"``)."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of this auth provider."""
        ...

    @abstractmethod
    async def authenticate(self, token: str, db: AsyncSession) -> AuthContext | None:
        """Attempt to authenticate a raw token.

        Args:
            token: The raw token string (e.g., JWT, API key).
            db: Async database session for key lookups.

        Returns:
            An :class:`AuthContext` on success, or ``None`` if this
            provider cannot authenticate the token.
        """
        ...
