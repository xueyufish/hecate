"""Fernet encryption/decryption helper for PII storage in mask_and_encrypt mode."""

from __future__ import annotations

import logging

from hecate.core.config import settings

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when required configuration is missing."""


def _get_fernet():
    """Lazy-load Fernet, raising ConfigurationError if key is missing."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as e:
        raise ConfigurationError("cryptography package not installed") from e

    key = settings.FERNET_KEY
    if not key:
        raise ConfigurationError("FERNET_KEY not configured; required for mask_and_encrypt mode")

    if isinstance(key, str):
        key = key.encode()

    try:
        return Fernet(key)
    except Exception as e:
        raise ConfigurationError(f"Invalid FERNET_KEY: {e}") from e


def encrypt_value(plaintext: str) -> bytes:
    """Encrypt a string value using Fernet.

    Args:
        plaintext: The original PII value to encrypt.

    Returns:
        Encrypted bytes.

    Raises:
        ConfigurationError: If FERNET_KEY is not set or invalid.
    """
    f = _get_fernet()
    return f.encrypt(plaintext.encode())


def decrypt_value(ciphertext: bytes) -> str:
    """Decrypt a Fernet-encrypted value back to plaintext.

    Args:
        ciphertext: The encrypted bytes.

    Returns:
        Decrypted plaintext string.

    Raises:
        ConfigurationError: If FERNET_KEY is not set or invalid.
    """
    f = _get_fernet()
    return f.decrypt(ciphertext).decode()


def validate_fernet_config() -> None:
    """Check that Fernet is properly configured.

    Raises:
        ConfigurationError: If FERNET_KEY is not set or cryptography not installed.
    """
    _get_fernet()
