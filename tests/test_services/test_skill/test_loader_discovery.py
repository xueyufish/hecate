"""Tests for SkillLoader discovery-pool behaviour (5.9c).

Covers the skill-auto-detection capability: pool composition and
eligibility, three-layer governance, trust floor, deterministic catalog
selection, advertised-set widening with provenance, and the
``requires`` warning path.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.evolution import SkillUsageEventModel
from hecate.models.skill import SkillModel
from hecate.models.workspace import WorkspaceModel
from hecate.tools.skill.loader import SkillLoader, SkillNotAdvertisedError

WS = uuid.uuid4()


async def _create_skill(
    db: AsyncSession,
    name: str,
    *,
    workspace_id: uuid.UUID = WS,
    provider: str | None = "project",
    trust_tier: str = "community",
    model_invocable: bool = True,
    instructions: str = "SECRET-INSTRUCTIONS",
    requires: list | None = None,
) -> SkillModel:
    skill = SkillModel(
        workspace_id=workspace_id,
        name=name,
        description=f"Description for {name}",
        source="project",
        instructions=instructions,
        provider=provider,
        trust_tier=trust_tier,
        model_invocable=model_invocable,
        requires=requires,
    )
    db.add(skill)
    await db.flush()
    return skill


async def _create_agent(
    db: AsyncSession,
    skills: list[str],
    *,
    workspace_id: uuid.UUID = WS,
    skill_discovery_enabled: bool | None = None,
) -> AgentModel:
    agent = AgentModel(
        workspace_id=workspace_id,
        name="test-agent",
        persona="You are helpful.",
        model_config_db={"model": "gpt-4o"},
        skills=skills,
        skill_discovery_enabled=skill_discovery_enabled,
    )
    db.add(agent)
    await db.flush()
    return agent


async def _create_workspace(
    db: AsyncSession,
    *,
    settings: dict | None = None,
    workspace_id: uuid.UUID = WS,
) -> WorkspaceModel:
    ws = WorkspaceModel(
        id=workspace_id,
        org_id=uuid.uuid4(),
        name="ws",
        slug="ws",
        settings=settings,
    )
    db.add(ws)
    await db.flush()
    return ws


def _enable_global(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "SKILL_DISCOVERY_ENABLED", True)


async def _usage_events(db: AsyncSession, event_type: str | None = None) -> list[SkillUsageEventModel]:
    stmt = select(SkillUsageEventModel).order_by(SkillUsageEventModel.skill_name)
    if event_type is not None:
        stmt = stmt.where(SkillUsageEventModel.event_type == event_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


class TestDiscoveryGovernance:
    async def test_global_off_keeps_catalog_closed(self, db_session: AsyncSession, monkeypatch) -> None:
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert result == ""

    async def test_workspace_opt_in_surfaces_unbound_skill(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)

        assert '<skill name="unbound-skill" description="Description for unbound-skill"/>' in result
        assert "SECRET-INSTRUCTIONS" not in result

    async def test_workspace_off_blocks_agent_opt_in(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings=None)
        await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [], skill_discovery_enabled=True)

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert result == ""

    async def test_agent_opt_out_wins_over_workspace(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [], skill_discovery_enabled=False)

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert result == ""


class TestDiscoveryPoolComposition:
    async def test_user_provider_excluded(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "personal-skill", provider="user")
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert result == ""

    async def test_model_invisible_excluded(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "hidden-skill", model_invocable=False)
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert result == ""

    async def test_bound_skill_not_duplicated_in_pool(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "bound-skill")
        agent = await _create_agent(db_session, ["bound-skill"])

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert result.count('<skill name="bound-skill"') == 1

    async def test_bundled_skill_discoverable(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "bundled-skill", workspace_id=uuid.UUID(int=0))
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert '<skill name="bundled-skill"' in result

    async def test_trust_floor_excludes_community_but_not_bound(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(
            db_session,
            settings={"skill_discovery": {"enabled": True, "min_trust_tier": "trusted"}},
        )
        await _create_skill(db_session, "community-skill", trust_tier="community")
        await _create_skill(db_session, "trusted-skill", trust_tier="trusted")
        agent = await _create_agent(db_session, ["bound-community"])
        await _create_skill(db_session, "bound-community", trust_tier="community")
        agent.skills = ["bound-community"]
        await db_session.flush()

        result = await SkillLoader(db_session, progressive=True).format_skills(agent.id, agent.workspace_id)
        assert '<skill name="trusted-skill"' in result
        assert '<skill name="community-skill"' not in result
        assert '<skill name="bound-community"' in result


class TestCatalogSelection:
    async def test_trust_tier_outranks_usage_count(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "official-skill", trust_tier="official")
        popular = await _create_skill(db_session, "popular-skill", trust_tier="community")
        for _ in range(10):
            db_session.add(
                SkillUsageEventModel(
                    workspace_id=WS,
                    skill_id=popular.id,
                    skill_name=popular.name,
                    event_type="skill_loaded",
                    detected_via="auto_detected",
                )
            )
        await db_session.flush()
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(
            agent.id, agent.workspace_id, catalog_budget=30
        )
        assert '<skill name="official-skill"' in result
        assert '<skill name="popular-skill"' not in result

    async def test_catalog_omitted_skill_still_loadable(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "keeper", trust_tier="official")
        await _create_skill(db_session, "dropped", trust_tier="community")
        agent = await _create_agent(db_session, [])

        result = await SkillLoader(db_session, progressive=True).format_skills(
            agent.id, agent.workspace_id, catalog_budget=20
        )
        assert '<skill name="dropped"' not in result

        content = await SkillLoader(db_session, progressive=True).load_skill_content(
            "dropped", agent.id, agent.workspace_id
        )
        assert "SECRET-INSTRUCTIONS" in content


class TestL2AdvertisedWidening:
    async def test_discovered_skill_loads_with_provenance(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        skill = await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [])

        content = await SkillLoader(db_session, progressive=True).load_skill_content(
            "unbound-skill", agent.id, agent.workspace_id
        )
        assert "SECRET-INSTRUCTIONS" in content
        events = await _usage_events(db_session, "skill_loaded")
        assert len(events) == 1
        assert events[0].skill_id == skill.id
        assert events[0].detected_via == "auto_detected"

    async def test_bound_skill_load_marks_bound(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "bound-skill")
        agent = await _create_agent(db_session, ["bound-skill"])

        await SkillLoader(db_session, progressive=True).load_skill_content("bound-skill", agent.id, agent.workspace_id)
        events = await _usage_events(db_session, "skill_loaded")
        assert events[0].detected_via == "bound"

    async def test_discovered_skill_rejected_when_agent_opted_out(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [], skill_discovery_enabled=False)

        with pytest.raises(SkillNotAdvertisedError):
            await SkillLoader(db_session, progressive=True).load_skill_content(
                "unbound-skill", agent.id, agent.workspace_id
            )

    async def test_unbound_skill_rejected_when_global_off(self, db_session: AsyncSession) -> None:
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "unbound-skill")
        agent = await _create_agent(db_session, [])

        with pytest.raises(SkillNotAdvertisedError):
            await SkillLoader(db_session, progressive=True).load_skill_content(
                "unbound-skill", agent.id, agent.workspace_id
            )


class TestRequiresWarning:
    async def test_missing_dependency_warns_but_serves(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "needy-skill", requires=[{"name": "ghost-skill"}])
        agent = await _create_agent(db_session, [])

        content = await SkillLoader(db_session, progressive=True).load_skill_content(
            "needy-skill", agent.id, agent.workspace_id
        )
        assert "SECRET-INSTRUCTIONS" in content
        warnings = await _usage_events(db_session, "dependency_warning")
        assert [w.skill_name for w in warnings] == ["ghost-skill"]

    async def test_satisfied_dependency_no_warning(self, db_session: AsyncSession, monkeypatch) -> None:
        _enable_global(monkeypatch)
        await _create_workspace(db_session, settings={"skill_discovery": {"enabled": True}})
        await _create_skill(db_session, "dep-skill")
        await _create_skill(db_session, "needy-skill", requires=[{"name": "dep-skill"}])
        agent = await _create_agent(db_session, [])

        await SkillLoader(db_session, progressive=True).load_skill_content("needy-skill", agent.id, agent.workspace_id)
        assert await _usage_events(db_session, "dependency_warning") == []
