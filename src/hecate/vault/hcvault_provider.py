"""HashiCorpVaultProvider — secret management via HashiCorp Vault.

Uses hvac client for KV v2 reads, database engine dynamic credentials,
and AppRole/token authentication.
"""

from __future__ import annotations

import logging

from hecate.vault.provider import SecretProviderABC

logger = logging.getLogger(__name__)


class HashiCorpVaultProvider(SecretProviderABC):
    """Secret provider backed by HashiCorp Vault.

    Supports KV v2 secret reads and database engine dynamic credentials.
    """

    def __init__(
        self,
        vault_url: str,
        vault_token: str = "",
        vault_role_id: str = "",
        vault_secret_id: str = "",
        mount_point: str = "secret",
    ) -> None:
        self._vault_url = vault_url
        self._vault_token = vault_token
        self._vault_role_id = vault_role_id
        self._vault_secret_id = vault_secret_id
        self._mount_point = mount_point
        self._client: object | None = None

    @property
    def name(self) -> str:
        return "hcvault"

    @property
    def description(self) -> str:
        return "HashiCorp Vault secret provider (KV v2 + database engine)"

    async def _get_client(self) -> object:
        """Get or create hvac client."""
        if self._client is not None:
            return self._client

        import hvac

        client = hvac.Client(url=self._vault_url)

        if self._vault_token:
            client.token = self._vault_token
        elif self._vault_role_id and self._vault_secret_id:
            client.auth.approle.login(
                role_id=self._vault_role_id,
                secret_id=self._vault_secret_id,
            )

        self._client = client
        return client

    async def get_secret(self, path: str) -> str | None:
        """Read a secret from Vault KV v2."""
        try:
            client = await self._get_client()
            response = client.secrets.kv.v2.read_secret_version(path=path, mount_point=self._mount_point)
            data = response.get("data", {}).get("data", {})
            # Return first value or the whole dict as string
            if len(data) == 1:
                return str(list(data.values())[0])
            return str(data)
        except Exception:
            logger.warning("Vault: failed to read secret %s", path, exc_info=True)
            return None

    async def get_dynamic_credentials(self, role: str) -> dict[str, str] | None:
        """Request dynamic database credentials from Vault."""
        try:
            client = await self._get_client()
            response = client.read(role)
            if response is None:
                return None
            return {
                "username": response.get("data", {}).get("username", ""),
                "password": response.get("data", {}).get("password", ""),
                "lease_id": response.get("lease_id", ""),
                "lease_duration": str(response.get("lease_duration", 0)),
            }
        except Exception:
            logger.warning("Vault: failed to get dynamic credentials for role %s", role, exc_info=True)
            return None

    async def health_check(self) -> bool:
        """Check Vault server health."""
        try:
            client = await self._get_client()
            return client.sys.is_initialized()
        except Exception:
            return False
