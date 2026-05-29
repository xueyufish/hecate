"""JWT token generation and verification.

Issues access tokens (short-lived, 30min) and refresh tokens (long-lived, 7d)
using python-jose with HS256 algorithm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt

from hecate.core.config import settings

_ACCESS_TOKEN_EXPIRE = timedelta(minutes=30)
_REFRESH_TOKEN_EXPIRE = timedelta(days=7)
_ALGORITHM = "HS256"

# Separate claims key to distinguish token types
_TOKEN_TYPE_CLAIM = "type"  # noqa: S105


def _get_secret() -> str:
    """Derive a JWT secret from configured API keys or generate a default."""
    keys = settings.api_keys_list
    if keys:
        return keys[0]
    return "hecate-default-jwt-secret-change-me"


def create_access_token(user_id: UUID) -> str:
    """Create a short-lived JWT access token.

    Args:
        user_id: The user's UUID to encode in the token subject.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "exp": now + _ACCESS_TOKEN_EXPIRE,
        "iat": now,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def create_refresh_token(user_id: UUID) -> str:
    """Create a long-lived JWT refresh token.

    Args:
        user_id: The user's UUID to encode in the token subject.

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": now + _REFRESH_TOKEN_EXPIRE,
        "iat": now,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate an access token.

    Args:
        token: The JWT string to decode.

    Returns:
        The decoded payload dictionary.

    Raises:
        JWTError: If the token is invalid, expired, or not an access token.
    """
    payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
    if payload.get(_TOKEN_TYPE_CLAIM) != "access":
        raise JWTError("Not an access token")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Decode and validate a refresh token.

    Args:
        token: The JWT string to decode.

    Returns:
        The decoded payload dictionary.

    Raises:
        JWTError: If the token is invalid, expired, or not a refresh token.
    """
    payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
    if payload.get(_TOKEN_TYPE_CLAIM) != "refresh":
        raise JWTError("Not a refresh token")
    return payload
