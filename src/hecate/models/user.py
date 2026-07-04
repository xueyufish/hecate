"""User ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for user accounts,
supporting email/password authentication with bcrypt-hashed passwords.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, EmailStr, Field
from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class UserModel(BaseModel):
    """ORM model for users — supports email/password authentication.

    The optional ``sso_id`` field stores the external identity provider's
    user identifier for future SSO integration (OIDC/SAML). It is not used
    by the local auth flow.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    sso_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, unique=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None, index=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    given_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    family_name: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preferred_locale: Mapped[str | None] = mapped_column(String(10), nullable=True, default=None)

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
    sso_id: str | None = None
    external_id: str | None = None
    display_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    active: bool = True
    created_at: datetime


class TokenResponseSchema(PydanticBase):
    """Schema for JWT token response."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105


class LoginResponseSchema(PydanticBase):
    """Schema for login response with workspace context."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    workspaces: list[dict[str, str]] = []


class SwitchWorkspaceSchema(PydanticBase):
    """Schema for workspace switching request."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID


class RefreshTokenSchema(PydanticBase):
    """Schema for token refresh request."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str
