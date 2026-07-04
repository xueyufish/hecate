"""AzureKeyVaultProvider — secret management via Azure Key Vault.

Uses azure-keyvault-secrets with DefaultAzureCredential for
authentication (supports managed identity, CLI, environment).
"""

from __future__ import annotations

import logging

from hecate.vault.provider import SecretProviderABC

logger = logging.getLogger(__name__)


class AzureKeyVaultProvider(SecretProviderABC):
    """Secret provider backed by Azure Key Vault.

    Uses DefaultAzureCredential for authentication which supports
    managed identity, Azure CLI, environment variables, and more.
    """

    def __init__(self, vault_url: str) -> None:
        self._vault_url = vault_url

    @property
    def name(self) -> str:
        return "azure"

    @property
    def description(self) -> str:
        return "Azure Key Vault secret provider"

    async def get_secret(self, path: str) -> str | None:
        """Read a secret from Azure Key Vault."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self._vault_url, credential=credential)
            secret = client.get_secret(path)
            return secret.value
        except Exception:
            logger.warning("Azure: failed to read secret %s", path, exc_info=True)
            return None

    async def get_dynamic_credentials(self, role: str) -> dict[str, str] | None:
        """Azure Key Vault does not support dynamic credentials."""
        return None

    async def health_check(self) -> bool:
        """Check Azure Key Vault connectivity."""
        try:
            from azure.identity import DefaultAzureCredential
            from azure.keyvault.secrets import SecretClient

            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=self._vault_url, credential=credential)
            # Try to list properties (requires read permission)
            list(client.list_properties_of_secrets(max_results=1))
            return True
        except Exception:
            return False
