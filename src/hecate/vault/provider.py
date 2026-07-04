"""SecretProviderABC — abstract interface for secret management providers.

Follows the same ABC pattern as AuthProviderABC and ChannelABC.
"""

from __future__ import annotations

import abc


class SecretProviderABC(abc.ABC):
    """Abstract base class for secret management providers.

    Subclasses must implement name, description, get_secret,
    get_dynamic_credentials, and health_check.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Return the provider name (e.g., 'hcvault', 'aws', 'azure')."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Return a human-readable description of the provider."""

    @abc.abstractmethod
    async def get_secret(self, path: str) -> str | None:
        """Retrieve a static secret by path.

        Args:
            path: Secret path (e.g., 'secret/myapp/api-key').

        Returns:
            Secret value, or None if not found.
        """

    @abc.abstractmethod
    async def get_dynamic_credentials(self, role: str) -> dict[str, str] | None:
        """Request dynamic short-lived credentials.

        Args:
            role: Role identifier for credential generation.

        Returns:
            Dict with 'username', 'password', and optionally 'lease_id',
            'lease_duration', or None if not supported.
        """

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is reachable and healthy.

        Returns:
            True if healthy, False otherwise.
        """
