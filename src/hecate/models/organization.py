"""Organization ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for organizations,
representing enterprise customers in the multi-tenant hierarchy.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel, JSONType


class OrganizationModel(BaseModel):
    """ORM model for organizations — represents an enterprise customer.

    Flat structure: no nested departments. Department hierarchy is managed
    by external OA/IAM systems and synced into Hecate as workspace mappings.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        index=True,
    )
    settings: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
        default=None,
    )

    __table_args__ = (
        Index("idx_orgs_slug_unique", "slug", unique=True),
        Index("idx_orgs_owner", "owner_id"),
    )


class OrganizationCreateSchema(PydanticBase):
    """Schema for creating an organization."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class OrganizationUpdateSchema(PydanticBase):
    """Schema for updating an organization."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    settings: dict[str, Any] | None = None


class OrganizationReadSchema(PydanticBase):
    """Schema for reading organization data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    owner_id: uuid.UUID
    settings: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
