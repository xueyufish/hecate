"""Tests for ApiKeyModel.

Validates ORM behavior, key hash storage, scope enum, and constraints
for database-backed API keys.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.api_key import (
    ApiKeyModel,
    ApiKeyReadSchema,
    ApiKeyScope,
)


def _hash_key(raw_key: str) -> str:
    """Compute SHA-256 hash of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


@pytest.mark.asyncio
async def test_create_api_key(db_session: AsyncSession) -> None:
    """Test creating an API key with valid data."""
    raw_key = "hcat_test1234567890abcdef12345678"
    key_hash = _hash_key(raw_key)

    api_key = ApiKeyModel(
        name="Test Key",
        key_hash=key_hash,
        key_prefix="hcat_tes",
        scope=ApiKeyScope.WORKSPACE,
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        created_by=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        is_active=True,
    )
    db_session.add(api_key)
    await db_session.flush()

    assert api_key.id is not None
    assert api_key.name == "Test Key"
    assert api_key.key_hash == key_hash
    assert api_key.key_prefix == "hcat_tes"
    assert api_key.scope == ApiKeyScope.WORKSPACE
    assert api_key.is_active is True


@pytest.mark.asyncio
async def test_api_key_hash_uniqueness(db_session: AsyncSession) -> None:
    """Test key_hash unique constraint."""
    key_hash = _hash_key("hcat_duplicate_key_1234567890abcdef")

    api_key1 = ApiKeyModel(
        name="Key 1",
        key_hash=key_hash,
        key_prefix="hcat_dup",
        scope=ApiKeyScope.SYSTEM,
        created_by=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        is_active=True,
    )
    db_session.add(api_key1)
    await db_session.flush()

    api_key2 = ApiKeyModel(
        name="Key 2",
        key_hash=key_hash,
        key_prefix="hcat_dup",
        scope=ApiKeyScope.SYSTEM,
        created_by=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        is_active=True,
    )
    db_session.add(api_key2)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_api_key_scope_enum() -> None:
    """Test ApiKeyScope enum values."""
    assert ApiKeyScope.SYSTEM.value == "system"
    assert ApiKeyScope.WORKSPACE.value == "workspace"


@pytest.mark.asyncio
async def test_api_key_expiration(db_session: AsyncSession) -> None:
    """Test API key expiration check."""
    raw_key = "hcat_expired_key_1234567890abcdef12"
    key_hash = _hash_key(raw_key)

    api_key = ApiKeyModel(
        name="Expired Key",
        key_hash=key_hash,
        key_prefix="hcat_exp",
        scope=ApiKeyScope.WORKSPACE,
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        created_by=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        is_active=True,
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),  # Already expired
    )
    db_session.add(api_key)
    await db_session.flush()

    assert api_key.expires_at is not None
    assert api_key.expires_at < datetime.now(UTC)


@pytest.mark.asyncio
async def test_api_key_read_schema(db_session: AsyncSession) -> None:
    """Test ApiKeyReadSchema serialization."""
    raw_key = "hcat_schema_test_1234567890abcdef12"
    key_hash = _hash_key(raw_key)

    api_key = ApiKeyModel(
        name="Schema Test",
        key_hash=key_hash,
        key_prefix="hcat_sch",
        scope=ApiKeyScope.SYSTEM,
        created_by=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        is_active=True,
    )
    db_session.add(api_key)
    await db_session.flush()

    schema = ApiKeyReadSchema.model_validate(api_key)
    data = schema.model_dump()

    assert data["name"] == "Schema Test"
    assert data["key_prefix"] == "hcat_sch"
    assert data["scope"] == "system"
    assert "id" in data
    assert "key_hash" not in data  # Should not expose hash in read schema
