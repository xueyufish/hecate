"""Unit tests for WorkflowService — CRUD operations."""

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
async def test_create_workflow(db_session: AsyncSession) -> None:
    """Test creating a workflow."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test-workflow", graph_dsl=VALID_DSL)

    result = await service.create_workflow(data)

    assert result.name == "test-workflow"
    assert result.current_version == 1
    assert result.version is not None
    assert result.version.version == 1


@pytest.mark.asyncio
async def test_get_workflow(db_session: AsyncSession) -> None:
    """Test getting a workflow."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test-workflow", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)
    result = await service.get_workflow(created.id)

    assert result.id == created.id
    assert result.name == "test-workflow"


@pytest.mark.asyncio
async def test_get_workflow_not_found(db_session: AsyncSession) -> None:
    """Test getting a non-existent workflow raises ValueError."""
    service = WorkflowService(db_session)

    with pytest.raises(ValueError, match="not found"):
        await service.get_workflow(uuid.uuid4())


@pytest.mark.asyncio
async def test_update_workflow_name(db_session: AsyncSession) -> None:
    """Test updating workflow name only."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="original", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)
    update_data = WorkflowUpdateSchema(name="updated")
    result = await service.update_workflow(created.id, update_data)

    assert result.name == "updated"
    assert result.current_version == 1


@pytest.mark.asyncio
async def test_update_workflow_dsl_creates_new_version(db_session: AsyncSession) -> None:
    """Test updating graph_dsl creates a new version."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="test", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)

    new_dsl = {
        "version": "1.0",
        "name": "test-v2",
        "state": {"messages": {"type": "topic", "reduce": "append"}},
        "nodes": {
            "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
            "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
        },
        "edges": [{"source": "A", "target": "B"}],
        "entry": "A",
    }
    update_data = WorkflowUpdateSchema(graph_dsl=new_dsl, change_summary="Added node B")
    result = await service.update_workflow(created.id, update_data)

    assert result.current_version == 2
    assert result.version is not None
    assert result.version.version == 2


@pytest.mark.asyncio
async def test_delete_workflow(db_session: AsyncSession) -> None:
    """Test soft deleting a workflow."""
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="to-delete", graph_dsl=VALID_DSL)

    created = await service.create_workflow(data)
    await service.delete_workflow(created.id)

    with pytest.raises(ValueError, match="not found"):
        await service.get_workflow(created.id)


@pytest.mark.asyncio
async def test_list_workflows(db_session: AsyncSession) -> None:
    """Test listing workflows."""
    service = WorkflowService(db_session)

    for i in range(3):
        data = WorkflowCreateSchema(name=f"workflow-{i}", graph_dsl=VALID_DSL)
        await service.create_workflow(data)

    result = await service.list_workflows()
    assert result["total"] == 3
    assert len(result["items"]) == 3


@pytest.mark.asyncio
async def test_list_workflows_pagination(db_session: AsyncSession) -> None:
    """Test listing workflows with pagination."""
    service = WorkflowService(db_session)

    for i in range(5):
        data = WorkflowCreateSchema(name=f"workflow-{i}", graph_dsl=VALID_DSL)
        await service.create_workflow(data)

    result = await service.list_workflows(page=1, page_size=2)
    assert result["total"] == 5
    assert len(result["items"]) == 2
