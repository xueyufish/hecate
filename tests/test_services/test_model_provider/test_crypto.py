"""Tests for API Key encryption/decryption."""

from __future__ import annotations

from hecate.services.model_provider.crypto import decrypt_api_key, encrypt_api_key


class TestEncryptDecrypt:
    def test_roundtrip_with_fernet(self, monkeypatch) -> None:
        monkeypatch.setenv("FERNET_KEY", "fhoveHULDckhD62esNJUIO55BgGvk8VAClZOgj9vkNQ=")
        original = "sk-test-api-key-12345"
        encrypted = encrypt_api_key(original)
        assert encrypted != original
        decrypted = decrypt_api_key(encrypted)
        assert decrypted == original

    def test_plaintext_without_fernet(self, monkeypatch) -> None:
        monkeypatch.setenv("FERNET_KEY", "")
        original = "sk-test-api-key-12345"
        assert encrypt_api_key(original) == original
        assert decrypt_api_key(original) == original
