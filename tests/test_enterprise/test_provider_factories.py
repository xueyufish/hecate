"""Enterprise provider factory unit tests (PR1.2).

Each enterprise provider module exposes a zero-arg ``provider()`` factory
that reads its own settings and returns an instance, or ``None`` when
unconfigured. The host (hecate.auth.resolver / hecate.vault.resolver)
skips ``None``.

These tests verify the contract without setting environment variables
(monkeypatch settings attributes instead — pydantic-settings reads them
directly).
"""

from __future__ import annotations

import pytest

from hecate.core.config import settings

# ---- auth factories ----


def test_oidc_provider_returns_none_when_unconfigured() -> None:
    from hecate_enterprise.auth.oidc_provider import provider

    # Default: no SSO_OIDC_* set → factory returns None (skipped by host).
    assert provider() is None


def test_oidc_provider_returns_instance_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate_enterprise.auth.oidc_provider import OIDCAuthProvider, provider

    monkeypatch.setattr(settings, "SSO_OIDC_CLIENT_ID", "client-abc")
    monkeypatch.setattr(settings, "SSO_OIDC_CLIENT_SECRET", "secret-xyz")
    monkeypatch.setattr(settings, "SSO_OIDC_DISCOVERY_URL", "https://idp.example.com")
    monkeypatch.setattr(settings, "SSO_OIDC_SCOPE", "openid profile email")

    instance = provider()
    assert isinstance(instance, OIDCAuthProvider)
    # Construction is the contract; private attributes hold the values.
    assert instance.name == "oidc"


def test_saml_provider_returns_none_when_partial_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial config (only one of two required fields) is treated as unconfigured."""
    from hecate_enterprise.auth.saml_provider import provider

    monkeypatch.setattr(settings, "SSO_SAML_SP_ENTITY_ID", "sp-1")
    # SSO_SAML_IDP_SSO_URL still unset
    assert provider() is None


def test_ldap_provider_returns_instance_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate_enterprise.auth.ldap_provider import LDAPAuthProvider, provider

    monkeypatch.setattr(settings, "SSO_LDAP_SERVER_URL", "ldap://ldap.example.com")
    monkeypatch.setattr(settings, "SSO_LDAP_BASE_DN", "dc=example,dc=com")
    monkeypatch.setattr(settings, "SSO_LDAP_BIND_DN", "cn=admin,dc=example,dc=com")
    monkeypatch.setattr(settings, "SSO_LDAP_BIND_PASSWORD", "pw")
    monkeypatch.setattr(settings, "SSO_LDAP_SEARCH_FILTER", "(uid={username})")
    monkeypatch.setattr(settings, "SSO_LDAP_USE_SSL", True)

    instance = provider()
    assert isinstance(instance, LDAPAuthProvider)
    assert instance.name == "ldap"


# ---- vault factories ----


def test_hcvault_provider_returns_none_when_unconfigured() -> None:
    from hecate_enterprise.vault.hcvault_provider import provider

    assert provider() is None


def test_aws_provider_returns_instance_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate_enterprise.vault.aws_provider import AWSSecretsManagerProvider, provider

    monkeypatch.setattr(settings, "AWS_SECRETS_REGION", "us-east-1")
    monkeypatch.setattr(settings, "AWS_SECRETS_ACCESS_KEY_ID", "AKIA...")
    monkeypatch.setattr(settings, "AWS_SECRETS_SECRET_ACCESS_KEY", "secret")

    instance = provider()
    assert isinstance(instance, AWSSecretsManagerProvider)
    assert instance._region_name == "us-east-1"


def test_azure_provider_returns_instance_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate_enterprise.vault.azure_provider import AzureKeyVaultProvider, provider

    monkeypatch.setattr(settings, "AZURE_KEYVAULT_URL", "https://vault.azure.net")

    instance = provider()
    assert isinstance(instance, AzureKeyVaultProvider)
    assert instance._vault_url == "https://vault.azure.net"


# ---- scanner gate (the future license-check hook) ----


def test_load_entry_point_providers_respects_gate() -> None:
    """The ``gate`` parameter is the future license-check hook (PR1.2: always None)."""
    from hecate.auth.resolver import load_entry_point_providers

    # Gate returning False short-circuits the scan entirely.
    assert load_entry_point_providers(gate=lambda: False) == []

    # Gate returning True (or None) proceeds with the scan.
    with_no_gate = load_entry_point_providers()
    with_open_gate = load_entry_point_providers(gate=lambda: True)
    # Without configuring any provider, both return the same empty list
    # (the entries point at provider() factories that return None).
    assert with_no_gate == with_open_gate == []
