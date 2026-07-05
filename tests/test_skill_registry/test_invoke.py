"""Tests for SkillRegistry.invoke() — routing to EnginePort methods."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.tool import ToolModel
from hecate.skill_registry.registry import SkillRegistry
from hecate.skill_registry.types import SkillRef, SkillRefType


@pytest.fixture
def mock_port() -> MagicMock:
    """Create a mock EnginePort."""
    port = MagicMock()
    port.tool_execute = AsyncMock(return_value={"result": "sunny"})
    port.knowledge_query = AsyncMock(return_value=[{"content": "doc chunk"}])
    port.workflow_execute = AsyncMock(return_value={"output": "done"})
    port.agent_execute = AsyncMock(return_value={"response": "hello"})
    return port


@pytest.fixture
async def sample_tool(db_session: AsyncSession) -> ToolModel:
    """Create a sample tool for testing."""
    tool = ToolModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="search_tool",
        description="Search the web",
        source="builtin",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    db_session.add(tool)
    await db_session.flush()
    return tool


async def test_invoke_tool(db_session: AsyncSession, sample_tool: ToolModel, mock_port: MagicMock) -> None:
    """Test invoking a tool skill routes to tool_execute."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.TOOL, ref_id="search_tool")
    result = await registry.invoke(ref, {"args": {"query": "weather"}}, port=mock_port)
    mock_port.tool_execute.assert_called_once_with("search_tool", {"query": "weather"}, {"args": {"query": "weather"}})
    assert result == {"result": "sunny"}


async def test_invoke_tool_without_port(db_session: AsyncSession, sample_tool: ToolModel) -> None:
    """Test that invoking a tool without EnginePort raises ValueError."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.TOOL, ref_id="search_tool")
    with pytest.raises(ValueError, match="EnginePort required"):
        await registry.invoke(ref, {})


async def test_invoke_knowledge(db_session: AsyncSession, mock_port: MagicMock) -> None:
    """Test invoking a knowledge skill routes to knowledge_query."""
    registry = SkillRegistry(db_session)
    kb_id = uuid.uuid4()
    ref = SkillRef(ref_type=SkillRefType.KNOWLEDGE, ref_id=str(kb_id))
    result = await registry.invoke(ref, {"query": "test query"}, port=mock_port)
    mock_port.knowledge_query.assert_called_once_with("test query", [kb_id])
    assert result == [{"content": "doc chunk"}]


async def test_invoke_workflow(db_session: AsyncSession, mock_port: MagicMock) -> None:
    """Test invoking a workflow skill routes to workflow_execute."""
    registry = SkillRegistry(db_session)
    wf_id = uuid.uuid4()
    ref = SkillRef(ref_type=SkillRefType.WORKFLOW, ref_id=str(wf_id))
    result = await registry.invoke(ref, {"input": {"data": "test"}}, port=mock_port)
    mock_port.workflow_execute.assert_called_once_with(wf_id, {"data": "test"}, {"input": {"data": "test"}})
    assert result == {"output": "done"}


async def test_invoke_agent(db_session: AsyncSession, mock_port: MagicMock) -> None:
    """Test invoking an agent skill routes to agent_execute."""
    registry = SkillRegistry(db_session)
    agent_id = uuid.uuid4()
    ref = SkillRef(ref_type=SkillRefType.AGENT, ref_id=str(agent_id))
    result = await registry.invoke(ref, {"task": "help me"}, port=mock_port)
    mock_port.agent_execute.assert_called_once()
    assert result == {"response": "hello"}
