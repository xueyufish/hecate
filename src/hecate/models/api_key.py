"""API key ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for database-backed API keys
with scoping (system or workspace) and lifecycle management.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, DateTime, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class ApiKeyScope(enum.StrEnum):
    """Scoping levels for API keys."""

    SYSTEM = "system"
    WORKSPACE = "workspace"


class ApiKeyModel(BaseModel):
    """ORM model for API keys — database-backed, scoped authentication.

    Supports two scopes:
    - SYSTEM: cross-org platform admin access
    - WORKSPACE: single-workspace operations

    Keys are stored as SHA-256 hashes. The raw key is shown only once at
    creation time and never persisted in plaintext.
    """

    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    scope: Mapped[ApiKeyScope] = mapped_column(
        Enum(ApiKeyScope, name="api_key_scope", create_constraint=True),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    created_by: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    )

    __table_args__ = (
        Index("idx_api_keys_hash", "key_hash", unique=True),
        Index("idx_api_keys_created_by", "created_by"),
        Index("idx_api_keys_workspace", "workspace_id"),
    )


class ApiKeyCreateSchema(PydanticBase):
    """Schema for creating an API key."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    scope: ApiKeyScope
    workspace_id: uuid.UUID | None = None
    expires_at: datetime | None = None


class ApiKeyReadSchema(PydanticBase):
    """Schema for reading API key data (excludes the full key)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    scope: ApiKeyScope
    org_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    created_by: uuid.UUID
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class ApiKeyCreateResponseSchema(PydanticBase):
    """Schema for API key creation response (includes full key once)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key: str
    key_prefix: str
    scope: ApiKeyScope
    org_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    created_at: datetime
