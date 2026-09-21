"""Guard tests for persisted skill provider-registry invariants (5.9-enh)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.skill import SkillModel
from hecate.tools.skill.provider_registry import (
    PROVIDER_BUNDLED,
    PROVIDER_PROJECT,
    PROVIDER_USER,
    derive_provider,
    provider_inconsistencies,
)

_WS = uuid.UUID(int=42)


async def _persist(db: AsyncSession, **overrides: object) -> SkillModel:
    fields: dict[str, object] = {
        "workspace_id": _WS,
        "name": "guard-skill",
        "description": "guard",
        "source": "user",
        "instructions": "body",
        "provider": derive_provider("user"),
    }
    fields.update(overrides)
    skill = SkillModel(**fields)  # type: ignore[arg-type]
    db.add(skill)
    await db.flush()
    return skill


async def _all_skills(db: AsyncSession) -> list[SkillModel]:
    result = await db.execute(select(SkillModel))
    return list(result.scalars().all())


class TestProviderInvariants:
    async def test_ranked_sources_derive_matching_provider(self, db_session: AsyncSession) -> None:
        for source, _expected in (
            ("system", PROVIDER_BUNDLED),
            ("user", PROVIDER_USER),
            ("project", PROVIDER_PROJECT),
        ):
            await _persist(
                db_session,
                name=f"skill-{source}",
                source=source,
                provider=derive_provider(source),
                trust_tier="official" if source == "system" else "community",
            )
        assert provider_inconsistencies(await _all_skills(db_session)) == []

    async def test_plugin_rows_carry_no_provider(self, db_session: AsyncSession) -> None:
        skill = await _persist(db_session, name="plug-skill", source="plugin", provider=None)
        assert skill.provider is None
        assert provider_inconsistencies(await _all_skills(db_session)) == []

    async def test_bundled_rows_are_official(self, db_session: AsyncSession) -> None:
        skill = await _persist(
            db_session,
            name="bundled-skill",
            source="system",
            provider=PROVIDER_BUNDLED,
            trust_tier="official",
        )
        assert skill.trust_tier == "official"
        assert provider_inconsistencies(await _all_skills(db_session)) == []

    async def test_drifted_provider_is_reported(self, db_session: AsyncSession) -> None:
        await _persist(db_session, name="drifted", source="user", provider="project")
        issues = provider_inconsistencies(await _all_skills(db_session))
        assert len(issues) == 1
        assert "provider 'project' != derived 'user'" in issues[0]

    async def test_plugin_row_with_provider_is_reported(self, db_session: AsyncSession) -> None:
        await _persist(db_session, name="bad-plugin", source="plugin", provider="user")
        issues = provider_inconsistencies(await _all_skills(db_session))
        assert len(issues) == 1
        assert "must have provider unset" in issues[0]

    async def test_bundled_row_without_official_tier_is_reported(self, db_session: AsyncSession) -> None:
        await _persist(
            db_session,
            name="bad-bundled",
            source="system",
            provider=PROVIDER_BUNDLED,
            trust_tier="community",
        )
        issues = provider_inconsistencies(await _all_skills(db_session))
        assert len(issues) == 1
        assert "trust_tier 'community' != 'official'" in issues[0]


class TestOrmDefaults:
    async def test_flags_default_true_and_tier_defaults_community(self, db_session: AsyncSession) -> None:
        skill = await _persist(db_session, name="defaults")
        await db_session.refresh(skill)
        assert skill.model_invocable is True
        assert skill.user_invocable is True
        assert skill.trust_tier == "community"
        assert skill.content_hash is None
