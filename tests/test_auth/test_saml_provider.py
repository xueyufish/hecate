"""Tests for SAMLAuthProvider — initialization, error handling."""

from __future__ import annotations

import pytest

from hecate.auth.provider import AuthProviderABC
from hecate.auth.saml_provider import SAMLAuthProvider


class TestSAMLAuthProvider:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            AuthProviderABC()  # type: ignore[abstract]

    def test_name(self) -> None:
        provider = SAMLAuthProvider(
            sp_entity_id="https://example.com/sp",
            sp_acs_url="https://example.com/acs",
            idp_entity_id="https://idp.example.com",
            idp_sso_url="https://idp.example.com/sso",
            idp_x509_cert="test-cert",
        )
        assert provider.name == "saml"

    def test_description(self) -> None:
        provider = SAMLAuthProvider(
            sp_entity_id="https://example.com/sp",
            sp_acs_url="https://example.com/acs",
            idp_entity_id="https://idp.example.com",
            idp_sso_url="https://idp.example.com/sso",
            idp_x509_cert="test-cert",
        )
        assert "SAML" in provider.description

    def test_get_saml_settings(self) -> None:
        provider = SAMLAuthProvider(
            sp_entity_id="https://example.com/sp",
            sp_acs_url="https://example.com/acs",
            idp_entity_id="https://idp.example.com",
            idp_sso_url="https://idp.example.com/sso",
            idp_x509_cert="test-cert",
        )
        settings = provider.get_saml_settings()
        assert settings["sp"]["entityId"] == "https://example.com/sp"
        assert settings["idp"]["entityId"] == "https://idp.example.com"

    async def test_invalid_response_returns_none(self) -> None:
        provider = SAMLAuthProvider(
            sp_entity_id="https://example.com/sp",
            sp_acs_url="https://example.com/acs",
            idp_entity_id="https://idp.example.com",
            idp_sso_url="https://idp.example.com/sso",
            idp_x509_cert="test-cert",
        )
        result = await provider.authenticate("invalid-base64", None)  # type: ignore[arg-type]
        assert result is None
