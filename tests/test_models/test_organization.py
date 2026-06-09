"""Tests for OrganizationModel.

Validates ORM behavior, schema serialization, and constraints for the
organization entity used in multi-tenant workspace hierarchy.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.organization import (
    OrganizationCreateSchema,
    OrganizationModel,
    OrganizationReadSchema,
)


@pytest.mark.asyncio
async def test_create_organization(db_session: AsyncSession) -> None:
    """Test creating an organization with valid data."""
    org = OrganizationModel(
        name="Test Organization",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    assert org.id is not None
    assert org.name == "Test Organization"
    assert org.slug == "test-org"
    assert org.owner_id == uuid.UUID("00000000-0000-0000-0000-000000000001")
    assert org.deleted is False
    assert org.created_at is not None


@pytest.mark.asyncio
async def test_organization_read_schema(db_session: AsyncSession) -> None:
    """Test OrganizationReadSchema serialization."""
    org = OrganizationModel(
        name="Test Org",
        slug="test-org",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    schema = OrganizationReadSchema.model_validate(org)
    data = schema.model_dump()

    assert data["name"] == "Test Org"
    assert data["slug"] == "test-org"
    assert "id" in data
    assert "created_at" in data


@pytest.mark.asyncio
async def test_organization_create_schema() -> None:
    """Test OrganizationCreateSchema validation."""
    data = {"name": "New Org", "slug": "new-org"}
    schema = OrganizationCreateSchema.model_validate(data)
    assert schema.name == "New Org"
    assert schema.slug == "new-org"


@pytest.mark.asyncio
async def test_organization_soft_delete(db_session: AsyncSession) -> None:
    """Test soft delete behavior."""
    org = OrganizationModel(
        name="To Delete",
        slug="to-delete",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
    )
    db_session.add(org)
    await db_session.flush()

    assert org.deleted is False
    assert org.deleted_at is None

    org.deleted = True
    from datetime import UTC, datetime

    org.deleted_at = datetime.now(UTC)
    await db_session.flush()

    assert org.deleted is True
    assert org.deleted_at is not None


@pytest.mark.asyncio
async def test_organization_settings_json(db_session: AsyncSession) -> None:
    """Test settings JSON field."""
    org = OrganizationModel(
        name="With Settings",
        slug="with-settings",
        owner_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        settings={"theme": "dark", "language": "en"},
    )
    db_session.add(org)
    await db_session.flush()

    assert org.settings == {"theme": "dark", "language": "en"}
