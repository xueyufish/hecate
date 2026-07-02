"""Tests for SkillLoader service."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.skill import SkillModel
from hecate.services.skill.loader import SkillLoader


async def _create_skill(
    db: AsyncSession,
    name: str,
    instructions: str = "Test instructions",
    workspace_id: uuid.UUID | None = None,
    auto_load: bool = False,
    max_tokens: int = 2000,
) -> SkillModel:
    skill = SkillModel(
        workspace_id=workspace_id or uuid.UUID(int=0),
        name=name,
        description=f"Description for {name}",
        source="user",
        instructions=instructions,
        auto_load=auto_load,
        max_tokens=max_tokens,
    )
    db.add(skill)
    await db.flush()
    return skill


async def _create_agent(
    db: AsyncSession,
    skills: list[str],
    workspace_id: uuid.UUID | None = None,
) -> AgentModel:
    agent = AgentModel(
        workspace_id=workspace_id or uuid.UUID(int=0),
        name="test-agent",
        persona="You are helpful.",
        model_config_db={"model": "gpt-4o"},
        skills=skills,
    )
    db.add(agent)
    await db.flush()
    return agent


class TestSkillLoader:
    """Tests for SkillLoader.format_skills()."""

    async def test_format_skills_with_skills(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "code-review", "Check code quality")
        agent = await _create_agent(db_session, ["code-review"])

        loader = SkillLoader(db_session)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert "<skills>" in result
        assert '<skill name="code-review">' in result
        assert "Check code quality" in result
        assert "</skills>" in result

    async def test_format_skills_no_skills(self, db_session: AsyncSession) -> None:
        agent = await _create_agent(db_session, [])

        loader = SkillLoader(db_session)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert result == ""

    async def test_format_skills_missing_skill(self, db_session: AsyncSession) -> None:
        agent = await _create_agent(db_session, ["nonexistent"])

        loader = SkillLoader(db_session)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert result == ""

    async def test_auto_load_skills_included(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "auto-skill", "Always loaded", auto_load=True)
        agent = await _create_agent(db_session, [])

        loader = SkillLoader(db_session)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert '<skill name="auto-skill">' in result

    async def test_xml_format(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "test-skill", "Body text")
        agent = await _create_agent(db_session, ["test-skill"])

        loader = SkillLoader(db_session)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert result.startswith("<skills>")
        assert result.endswith("</skills>")
        assert '<skill name="test-skill">' in result
        assert "</skill>" in result

    async def test_deduplication(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "shared", "Shared skill", auto_load=True)
        agent = await _create_agent(db_session, ["shared"])

        loader = SkillLoader(db_session)
        result = await loader.format_skills(agent.id, agent.workspace_id)

        assert result.count('<skill name="shared">') == 1

    async def test_nonexistent_agent(self, db_session: AsyncSession) -> None:
        loader = SkillLoader(db_session)
        result = await loader.format_skills(uuid.uuid4(), uuid.UUID(int=0))
        assert result == ""
