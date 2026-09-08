"""Gateway target ORM model and Pydantic schemas.

Defines the persistence layer for MCP Gateway targets — upstream systems
(REST/OpenAPI services or external MCP servers) whose tools are federated
through the ``/mcp`` endpoint. Target credentials are stored server-side
and MUST never leave the service layer unredacted (see
``GatewayService.redact_credentials``).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import JSON, Enum, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class GatewayTargetKind(enum.StrEnum):
    """Kind of upstream system behind a gateway target."""

    REST = "rest"
    MCP = "mcp"


class GatewayTargetModel(BaseModel):
    """ORM model for gateway targets — upstream tool sources.

    A target is a named, workspace-owned registration of an upstream
    system. For ``rest`` targets the OpenAPI document is stored verbatim
    in ``spec``; for ``mcp`` targets ``spec`` stays empty and the
    endpoint is managed through the MCP connection manager. The
    ``base_url`` is pinned at registration time and is the only URL the
    executor ever contacts (spec-level ``servers`` overrides are
    ignored).
    """

    __tablename__ = "gateway_targets"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[GatewayTargetKind] = mapped_column(
        Enum(
            GatewayTargetKind,
            name="gateway_target_kind",
            create_constraint=True,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    credentials: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    __table_args__ = (Index("idx_gateway_targets_workspace_name", "workspace_id", "name", unique=True),)


class GatewayTargetCreateSchema(PydanticBase):
    """Payload for registering a gateway target."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: GatewayTargetKind
    base_url: str = Field(min_length=1, max_length=1024)
    spec: dict | None = None
    credentials: dict = Field(default_factory=dict)
    workspace_id: uuid.UUID | None = None


class GatewayTargetUpdateSchema(PydanticBase):
    """Payload for updating a gateway target (partial)."""

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = Field(default=None, min_length=1, max_length=1024)
    spec: dict | None = None
    credentials: dict | None = None
    is_active: bool | None = None


class GatewayTargetReadSchema(PydanticBase):
    """Gateway target as returned by management APIs — credentials redacted."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    kind: GatewayTargetKind
    base_url: str
    credentials: dict
    workspace_id: uuid.UUID | None
    is_active: bool
    created_at: datetime
