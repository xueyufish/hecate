"""AWSSecretsManagerProvider — secret management via AWS Secrets Manager.

Uses aiobotocore for async AWS operations including GetSecretValue
and STS AssumeRole for dynamic credentials.
"""

from __future__ import annotations

import logging

from hecate.vault.provider import SecretProviderABC

logger = logging.getLogger(__name__)


class AWSSecretsManagerProvider(SecretProviderABC):
    """Secret provider backed by AWS Secrets Manager.

    Supports secret reads and STS AssumeRole for dynamic credentials.
    """

    def __init__(
        self,
        region_name: str,
        access_key_id: str = "",
        secret_access_key: str = "",
    ) -> None:
        self._region_name = region_name
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key

    @property
    def name(self) -> str:
        return "aws"

    @property
    def description(self) -> str:
        return "AWS Secrets Manager secret provider"

    async def get_secret(self, path: str) -> str | None:
        """Read a secret from AWS Secrets Manager."""
        try:
            import aiobotocore.session

            session = aiobotocore.session.get_session()
            kwargs = {"region_name": self._region_name}
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key

            async with session.create_client("secretsmanager", **kwargs) as client:
                response = await client.get_secret_value(SecretId=path)
                return response.get("SecretString")
        except Exception:
            logger.warning("AWS: failed to read secret %s", path, exc_info=True)
            return None

    async def get_dynamic_credentials(self, role: str) -> dict[str, str] | None:
        """Request dynamic STS credentials via AssumeRole."""
        try:
            import aiobotocore.session

            session = aiobotocore.session.get_session()
            kwargs = {"region_name": self._region_name}
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key

            async with session.create_client("sts", **kwargs) as client:
                response = await client.assume_role(
                    RoleArn=role,
                    RoleSessionName="hecate-session",
                )
                creds = response.get("Credentials", {})
                return {
                    "access_key": creds.get("AccessKeyId", ""),
                    "secret_key": creds.get("SecretAccessKey", ""),
                    "session_token": creds.get("SessionToken", ""),
                    "expiration": str(creds.get("Expiration", "")),
                }
        except Exception:
            logger.warning("AWS: failed to assume role %s", role, exc_info=True)
            return None

    async def health_check(self) -> bool:
        """Check AWS connectivity by listing secrets."""
        try:
            import aiobotocore.session

            session = aiobotocore.session.get_session()
            kwargs = {"region_name": self._region_name}
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key

            async with session.create_client("secretsmanager", **kwargs) as client:
                await client.list_secrets(MaxResults=1)
                return True
        except Exception:
            return False
