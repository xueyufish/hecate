"""User ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for user accounts,
supporting email/password authentication with bcrypt-hashed passwords.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, EmailStr, Field
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class UserModel(BaseModel):
    """ORM model for users — supports email/password authentication."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (Index("idx_users_email_unique", "email", unique=True),)


class RegisterSchema(PydanticBase):
    """Schema for user registration."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginSchema(PydanticBase):
    """Schema for user login."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str


class UserReadSchema(PydanticBase):
    """Schema for reading user data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime


class TokenResponseSchema(PydanticBase):
    """Schema for JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105


class RefreshTokenSchema(PydanticBase):
    """Schema for token refresh request."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str
