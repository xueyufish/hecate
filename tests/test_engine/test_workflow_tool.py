"""Tests for WorkflowTool schema generation and execution."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from hecate.engine.workflow_tool import WorkflowTool


@pytest.fixture
def mock_port() -> MagicMock:
    """Create a mock EnginePort."""
    port = MagicMock()
    port.workflow_execute = AsyncMock(return_value={"output": "done", "status": "completed"})
    return port


def test_workflow_tool_name() -> None:
    """Test WorkflowTool name property."""
    tool = WorkflowTool(
        workflow_id=uuid.uuid4(),
        name="workflow_pipeline",
        description="Run data pipeline",
    )
    assert tool.name == "workflow_pipeline"


def test_workflow_tool_description() -> None:
    """Test WorkflowTool description property."""
    tool = WorkflowTool(
        workflow_id=uuid.uuid4(),
        name="pipeline",
        description="Process data through pipeline",
    )
    assert tool.description == "Process data through pipeline"


def test_workflow_tool_parameters_default() -> None:
    """Test WorkflowTool default parameters schema."""
    tool = WorkflowTool(
        workflow_id=uuid.uuid4(),
        name="pipeline",
        description="Run pipeline",
    )
    assert tool.parameters == {"type": "object", "properties": {}}


def test_workflow_tool_parameters_custom() -> None:
    """Test WorkflowTool custom parameters schema."""
    params = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
    }
    tool = WorkflowTool(
        workflow_id=uuid.uuid4(),
        name="search",
        description="Search pipeline",
        parameters=params,
    )
    assert tool.parameters == params


def test_workflow_tool_to_tool_schema() -> None:
    """Test WorkflowTool schema generation."""
    params = {"type": "object", "properties": {"query": {"type": "string"}}}
    tool = WorkflowTool(
        workflow_id=uuid.uuid4(),
        name="search",
        description="Search the web",
        parameters=params,
    )
    schema = tool.to_tool_schema()
    assert schema["name"] == "search"
    assert schema["description"] == "Search the web"
    assert schema["parameters"] == params


async def test_workflow_tool_execute(mock_port: MagicMock) -> None:
    """Test WorkflowTool execution via EnginePort."""
    wf_id = uuid.uuid4()
    tool = WorkflowTool(
        workflow_id=wf_id,
        name="pipeline",
        description="Run pipeline",
    )
    result = await tool.execute({"query": "test"}, port=mock_port)
    mock_port.workflow_execute.assert_called_once_with(
        workflow_id=wf_id,
        input_data={"query": "test"},
        context={"channel_snapshot": {}},
    )
    assert result == {"output": "done", "status": "completed"}


async def test_workflow_tool_execute_timeout() -> None:
    """Test WorkflowTool timeout handling."""
    port = MagicMock()

    async def slow_execute(**kwargs):
        import asyncio

        await asyncio.sleep(100)
        return {"output": "done"}

    port.workflow_execute = slow_execute
    tool = WorkflowTool(
        workflow_id=uuid.uuid4(),
        name="slow",
        description="Slow pipeline",
    )
    result = await tool.execute({}, port=port, timeout_seconds=0.01)
    assert result["timed_out"] is True
