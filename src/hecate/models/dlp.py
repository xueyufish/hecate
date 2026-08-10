"""DLP ORM models and Pydantic schemas.

Defines the persistence layer for the outbound DLP engine:

* :class:`DLPPolicyModel` — one row per (scope, entity_type, direction)
  policy rule with action, optional mask format, and ``is_locked`` flag.
* :class:`DLPCustomRegexModel` — org- or workspace-scoped additional
  regex patterns that augment the built-in :class:`RegexRecognizer`.
* :class:`DLPDictionaryModel` — org- or workspace-scoped term lists that
  back the :class:`DictionaryRecognizer`.

All three tables follow the multi-tenant ``org_id`` / ``workspace_id``
naming convention (see ``docs/design/multi-tenancy.md``) so the
:class:`DLPPolicyResolver` can perform its four-level scope lookup
without bespoke join logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import JSON, Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class DLPPolicyModel(BaseModel):
    """ORM model for a DLP policy rule.

    One row per ``(scope, entity_type, direction)`` triple at a given
    scope level. Scope is encoded via the three ``org_id`` /
    ``workspace_id`` / ``agent_id`` columns (only one of which is set
    per row — enforced at the application layer by
    :class:`PolicyScope`).

    The ``is_locked`` column implements design.md §D4: when ``True``,
    the rule cannot be overridden by rules at more specific scopes.
    The ``enabled`` column allows soft-disable without deleting the
    row.
    """

    __tablename__ = "dlp_policies"

    org_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    mask_format: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        Index(
            "ix_dlp_policies_org_entity",
            "org_id",
            "entity_type",
            "direction",
        ),
        Index(
            "ix_dlp_policies_workspace_entity",
            "workspace_id",
            "entity_type",
            "direction",
        ),
        Index(
            "ix_dlp_policies_agent_entity",
            "agent_id",
            "entity_type",
            "direction",
        ),
    )


class DLPPolicyCreateSchema(PydanticBase):
    """Create payload for a DLP policy rule."""

    model_config = ConfigDict(extra="forbid")

    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    entity_type: str = Field(min_length=1, max_length=100)
    direction: str = Field(min_length=1, max_length=50)
    action: str = Field(pattern=r"^(allow|block|mask|audit)$")
    mask_format: str | None = Field(default=None, max_length=100)
    is_locked: bool = False
    enabled: bool = True


class DLPPolicyUpdateSchema(PydanticBase):
    """Partial update payload — all fields optional."""

    model_config = ConfigDict(extra="forbid")

    action: str | None = Field(default=None, pattern=r"^(allow|block|mask|audit)$")
    mask_format: str | None = Field(default=None, max_length=100)
    is_locked: bool | None = None
    enabled: bool | None = None


class DLPPolicyReadSchema(PydanticBase):
    """Read representation of a DLP policy rule."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    entity_type: str
    direction: str
    action: str
    mask_format: str | None
    is_locked: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DLPPolicyQuerySchema(PydanticBase):
    """Query parameters for filtering DLP policies."""

    model_config = ConfigDict(extra="forbid")

    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    agent_id: uuid.UUID | None = None
    direction: str | None = None
    enabled_only: bool = True


class DLPCustomRegexModel(BaseModel):
    """ORM model for an org/workspace-scoped additional regex pattern.

    Augments the built-in :class:`RegexRecognizer` with custom
    patterns (e.g., project-specific tokens like ``HECATE_API_KEY``).
    """

    __tablename__ = "dlp_custom_regex"

    org_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        Index(
            "ix_dlp_custom_regex_org_entity",
            "org_id",
            "entity_type",
        ),
    )


class DLPCustomRegexCreateSchema(PydanticBase):
    """Create payload for a custom regex pattern."""

    model_config = ConfigDict(extra="forbid")

    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    pattern: str = Field(min_length=1)
    entity_type: str = Field(min_length=1, max_length=100)
    enabled: bool = True


class DLPCustomRegexReadSchema(PydanticBase):
    """Read representation of a custom regex pattern."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    name: str
    pattern: str
    entity_type: str
    enabled: bool


class DLPDictionaryModel(BaseModel):
    """ORM model for an org/workspace-scoped dictionary of terms.

    Backs the :class:`DictionaryRecognizer`. ``terms`` is stored as a
    JSONB array so callers can extend the list without DDL changes.
    """

    __tablename__ = "dlp_dictionaries"

    org_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    terms: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        Index(
            "ix_dlp_dictionaries_org_entity",
            "org_id",
            "entity_type",
        ),
    )


class DLPDictionaryCreateSchema(PydanticBase):
    """Create payload for a dictionary."""

    model_config = ConfigDict(extra="forbid")

    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    entity_type: str = Field(min_length=1, max_length=100)
    case_sensitive: bool = False
    terms: list[str] = Field(default_factory=list)
    enabled: bool = True


class DLPDictionaryReadSchema(PydanticBase):
    """Read representation of a dictionary."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    workspace_id: uuid.UUID | None
    name: str
    entity_type: str
    case_sensitive: bool
    terms: list[str]
    enabled: bool
