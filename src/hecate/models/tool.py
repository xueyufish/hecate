"""Tool ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for tools — callable functions
that agents can invoke at runtime. Tools originate from built-in
definitions, custom user code, or MCP (Model Context Protocol) server
discovery.
"""

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
    """ORM model for tools — defines callable tools from builtin, custom, or MCP sources.

    Key fields:

    - **source** — origin of the tool: ``"builtin"`` (shipped with Hecate,
      e.g. file search, web search), ``"custom"`` (user-defined via the
      API), or ``"mcp"`` (discovered from an MCP server at runtime).
    - **parameters** — JSON Schema describing the tool's input parameters.
    - **returns** — optional JSON Schema describing the tool's return value.
    - **risk_level** — qualitative risk classification used by the guard
      layer to evaluate whether the tool call should proceed.
    - **approval_required** — if ``True``, executing this tool triggers a
      human-in-the-loop interrupt: the agent pauses and waits for explicit
      user approval before the tool is invoked.
    - **mcp_server** / **mcp_tool_name** — for ``"mcp"`` source tools,
      these fields identify the originating MCP server and the tool name on
      that server. ``None`` for non-MCP tools.
    """

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
