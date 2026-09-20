"""Loader tests for provider-registry precedence and invocation policy (5.9-enh)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.skill import SkillModel
from hecate.tools.skill.loader import SkillLoader, SkillNotAdvertisedError

_WS = uuid.UUID(int=7)


async def _create_skill(
    db: AsyncSession,
    name: str,
    source: str = "user",
    workspace_id: uuid.UUID | None = _WS,
    auto_load: bool = False,
    model_invocable: bool = True,
) -> SkillModel:
    from hecate.tools.skill.provider_registry import derive_provider

    skill = SkillModel(
        workspace_id=workspace_id if workspace_id is not None else uuid.UUID(int=0),
        name=name,
        description=f"Description for {name} ({source})",
        source=source,
        instructions=f"Instructions for {name} ({source})",
        auto_load=auto_load,
        provider=derive_provider(source),
        model_invocable=model_invocable,
    )
    db.add(skill)
    await db.flush()
    return skill


async def _create_agent(db: AsyncSession, skills: list[str]) -> AgentModel:
    agent = AgentModel(
        workspace_id=_WS,
        name="provider-agent",
        persona="You are helpful.",
        model_config_db={"model": "gpt-4o"},
        skills=skills,
    )
    db.add(agent)
    await db.flush()
    return agent


class TestPrecedenceResolution:
    async def test_project_shadows_user_and_bundled(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "pdf-report", source="system", workspace_id=None)
        await _create_skill(db_session, "pdf-report", source="user")
        await _create_skill(db_session, "pdf-report", source="project")
        agent = await _create_agent(db_session, ["pdf-report"])

        rendered = await SkillLoader(db_session).format_skills(agent.id, _WS)
        assert 'name="pdf-report"' in rendered
        assert "(project)" in rendered
        assert "(system)" not in rendered
        assert "(user)" not in rendered

    async def test_user_shadows_bundled(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "summarize", source="system", workspace_id=None)
        await _create_skill(db_session, "summarize", source="user")
        agent = await _create_agent(db_session, ["summarize"])

        rendered = await SkillLoader(db_session).format_skills(agent.id, _WS)
        assert "(user)" in rendered
        assert "(system)" not in rendered

    async def test_bundled_served_without_workspace_counterpart(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "translate", source="system", workspace_id=None)
        agent = await _create_agent(db_session, ["translate"])

        rendered = await SkillLoader(db_session).format_skills(agent.id, _WS)
        assert "(system)" in rendered

    async def test_auto_load_same_name_resolves_to_highest_rank(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "always-on", source="system", workspace_id=None, auto_load=True)
        await _create_skill(db_session, "always-on", source="project", auto_load=True)
        agent = await _create_agent(db_session, [])

        rendered = await SkillLoader(db_session).format_skills(agent.id, _WS)
        assert "(project)" in rendered
        assert "(system)" not in rendered


class TestModelInvocableFiltering:
    async def test_model_invisible_skill_excluded_from_catalog(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "hidden-skill", model_invocable=False)
        agent = await _create_agent(db_session, ["hidden-skill"])

        rendered = await SkillLoader(db_session).format_skills(agent.id, _WS)
        assert "hidden-skill" not in rendered

    async def test_model_invisible_auto_load_skill_excluded(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "hidden-auto", auto_load=True, model_invocable=False)
        agent = await _create_agent(db_session, [])

        rendered = await SkillLoader(db_session).format_skills(agent.id, _WS)
        assert "hidden-auto" not in rendered

    async def test_l2_load_rejected_for_model_invisible_skill(self, db_session: AsyncSession) -> None:
        await _create_skill(db_session, "secret-skill", model_invocable=False)
        agent = await _create_agent(db_session, ["secret-skill"])

        with pytest.raises(SkillNotAdvertisedError, match="not model-invocable"):
            await SkillLoader(db_session).load_skill_content("secret-skill", agent.id, _WS)

    async def test_legacy_mode_filters_model_invisible_skills(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("hecate.tools.skill.loader.DEFAULT_TOTAL_TOKEN_BUDGET", 4000)
        await _create_skill(db_session, "legacy-hidden", model_invocable=False)
        await _create_skill(db_session, "legacy-visible")
        agent = await _create_agent(db_session, ["legacy-hidden", "legacy-visible"])

        loader = SkillLoader(db_session, progressive=False)
        rendered = await loader.format_skills(agent.id, _WS)
        assert "legacy-visible" in rendered
        assert "legacy-hidden" not in rendered
