from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class AgentModel(BaseModel):
    """ORM model for agents — the core entity representing an AI agent configuration."""

    __tablename__ = "agents"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    persona: Mapped[str | None] = mapped_column(nullable=True)
    model_config_db: Mapped[dict] = mapped_column("model_config", JSON, nullable=False, default=dict)
    mode: Mapped[str] = mapped_column(String(50), nullable=False, default="chat")
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    tools: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    knowledge_base_ids: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")

    __table_args__ = (Index("idx_agents_workspace", "workspace_id", postgresql_where=BaseModel.deleted_at.is_(None)),)


class AgentCreateSchema(PydanticBase):
    """Schema for creating a new agent."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(..., min_length=1, max_length=255)
    persona: str | None = None
    llm_config: dict = Field(
        ...,
        alias="model_config",
        description="LLM model config with model name, temperature, etc.",
    )
    mode: str = Field(default="chat", pattern="^(chat|three_layer|workflow)$")
    workflow_id: uuid.UUID | None = None
    tools: list = Field(default_factory=list)
    skills: list = Field(default_factory=list)
    knowledge_base_ids: list = Field(default_factory=list)
    risk_level: str = Field(default="LOW")


class AgentUpdateSchema(PydanticBase):
    """Schema for updating an existing agent. All fields are optional."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    persona: str | None = None
    llm_config: dict | None = Field(None, alias="model_config")
    mode: str | None = Field(None, pattern="^(chat|three_layer|workflow)$")
    tools: list | None = None
    skills: list | None = None
    knowledge_base_ids: list | None = None
    risk_level: str | None = None


class AgentReadSchema(PydanticBase):
    """Schema for reading agent data, populated from ORM model attributes."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    persona: str | None
    model_config_db: dict = Field(serialization_alias="model_config")
    mode: str
    workflow_id: uuid.UUID | None
    tools: list
    skills: list
    knowledge_base_ids: list
    risk_level: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
