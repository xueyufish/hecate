"""Backup record ORM model and Pydantic schemas.

Defines the persistence layer for backup metadata, tracking each backup
operation's type, scope, status, storage location, and verification result.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict, Field
from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel, JSONType


class BackupType(enum.StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"
    WAL = "wal"


class BackupScope(enum.StrEnum):
    ALL = "all"
    PG = "pg"
    QDRANT = "qdrant"
    MINIO = "minio"
    FS = "fs"


class BackupStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class RestoreConflict(enum.StrEnum):
    REPLACE = "replace"
    MERGE = "merge"
    FAIL = "fail"


class BackupRecordModel(BaseModel):
    """ORM model for backup records — tracks each backup operation."""

    __tablename__ = "backup_records"

    backup_type: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=BackupStatus.PENDING)
    storage_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    __table_args__ = (
        Index("ix_backup_records_status", "status"),
        Index("ix_backup_records_scope_ts", "scope", "started_at"),
    )


class BackupRecordCreateSchema(PydanticBase):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(default="all", pattern="^(all|pg|qdrant|minio|fs)$")


class BackupRecordReadSchema(PydanticBase):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: uuid.UUID
    backup_type: str
    scope: str
    status: str
    storage_type: str
    storage_path: str
    size_bytes: int | None = None
    checksum: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    metadata_: dict | None = Field(default=None, validation_alias="metadata_")
    verified_at: datetime | None = None
    verification_status: str | None = None
    created_at: datetime
    updated_at: datetime
