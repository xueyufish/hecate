"""Unit tests for WorkflowService — version management."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workflow import WorkflowCreateSchema, WorkflowUpdateSchema
from hecate.services.workflow_service import WorkflowService

VALID_DSL = {
    "version": "1.0",
    "name": "test",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}


@pytest.mark.asyncio
async def test_list_versions(db_session: AsyncSession) -> None:
    """Test listing workflow versions."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)
    versions = await service.list_versions(created.id)

    assert len(versions) == 1
    assert versions[0].version == 1


@pytest.mark.asyncio
async def test_get_version(db_session: AsyncSession) -> None:
    """Test getting a specific version."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)
    version = await service.get_version(created.id, 1)

    assert version.version == 1
    assert version.workflow_id == created.id


@pytest.mark.asyncio
async def test_get_version_not_found(db_session: AsyncSession) -> None:
    """Test getting a non-existent version raises ValueError."""
    service = WorkflowService(db_session)

    with pytest.raises(ValueError, match="not found"):
        await service.get_version(uuid.uuid4(), 99)


@pytest.mark.asyncio
async def test_rollback_to_version(db_session: AsyncSession) -> None:
    """Test rolling back to a previous version."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)

    # Create version 2
    v2_dsl = {
        "version": "1.0",
        "name": "v2",
        "state": {"messages": {"type": "topic", "reduce": "append"}},
        "nodes": {
            "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
            "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
        },
        "edges": [{"source": "A", "target": "B"}],
        "entry": "A",
    }
    await service.update_workflow(created.id, WorkflowUpdateSchema(graph_dsl=v2_dsl))

    # Rollback to version 1
    result = await service.rollback_to_version(created.id, 1)

    assert result.current_version == 3
    assert result.version is not None
    assert "Rollback to version 1" in result.version.change_summary


@pytest.mark.asyncio
async def test_rollback_to_nonexistent_version(db_session: AsyncSession) -> None:
    """Test rollback to non-existent version raises ValueError."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)

    with pytest.raises(ValueError, match="not found"):
        await service.rollback_to_version(created.id, 99)


@pytest.mark.asyncio
async def test_version_auto_increment(db_session: AsyncSession) -> None:
    """Test that version numbers auto-increment."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)
    versions = await service.list_versions(created.id)

    assert versions[0].version == 1
