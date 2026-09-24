"""Agent ORM model and Pydantic schemas.

Defines the persistence layer (SQLAlchemy) and API schemas (Pydantic) for
agents — the central entity that configures an AI assistant's behaviour,
LLM settings, tools, skills, and knowledge bases.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel
from hecate.models.workflow import EvaluationGateConfigSchema


class AgentModel(BaseModel):
    """ORM model for agents — the core entity representing an AI agent configuration.

    Key fields:

    - **workspace_id** — tenant scope. Defaults to the zero UUID for P1
      single-workspace mode; reserved for P3 multi-tenancy support.
    - **model_config_db** — JSONB column (column name ``model_config``)
      storing the LLM configuration such as model name, temperature,
      max tokens, etc.
    - **mode** — execution mode: ``"chat"`` for a single LLM,
      ``"three_layer"`` for the Guard → Planner → Sub-Agent pipeline,
      ``"workflow"`` for a custom directed graph.
    - **workflow_id** — references the graph definition when mode is
      ``"workflow"``; ``None`` otherwise.
    - **tools** / **skills** / **knowledge_base_ids** — lists of associated
      resource IDs that the agent can access at runtime.
    - **risk_level** — qualitative risk classification (e.g. ``"LOW"``,
      ``"MEDIUM"``, ``"HIGH"``) used by the guard layer.
    - **published_version** — version number currently published to
      channels, or ``None`` if never published (1.3.20). The live row is
      always the editable draft; this pointer decides which immutable
      ``agent_versions`` snapshot external callers resolve to.
    - **evaluation_gate** — nullable JSON publish-gate configuration
      (1.3.20; same shape as ``workflows.evaluation_gate``); ``NULL`` =
      gate off.
    - **skill_discovery_enabled** — three-state skill-discovery override
      (5.9c): ``NULL`` follows the workspace policy, ``True`` opts in,
      ``False`` opts out even when the workspace enabled discovery. The
      layers only narrow, never widen: an explicit ``True`` has no effect
      unless the workspace enabled discovery.
    """

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
    skill_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")
    opening_remarks: Mapped[str | None] = mapped_column(nullable=True)
    enable_suggestions: Mapped[bool] = mapped_column(default=True)
    guardrail_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    evaluation_gate: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    skill_discovery_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    __table_args__ = (Index("idx_agents_workspace", "workspace_id", "deleted"),)


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
    opening_remarks: str | None = None
    enable_suggestions: bool = Field(default=True)
    guardrail_config: dict | None = None
    skill_discovery_enabled: bool | None = Field(
        None,
        description="Skill-discovery override: None follows the workspace policy; "
        "True opts in (effective only when the workspace enabled discovery); "
        "False opts out.",
    )


class AgentUpdateSchema(PydanticBase):
    """Schema for updating an existing agent. All fields are optional.

    ``evaluation_gate`` accepts a full gate configuration or ``None``.
    Passing ``None`` explicitly clears the gate (turns it off); omitting
    the field leaves the stored configuration untouched.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    persona: str | None = None
    llm_config: dict | None = Field(None, alias="model_config")
    mode: str | None = Field(None, pattern="^(chat|three_layer|workflow)$")
    tools: list | None = None
    skills: list | None = None
    knowledge_base_ids: list | None = None
    risk_level: str | None = None
    opening_remarks: str | None = None
    enable_suggestions: bool | None = None
    guardrail_config: dict | None = None
    skill_discovery_enabled: bool | None = Field(
        None,
        description="None resets the override to follow the workspace policy; "
        "True/False set an explicit opt-in/opt-out. Omitting the field leaves "
        "the stored override untouched.",
    )
    evaluation_gate: EvaluationGateConfigSchema | None = None


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
    opening_remarks: str | None
    enable_suggestions: bool
    guardrail_config: dict | None = None
    published_version: int | None = None
    evaluation_gate: dict | None = None
    skill_discovery_enabled: bool | None = None
    created_at: datetime
    updated_at: datetime
    deleted: bool | None = False
    deleted_at: datetime | None
    model_available: bool | None = None
