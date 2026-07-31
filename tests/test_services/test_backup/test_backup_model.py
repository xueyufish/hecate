"""Tests for BackupRecord ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from hecate.models.backup import (
    BackupRecordCreateSchema,
    BackupRecordModel,
    BackupRecordReadSchema,
    BackupScope,
    BackupStatus,
    BackupType,
)


@pytest.fixture
def sample_backup_id() -> uuid.UUID:
    return uuid.uuid4()


async def test_create_backup_record(db_session):
    """BackupRecord can be created and persisted."""
    backup_id = uuid.uuid4()
    record = BackupRecordModel(
        id=backup_id,
        backup_type=BackupType.FULL,
        scope=BackupScope.ALL,
        status=BackupStatus.COMPLETED,
        storage_type="minio",
        storage_path="20260730_020000",
        size_bytes=1048576,
        checksum="abc123def456",
        started_at=datetime(2026, 7, 30, 2, 0, 0),
        completed_at=datetime(2026, 7, 30, 2, 5, 0),
        metadata_={"pg": {"agents": 10}},
    )
    db_session.add(record)
    await db_session.flush()

    result = await db_session.get(BackupRecordModel, backup_id)
    assert result is not None
    assert result.backup_type == BackupType.FULL
    assert result.scope == BackupScope.ALL
    assert result.status == BackupStatus.COMPLETED
    assert result.size_bytes == 1048576
    assert result.checksum == "abc123def456"


async def test_query_backup_record(db_session):
    """BackupRecord can be queried by status."""
    for i in range(3):
        record = BackupRecordModel(
            id=uuid.uuid4(),
            backup_type=BackupType.FULL,
            scope=BackupScope.PG,
            status=BackupStatus.COMPLETED if i < 2 else BackupStatus.FAILED,
            storage_type="minio",
            storage_path=f"2026073{i}_020000",
            started_at=datetime(2026, 7, 30, 2, 0, 0),
        )
        db_session.add(record)
    await db_session.flush()

    stmt = select(BackupRecordModel).where(BackupRecordModel.status == BackupStatus.COMPLETED)
    result = await db_session.execute(stmt)
    completed = result.scalars().all()
    assert len(completed) == 2


async def test_update_backup_record(db_session):
    """BackupRecord can be updated (e.g., status transition)."""
    backup_id = uuid.uuid4()
    record = BackupRecordModel(
        id=backup_id,
        backup_type=BackupType.FULL,
        scope=BackupScope.ALL,
        status=BackupStatus.RUNNING,
        storage_type="minio",
        storage_path="20260730_020000",
        started_at=datetime(2026, 7, 30, 2, 0, 0),
    )
    db_session.add(record)
    await db_session.flush()

    record.status = BackupStatus.COMPLETED
    record.completed_at = datetime(2026, 7, 30, 2, 5, 0)
    record.size_bytes = 2097152
    await db_session.flush()

    updated = await db_session.get(BackupRecordModel, backup_id)
    assert updated.status == BackupStatus.COMPLETED
    assert updated.size_bytes == 2097152
    assert updated.completed_at is not None


def test_backup_record_create_schema_validation():
    """BackupRecordCreateSchema validates scope field."""
    schema = BackupRecordCreateSchema(scope="pg")
    assert schema.scope == "pg"

    schema_all = BackupRecordCreateSchema()
    assert schema_all.scope == "all"


def test_backup_record_create_schema_rejects_invalid_scope():
    """BackupRecordCreateSchema rejects invalid scope values."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BackupRecordCreateSchema(scope="invalid")


def test_backup_record_read_schema_serialization():
    """BackupRecordReadSchema can serialize from ORM model."""
    record = BackupRecordModel(
        id=uuid.uuid4(),
        backup_type=BackupType.FULL,
        scope=BackupScope.ALL,
        status=BackupStatus.COMPLETED,
        storage_type="s3",
        storage_path="backups/20260730",
        size_bytes=1024,
        checksum="deadbeef",
        started_at=datetime(2026, 7, 30, 2, 0, 0),
        completed_at=datetime(2026, 7, 30, 2, 3, 0),
        created_at=datetime(2026, 7, 30, 2, 0, 0),
        updated_at=datetime(2026, 7, 30, 2, 3, 0),
    )
    schema = BackupRecordReadSchema.model_validate(record)
    assert schema.scope == BackupScope.ALL
    assert schema.storage_type == "s3"
    assert schema.size_bytes == 1024


def test_backup_status_enum_values():
    """BackupStatus enum has expected values."""
    assert BackupStatus.PENDING == "pending"
    assert BackupStatus.RUNNING == "running"
    assert BackupStatus.COMPLETED == "completed"
    assert BackupStatus.FAILED == "failed"
    assert BackupStatus.PARTIAL == "partial"


def test_backup_scope_enum_values():
    """BackupScope enum has expected values."""
    assert BackupScope.ALL == "all"
    assert BackupScope.PG == "pg"
    assert BackupScope.QDRANT == "qdrant"
    assert BackupScope.MINIO == "minio"
    assert BackupScope.FS == "fs"


def test_restore_conflict_enum_values():
    """RestoreConflict enum has expected values."""
    from hecate.models.backup import RestoreConflict

    assert RestoreConflict.REPLACE == "replace"
    assert RestoreConflict.MERGE == "merge"
    assert RestoreConflict.FAIL == "fail"
