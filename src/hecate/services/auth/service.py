"""Authentication service handling registration, login, and token management."""

from __future__ import annotations

import logging
from uuid import UUID

from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.user import UserModel
from hecate.services.auth.password import hash_password, verify_password
from hecate.services.auth.token import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Handles user registration, login, and token operations."""

    async def register(self, db: AsyncSession, email: str, password: str) -> UserModel:
        """Register a new user.

        Args:
            db: Async database session.
            email: User's email address.
            password: Plaintext password (will be hashed).

        Returns:
            The newly created UserModel.

        Raises:
            ValueError: If email is already registered.
        """
        existing = await db.execute(select(UserModel).where(UserModel.email == email))
        if existing.scalar_one_or_none() is not None:
            raise ValueError("Email already registered")

        user = UserModel(
            email=email,
            hashed_password=hash_password(password),
        )
        db.add(user)
        await db.flush()
        logger.info("User registered: %s", email)
        return user

    async def login(self, db: AsyncSession, email: str, password: str) -> tuple[UserModel, str, str]:
        """Authenticate a user and issue tokens.

        Args:
            db: Async database session.
            email: User's email address.
            password: Plaintext password to verify.

        Returns:
            Tuple of (user, access_token, refresh_token).

        Raises:
            ValueError: If credentials are invalid.
        """
        result = await db.execute(select(UserModel).where(UserModel.email == email))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(password, user.hashed_password):
            raise ValueError("Invalid credentials")

        access_token = create_access_token(user.id)
        refresh_token = create_refresh_token(user.id)
        logger.info("User logged in: %s", email)
        return user, access_token, refresh_token

    async def refresh_tokens(self, db: AsyncSession, refresh_token: str) -> tuple[str, str]:
        """Issue new tokens from a valid refresh token.

        Args:
            db: Async database session.
            refresh_token: The refresh token to validate.

        Returns:
            Tuple of (new_access_token, new_refresh_token).

        Raises:
            ValueError: If the refresh token is invalid or user not found.
        """
        try:
            payload = decode_refresh_token(refresh_token)
        except JWTError as exc:
            raise ValueError("Invalid refresh token") from exc

        user_id = UUID(payload["sub"])
        result = await db.execute(select(UserModel).where(UserModel.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError("User not found")

        new_access = create_access_token(user.id)
        new_refresh = create_refresh_token(user.id)
        return new_access, new_refresh

    async def get_user_by_id(self, db: AsyncSession, user_id: UUID) -> UserModel | None:
        """Look up a user by ID.

        Args:
            db: Async database session.
            user_id: The user's UUID.

        Returns:
            The UserModel or None if not found.
        """
        result = await db.execute(select(UserModel).where(UserModel.id == user_id))
        return result.scalar_one_or_none()
