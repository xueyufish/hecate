"""API Key encryption utilities using Fernet symmetric encryption.

Provides transparent encrypt/decrypt for API keys stored in the database.
When FERNET_KEY is not configured, operates in plaintext mode for development.
"""

from __future__ import annotations

from hecate.core.config import settings


def _get_fernet():
    """Create a Fernet instance from FERNET_KEY. Returns None if not set."""
    key = settings.FERNET_KEY
    if not key:
        return None

    from cryptography.fernet import Fernet

    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key for storage.

    Args:
        plaintext: The raw API key string.

    Returns:
        Encrypted string (or plaintext if FERNET_KEY is not set).
    """
    fernet = _get_fernet()
    if fernet is None:
        return plaintext
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_api_key(encrypted: str) -> str:
    """Decrypt a stored API key.

    Args:
        encrypted: The encrypted (or plaintext) API key string.

    Returns:
        Decrypted plaintext API key.
    """
    fernet = _get_fernet()
    if fernet is None:
        return encrypted
    return fernet.decrypt(encrypted.encode()).decode()
