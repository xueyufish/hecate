"""Security finding ORM model and Pydantic schemas.

Stores anomaly detection findings produced by the FindingEngine.
Each finding captures the detection rule, severity, triggering event,
and metadata for compliance auditing and SIEM export.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class SecurityFindingModel(BaseModel):
    """ORM table for anomaly detection findings.

    Records findings from the FindingEngine (formerly PolicyEngine).
    Findings are persisted instead of being discarded via log.warning().
    """

    __tablename__ = "security_findings"
    __table_args__ = (
        Index("ix_security_finding_severity_ts", "severity", "created_at"),
        Index("ix_security_finding_rule_ts", "rule_name", "created_at"),
    )

    org_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source_event: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"SecurityFindingModel(id={self.id}, rule={self.rule_name}, severity={self.severity})"


class SecurityFindingReadSchema(PydanticBase):
    """Schema for reading a security finding."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    rule_name: str
    severity: str
    message: str
    source_event: dict | None = None
    metadata_: dict = Field(default_factory=dict, serialization_alias="metadata")
    created_at: datetime


class SecurityFindingQuerySchema(PydanticBase):
    """Query parameters for filtering security findings."""

    org_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    rule_name: str | None = None
    severity: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
