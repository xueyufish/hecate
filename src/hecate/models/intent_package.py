"""Intent package ORM models and Pydantic schemas (6.49).

Defines the persistence layer and API schemas for intent packages —
governed workspace assets pairing intent categories with sample utterances,
consumed by the intent recognition engine (6.23) as few-shot classification
evidence:

- **IntentPackageModel** — named package, workspace-scoped
- **IntentPackageCategoryModel** — one intent category (name, description,
  optional domain label, optional policy-gated flag)
- **IntentPackageSampleModel** — one sample utterance with provenance
- **IntentPackageVersionModel** — named, immutable freeze of the package's
  draft content, mirroring ``EvaluationDatasetVersionModel`` semantics

The draft (package + categories + samples rows) is the only editable
surface; versions freeze it and runtime reads published versions only.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class GateMode(enum.StrEnum):
    """Publish-gate enforcement mode (mirrors the workflow evaluation gate)."""

    WARN = "warn"
    REQUIRE = "require"


class SampleSourceType(enum.StrEnum):
    """How a sample utterance entered the package."""

    MANUAL = "manual"
    IMPORT = "import"
    CORRECTION = "correction"


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class IntentPackageModel(BaseModel):
    """ORM model for an intent package — a named set of intent categories.

    Key fields:

    - **name** — human-readable package name; unique among non-deleted
      packages in the workspace (service-enforced)
    - **description** — optional longer description
    """

    __tablename__ = "intent_packages"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        Index("idx_intent_packages_workspace", "workspace_id", "deleted"),
        Index("idx_intent_packages_created", "created_at"),
    )


class IntentPackageCategoryModel(BaseModel):
    """ORM model for one intent category inside a package.

    Key fields:

    - **package_id** — owning package
    - **name** — category label; unique among non-deleted categories of the
      package (service-enforced). Recognition results and controller
      mappings reference categories by this name.
    - **description** — classification guidance shown to the recognizer
      ("what + when", with distinguishing keywords)
    - **domain** — optional domain label (L4) carried on recognition results
      that hit this category
    - **policy_gated** — optional L5 marker: routes to this category are
      subject to the deterministic approval path regardless of recognition
      output
    """

    __tablename__ = "intent_package_categories"

    package_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    policy_gated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Explicit ordering for deterministic freeze serialization — DB
    # timestamps cannot order rows inserted in one transaction (server
    # now() is transaction-scoped in PostgreSQL).
    position: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")

    __table_args__ = (Index("idx_intent_package_categories_package", "package_id", "deleted"),)


class IntentPackageSampleModel(BaseModel):
    """ORM model for one sample utterance inside a category.

    Key fields:

    - **category_id** — owning category
    - **utterance** — the sample text used as few-shot classification
      evidence
    - **provenance** — audit metadata: ``source_type`` (manual / import /
      correction), ``source_session_id`` / ``source_turn_id`` (correction
      origin), ``reason`` (correction rationale), ``created_by``. Excluded
      from the version content hash.
    """

    __tablename__ = "intent_package_samples"

    category_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    utterance: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # Deterministic ordering within the category (see the category model).
    position: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")

    __table_args__ = (Index("idx_intent_package_samples_category", "category_id", "deleted"),)


class IntentPackageVersionModel(BaseModel):
    """ORM model for a named, immutable intent package version.

    A version freezes the package's draft content (categories with their
    samples) at creation time. The ``content_hash`` uses the same canonical
    JSON sha256 primitive as evaluation dataset snapshots, projected onto
    intent-package content fields (category definition + sample utterances;
    provenance excluded).

    Key fields:

    - **package_id** — the source package; versions never move packages
    - **name** — unique within the package across soft-deleted rows too, so
      a name always refers to the same frozen content
    - **content** — frozen ``{"categories": [...]}`` payload
    - **content_hash** — sha256 over the content projection
    - **created_by** — server-filled from the authenticated user
    - **published_at** — null while unpublished; set when the publish gate
      passes (or is forced). Runtime evidence resolution reads published
      versions only.
    - **gate_report** — gate evaluation result recorded at publish time
    """

    __tablename__ = "intent_package_versions"

    package_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    gate_report: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )

    __table_args__ = (
        UniqueConstraint("package_id", "name", name="uq_intent_package_versions_name"),
        Index("idx_intent_package_versions_workspace", "workspace_id", "deleted"),
    )


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------


class IntentPackageCreateSchema(PydanticBase):
    """Schema for creating an intent package."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class IntentPackageUpdateSchema(PydanticBase):
    """Schema for updating an intent package. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None


class IntentPackageReadSchema(PydanticBase):
    """Schema for reading intent package metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class IntentCategoryCreateSchema(PydanticBase):
    """Schema for creating a category in a package draft."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    domain: str | None = Field(None, max_length=255)
    policy_gated: bool = False


class IntentCategoryUpdateSchema(PydanticBase):
    """Schema for updating a category in a package draft. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    domain: str | None = Field(None, max_length=255)
    policy_gated: bool | None = None


class IntentCategoryReadSchema(PydanticBase):
    """Schema for reading a category with its sample count."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    package_id: uuid.UUID
    name: str
    description: str | None
    domain: str | None
    policy_gated: bool
    created_at: datetime
    updated_at: datetime


class IntentSampleCreateSchema(PydanticBase):
    """Schema for adding a sample utterance to a category draft."""

    model_config = ConfigDict(extra="forbid")

    utterance: str = Field(..., min_length=1)
    provenance: dict | None = None


class IntentSampleReadSchema(PydanticBase):
    """Schema for reading a sample utterance."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_id: uuid.UUID
    utterance: str
    provenance: dict
    created_at: datetime
    updated_at: datetime


class IntentCorrectionCreateSchema(PydanticBase):
    """Schema for a correction backflow submission (6.49).

    Appends a sample to the draft with mandatory provenance: what was
    misrouted (``utterance``), what it should have been (``category_id``
    or ``category_name``), why (``reason``), and where it came from
    (optional ``source_session_id`` / ``source_turn_id``).
    """

    model_config = ConfigDict(extra="forbid")

    utterance: str = Field(..., min_length=1)
    category_id: uuid.UUID | None = None
    category_name: str | None = Field(None, min_length=1, max_length=255)
    reason: str = Field(..., min_length=1)
    source_session_id: uuid.UUID | None = None
    source_turn_id: uuid.UUID | None = None


class PublishGateConfigSchema(PydanticBase):
    """Schema for the publish-gate configuration on a package.

    Signals (deterministic only): ``min_pass_rate`` — minimum recognition
    accuracy from the linked evaluation run; ``min_samples_per_category`` —
    minimum sample coverage per frozen category. ``mode=require`` blocks
    publishing when an enabled signal is unmet; ``warn`` records the verdict
    without blocking.
    """

    model_config = ConfigDict(extra="forbid")

    mode: GateMode = GateMode.WARN
    min_pass_rate: float | None = Field(None, ge=0.0, le=1.0)
    min_samples_per_category: int | None = Field(None, ge=1)


class IntentPackageVersionCreateSchema(PydanticBase):
    """Schema for freezing a package draft into a named version."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class IntentPackageVersionReadSchema(PydanticBase):
    """Schema for reading an intent package version."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    package_id: uuid.UUID
    name: str
    description: str | None
    content: dict
    content_hash: str
    created_by: uuid.UUID | None
    published_at: datetime | None
    gate_report: dict | None
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class IntentImportReportSchema(PydanticBase):
    """Row-level report for a bulk import (all-or-nothing)."""

    model_config = ConfigDict(extra="forbid")

    categories_created: int
    samples_created: int
    categories_merged: int
    samples_merged: int
    mode: str
    format: str


class IntentPublishResultSchema(PydanticBase):
    """Result of a publish attempt: gate verdicts plus outcome."""

    model_config = ConfigDict(extra="forbid")

    published: bool
    gate: dict
    version_id: uuid.UUID
    bypassed_by_force: bool = False


class IntentVersionGateRequestSchema(PydanticBase):
    """Request body for configuring the gate on a version publish."""

    model_config = ConfigDict(extra="forbid")

    gate: PublishGateConfigSchema | None = None
    force: bool = False


__all__ = [
    "GateMode",
    "IntentCategoryCreateSchema",
    "IntentCategoryReadSchema",
    "IntentCategoryUpdateSchema",
    "IntentCorrectionCreateSchema",
    "IntentImportReportSchema",
    "IntentPackageCategoryModel",
    "IntentPackageCreateSchema",
    "IntentPackageModel",
    "IntentPackageUpdateSchema",
    "IntentPackageVersionCreateSchema",
    "IntentPackageVersionModel",
    "IntentPackageVersionReadSchema",
    "IntentPublishResultSchema",
    "IntentSampleCreateSchema",
    "IntentSampleReadSchema",
    "IntentVersionGateRequestSchema",
    "PublishGateConfigSchema",
    "SampleSourceType",
]
