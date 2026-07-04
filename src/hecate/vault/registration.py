"""Vault registration — register configured secret providers."""

from __future__ import annotations

import logging

from hecate.core.config import settings
from hecate.vault.resolver import register_providers

logger = logging.getLogger(__name__)


def register_secret_providers() -> int:
    """Register configured secret providers.

    Returns:
        Number of providers registered.
    """
    providers: list = []

    if settings.VAULT_URL:
        from hecate.vault.hcvault_provider import HashiCorpVaultProvider

        providers.append(
            HashiCorpVaultProvider(
                vault_url=settings.VAULT_URL,
                vault_token=settings.VAULT_TOKEN,
                vault_role_id=settings.VAULT_ROLE_ID,
                vault_secret_id=settings.VAULT_SECRET_ID,
                mount_point=settings.VAULT_MOUNT_POINT,
            )
        )

    if settings.AWS_SECRETS_REGION:
        from hecate.vault.aws_provider import AWSSecretsManagerProvider

        providers.append(
            AWSSecretsManagerProvider(
                region_name=settings.AWS_SECRETS_REGION,
                access_key_id=settings.AWS_SECRETS_ACCESS_KEY_ID,
                secret_access_key=settings.AWS_SECRETS_SECRET_ACCESS_KEY,
            )
        )

    if settings.AZURE_KEYVAULT_URL:
        from hecate.vault.azure_provider import AzureKeyVaultProvider

        providers.append(AzureKeyVaultProvider(vault_url=settings.AZURE_KEYVAULT_URL))

    register_providers(*providers)
    logger.info("Registered %d secret providers", len(providers))
    return len(providers)
