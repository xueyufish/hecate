"""Tests for SkillRegistry.resolve() — each ref_type resolution."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.skill import SkillModel
from hecate.models.tool import ToolModel
from hecate.models.workflow import WorkflowModel
from hecate.skill_registry.registry import SkillRegistry
from hecate.skill_registry.types import SkillNotFoundError, SkillRef, SkillRefType


@pytest.fixture
async def sample_tool(db_session: AsyncSession) -> ToolModel:
    """Create a sample tool for testing."""
    tool = ToolModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="weather_api",
        description="Get current weather for a location",
        source="builtin",
        parameters={"type": "object", "properties": {"location": {"type": "string"}}},
    )
    db_session.add(tool)
    await db_session.flush()
    return tool


@pytest.fixture
async def sample_skill(db_session: AsyncSession) -> SkillModel:
    """Create a sample skill for testing."""
    skill = SkillModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="research_assistant",
        description="Helps with research tasks",
        source="user",
        instructions="You are a research assistant.",
    )
    db_session.add(skill)
    await db_session.flush()
    return skill


@pytest.fixture
async def sample_kb(db_session: AsyncSession) -> KnowledgeBaseModel:
    """Create a sample knowledge base for testing."""
    kb = KnowledgeBaseModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="product_docs",
        description="Product documentation",
        collection_name="product_docs_collection",
    )
    db_session.add(kb)
    await db_session.flush()
    return kb


@pytest.fixture
async def sample_workflow(db_session: AsyncSession) -> WorkflowModel:
    """Create a sample workflow for testing."""
    wf = WorkflowModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="data_pipeline",
        execution_mode="task",
    )
    db_session.add(wf)
    await db_session.flush()
    return wf


@pytest.fixture
async def sample_agent(db_session: AsyncSession) -> AgentModel:
    """Create a sample agent for testing."""
    agent = AgentModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="helper_bot",
        persona="I help with tasks.",
        mode="chat",
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


async def test_resolve_tool(db_session: AsyncSession, sample_tool: ToolModel) -> None:
    """Test resolving a tool reference."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.TOOL, ref_id="weather_api")
    results = await registry.resolve([ref])
    assert len(results) == 1
    assert results[0].name == "weather_api"
    assert results[0].source == SkillRefType.TOOL
    assert results[0].parameters is not None


async def test_resolve_skill(db_session: AsyncSession, sample_skill: SkillModel) -> None:
    """Test resolving a skill reference."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.SKILL, ref_id="research_assistant")
    results = await registry.resolve([ref])
    assert len(results) == 1
    assert results[0].name == "research_assistant"
    assert results[0].source == SkillRefType.SKILL


async def test_resolve_knowledge(db_session: AsyncSession, sample_kb: KnowledgeBaseModel) -> None:
    """Test resolving a knowledge base reference."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.KNOWLEDGE, ref_id=str(sample_kb.id))
    results = await registry.resolve([ref])
    assert len(results) == 1
    assert results[0].name == "product_docs"
    assert results[0].source == SkillRefType.KNOWLEDGE


async def test_resolve_workflow(db_session: AsyncSession, sample_workflow: WorkflowModel) -> None:
    """Test resolving a workflow reference."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.WORKFLOW, ref_id=str(sample_workflow.id))
    results = await registry.resolve([ref])
    assert len(results) == 1
    assert results[0].name == "data_pipeline"
    assert results[0].source == SkillRefType.WORKFLOW


async def test_resolve_agent(db_session: AsyncSession, sample_agent: AgentModel) -> None:
    """Test resolving an agent reference."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.AGENT, ref_id=str(sample_agent.id))
    results = await registry.resolve([ref])
    assert len(results) == 1
    assert results[0].name == "helper_bot"
    assert results[0].source == SkillRefType.AGENT


async def test_resolve_not_found(db_session: AsyncSession) -> None:
    """Test that SkillNotFoundError is raised for unknown references."""
    registry = SkillRegistry(db_session)
    ref = SkillRef(ref_type=SkillRefType.TOOL, ref_id="nonexistent_tool")
    with pytest.raises(SkillNotFoundError):
        await registry.resolve([ref])


async def test_resolve_multiple_refs(
    db_session: AsyncSession,
    sample_tool: ToolModel,
    sample_skill: SkillModel,
) -> None:
    """Test resolving multiple references in one call."""
    registry = SkillRegistry(db_session)
    refs = [
        SkillRef(ref_type=SkillRefType.TOOL, ref_id="weather_api"),
        SkillRef(ref_type=SkillRefType.SKILL, ref_id="research_assistant"),
    ]
    results = await registry.resolve(refs)
    assert len(results) == 2
    assert results[0].source == SkillRefType.TOOL
    assert results[1].source == SkillRefType.SKILL
