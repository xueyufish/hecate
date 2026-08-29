"""Tests for SecretProvider — abstractness, provider initialization."""

from __future__ import annotations

import pytest

from hecate.vault.provider import SecretProvider


class TestSecretProvider:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            SecretProvider()  # type: ignore[abstract]


class TestHashiCorpVaultProvider:
    def test_name(self) -> None:
        from hecate_enterprise.vault.hcvault_provider import HashiCorpVaultProvider

        provider = HashiCorpVaultProvider(vault_url="http://localhost:8200")
        assert provider.name == "hcvault"

    def test_description(self) -> None:
        from hecate_enterprise.vault.hcvault_provider import HashiCorpVaultProvider

        provider = HashiCorpVaultProvider(vault_url="http://localhost:8200")
        assert "Vault" in provider.description


class TestAWSSecretsManagerProvider:
    def test_name(self) -> None:
        from hecate_enterprise.vault.aws_provider import AWSSecretsManagerProvider

        provider = AWSSecretsManagerProvider(region_name="us-east-1")
        assert provider.name == "aws"

    def test_description(self) -> None:
        from hecate_enterprise.vault.aws_provider import AWSSecretsManagerProvider

        provider = AWSSecretsManagerProvider(region_name="us-east-1")
        assert "AWS" in provider.description


class TestAzureKeyVaultProvider:
    def test_name(self) -> None:
        from hecate_enterprise.vault.azure_provider import AzureKeyVaultProvider

        provider = AzureKeyVaultProvider(vault_url="https://test.vault.azure.net")
        assert provider.name == "azure"

    def test_description(self) -> None:
        from hecate_enterprise.vault.azure_provider import AzureKeyVaultProvider

        provider = AzureKeyVaultProvider(vault_url="https://test.vault.azure.net")
        assert "Azure" in provider.description
