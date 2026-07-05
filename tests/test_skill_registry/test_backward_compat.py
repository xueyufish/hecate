"""Tests for backward compatibility — agents with legacy fields still work."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel


@pytest.fixture
async def agent_with_legacy_fields(db_session: AsyncSession) -> AgentModel:
    """Create an agent using legacy fields (tools, skills, knowledge_base_ids)."""
    agent = AgentModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="legacy_agent",
        persona="I use legacy fields.",
        mode="chat",
        tools=["web_search", "read_file"],
        skills=["research_assistant"],
        knowledge_base_ids=[],
        skill_ids=[],  # Empty — uses legacy fields
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.fixture
async def agent_with_skill_ids(db_session: AsyncSession) -> AgentModel:
    """Create an agent using unified skill_ids field."""
    agent = AgentModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="modern_agent",
        persona="I use skill_ids.",
        mode="chat",
        tools=[],  # Empty — uses skill_ids
        skills=[],
        knowledge_base_ids=[],
        skill_ids=[
            {"ref_type": "tool", "ref_id": "web_search"},
            {"ref_type": "skill", "ref_id": "research_assistant"},
        ],
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


async def test_legacy_agent_has_empty_skill_ids(
    db_session: AsyncSession,
    agent_with_legacy_fields: AgentModel,
) -> None:
    """Test that legacy agents have empty skill_ids by default."""
    assert agent_with_legacy_fields.skill_ids == []
    assert agent_with_legacy_fields.tools == ["web_search", "read_file"]
    assert agent_with_legacy_fields.skills == ["research_assistant"]


async def test_modern_agent_has_skill_ids(
    db_session: AsyncSession,
    agent_with_skill_ids: AgentModel,
) -> None:
    """Test that modern agents have populated skill_ids."""
    assert len(agent_with_skill_ids.skill_ids) == 2
    assert agent_with_skill_ids.skill_ids[0]["ref_type"] == "tool"
    assert agent_with_skill_ids.skill_ids[1]["ref_type"] == "skill"


async def test_agent_model_accepts_skill_ids(db_session: AsyncSession) -> None:
    """Test that AgentModel can store and retrieve skill_ids."""
    agent = AgentModel(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
        name="test_agent",
        mode="chat",
        skill_ids=[{"ref_type": "tool", "ref_id": "search"}],
    )
    db_session.add(agent)
    await db_session.flush()
    assert agent.skill_ids == [{"ref_type": "tool", "ref_id": "search"}]
