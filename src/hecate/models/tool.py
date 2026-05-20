from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ToolModel(BaseModel):
    """ORM model for tools — defines callable tools from builtin, custom, or MCP sources."""

    __tablename__ = "tools"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    returns: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    mcp_server: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mcp_tool_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index(
            "idx_tools_workspace_name",
            "workspace_id",
            "name",
            unique=True,
            postgresql_where=BaseModel.deleted_at.is_(None),
        ),
    )


class ToolCreateSchema(PydanticBase):
    """Schema for creating a new tool."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str
    source: str = Field(..., pattern="^(builtin|custom|mcp)$")
    parameters: dict
    returns: dict | None = None
    risk_level: str = "LOW"
    approval_required: bool = False
    mcp_server: str | None = None
    mcp_tool_name: str | None = None


class ToolReadSchema(PydanticBase):
    """Schema for reading tool data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str
    source: str
    parameters: dict
    returns: dict | None
    risk_level: str
    approval_required: bool
    mcp_server: str | None
    mcp_tool_name: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
