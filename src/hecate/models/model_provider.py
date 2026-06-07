"""Model Provider ORM models and Pydantic schemas.

Defines the persistence layer and API schemas for model providers and
the model registry — enabling database-backed provider management
with encrypted API key storage.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ModelProviderModel(BaseModel):
    """ORM model for model providers (OpenAI, ZhiPu, DeepSeek, etc.)."""

    __tablename__ = "model_providers"

    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="inactive")

    __table_args__ = (Index("idx_model_providers_name", "name", "deleted"),)


class ModelRegistryModel(BaseModel):
    """ORM model for registered models — linked to a provider."""

    __tablename__ = "model_registry"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("model_providers.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_type: Mapped[str] = mapped_column(String(50), nullable=False, default="chat")
    capabilities: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_context: Mapped[int | None] = mapped_column(nullable=True)
    is_custom: Mapped[bool] = mapped_column(nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = (
        Index(
            "uq_model_registry_provider_model",
            "provider_id",
            "model_id",
            "deleted",
            "deleted_at",
            unique=True,
        ),
        Index(
            "idx_model_registry_provider",
            "provider_id",
            "deleted",
        ),
        Index(
            "idx_model_registry_model_id",
            "model_id",
            "deleted",
        ),
    )


class ModelProviderCreateSchema(PydanticBase):
    """Schema for creating a new model provider."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=255)
    api_key: str = Field(..., min_length=1)
    base_url: str | None = Field(None, max_length=512)
    config: dict = Field(default_factory=dict)
    is_enabled: bool = True


class ModelProviderUpdateSchema(PydanticBase):
    """Schema for updating a model provider. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(None, min_length=1, max_length=255)
    api_key: str | None = Field(None, min_length=1)
    base_url: str | None = Field(None, max_length=512)
    config: dict | None = None
    is_enabled: bool | None = None


class ModelProviderReadSchema(PydanticBase):
    """Schema for reading model provider data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    display_name: str
    base_url: str | None
    config: dict
    is_enabled: bool
    status: str
    created_at: datetime
    updated_at: datetime


class ModelRegistryReadSchema(PydanticBase):
    """Schema for reading model registry data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    model_id: str
    display_name: str
    model_type: str
    capabilities: dict
    max_context: int | None
    is_custom: bool
    is_enabled: bool
    created_at: datetime


class ModelUpdateSchema(PydanticBase):
    """Schema for updating a registered model."""

    model_config = ConfigDict(extra="forbid")

    is_enabled: bool | None = None
    display_name: str | None = None


class CustomModelCreateSchema(PydanticBase):
    """Schema for manually adding a custom model to a provider."""

    model_config = ConfigDict(extra="forbid")

    provider_id: uuid.UUID
    model_id: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=255)


class ModelTestRequestSchema(PydanticBase):
    """Schema for model testing request."""

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=100, ge=1)
