"""5.9d skill-versioning core contract tests.

Each test mirrors a spec scenario from the change artifacts:

- commit freezes the seven content fields, hashes the 5-field set
  identical to the agent ref manifest, rejects plugin-sourced skills.
- roll-back creates a new version with the target content and writes it
  back to the live row (live hash zeroes the dirty badge).
- pin-checked delete refuses any version referenced by an agent
  snapshot's reference manifest, lets un-pinned versions go through.
- manifest entries with a non-null ``version`` skip the drift report
  even when the live skill content moves; unpinned entries still drift.
- the agent ref-manifest builder resolves same-name candidates via the
  provider registry (not storage order), so project skills shadow user
  skills regardless of insertion sequence.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.canonical_hash import canonical_hash
from hecate.models.agent import AgentModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.skill import SkillModel
from hecate.studio.agents.versioning import AgentVersionService
from hecate.tools.skill.versioning import (
    SkillNotVersionableError,
    SkillVersionPinnedError,
    SkillVersionService,
)


def _make_skill_kwargs(
    *,
    name: str,
    workspace_id: uuid.UUID,
    provider: str | None,
) -> dict:
    src = {"bundled": "system", "user": "user", "project": "project"}.get(provider or "", "plugin")
    return {
        "workspace_id": workspace_id,
        "name": name,
        "description": "desc",
        "source": src,
        "instructions": "I1",
        "allowed_tools": [],
        "scripts": [],
        "references": [],
        "max_tokens": 2000,
        "auto_load": False,
        "provider": provider,
        "trust_tier": "official" if provider == "bundled" else "community",
        "model_invocable": True,
        "user_invocable": True,
    }


async def _add_and_hash(db_session: AsyncSession, skill: SkillModel, *, instructions: str | None = None) -> None:
    if instructions is not None:
        skill.instructions = instructions
    db_session.add(skill)
    await db_session.flush()
    skill.content_hash = canonical_hash(
        {
            "name": skill.name,
            "instructions": skill.instructions,
            "allowed_tools": skill.allowed_tools,
            "scripts": skill.scripts,
            "references": skill.references,
        }
    )
    await db_session.flush()


async def test_commit_freezes_seven_fields_and_rejects_plugin_source(db_session: AsyncSession) -> None:
    """Snapshot covers all seven frozen fields; plugin source → 409."""
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    skill = SkillModel(**_make_skill_kwargs(name="report", workspace_id=workspace_id, provider="project"))
    await _add_and_hash(db_session, skill, instructions="I1")
    skill.allowed_tools = ["x"]
    skill.scripts = ["y"]
    skill.references = ["z"]
    skill.description = "d1"
    skill.max_tokens = 500
    await db_session.flush()
    skill.content_hash = canonical_hash(
        {
            "name": skill.name,
            "instructions": skill.instructions,
            "allowed_tools": skill.allowed_tools,
            "scripts": skill.scripts,
            "references": skill.references,
        }
    )
    await db_session.flush()

    service = SkillVersionService(db_session)
    detail = await service.commit(skill.id, name="v1", change_summary="init")
    assert detail["version"] == 1
    snap = detail["config_snapshot"]
    assert snap["name"] == "report"
    assert snap["instructions"] == "I1"
    assert snap["allowed_tools"] == ["x"]
    assert snap["scripts"] == ["y"]
    assert snap["references"] == ["z"]
    assert snap["description"] == "d1"
    assert snap["max_tokens"] == 500
    assert detail["content_hash"] == skill.content_hash
    assert detail["learned_run_id"] is None

    plugin = SkillModel(**_make_skill_kwargs(name="plg", workspace_id=workspace_id, provider=None))
    await _add_and_hash(db_session, plugin)
    with pytest.raises(SkillNotVersionableError):
        await service.commit(plugin.id)


async def test_rollback_writes_back_and_zeros_dirty(db_session: AsyncSession) -> None:
    """Rollback snapshots the restored content AND writes it back live."""
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    skill = SkillModel(**_make_skill_kwargs(name="r", workspace_id=workspace_id, provider="user"))
    await _add_and_hash(db_session, skill, instructions="v1 text")

    service = SkillVersionService(db_session)
    await service.commit(skill.id, name="v1")

    await _add_and_hash(db_session, skill, instructions="v2 text")
    status = await service.get_status(skill.id)
    assert status["has_uncommitted_changes"] is True

    v2 = await service.rollback_to_version(skill.id, 1)
    assert v2["version"] == 2
    assert v2["config_snapshot"]["instructions"] == "v1 text"

    after = await service.get_status(skill.id)
    assert after["has_uncommitted_changes"] is False
    assert after["latest_version"] == 2
    assert skill.instructions == "v1 text"


async def test_delete_version_refuses_pinned_unpins_the_rest(db_session: AsyncSession) -> None:
    """A pin in an agent snapshot blocks deletion; un-pinned versions can go."""
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    skill = SkillModel(**_make_skill_kwargs(name="p", workspace_id=workspace_id, provider="project"))
    await _add_and_hash(db_session, skill)
    service = SkillVersionService(db_session)
    v1 = await service.commit(skill.id)
    v2 = await service.commit(skill.id)

    agent = AgentModel(
        workspace_id=workspace_id,
        name="A",
        persona="",
        model_config_db={"model": "x"},
        mode="chat",
        skills=["p"],
    )
    db_session.add(agent)
    await db_session.flush()
    pinned = AgentVersionModel(
        agent_id=agent.id,
        version=1,
        name="v1",
        change_summary="",
        config_snapshot={"skills": ["p"]},
        pinned_refs=[],
        ref_manifest=[
            {
                "resource_type": "skill",
                "resource_id": "p",
                "skill_id": str(skill.id),
                "provider": "project",
                "version": v1["version"],
                "content_hash": v1["content_hash"],
            }
        ],
        schema_version=1,
        content_hash="x",
        workspace_id=workspace_id,
    )
    db_session.add(pinned)
    await db_session.flush()

    with pytest.raises(SkillVersionPinnedError):
        await service.delete_version(skill.id, v1["version"])

    await service.delete_version(skill.id, v2["version"])


async def test_drift_skips_pinned_skill_entries(db_session: AsyncSession) -> None:
    """Manifest entries with ``version`` set never drift."""
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    skill = SkillModel(**_make_skill_kwargs(name="d", workspace_id=workspace_id, provider="project"))
    await _add_and_hash(db_session, skill, instructions="old")
    service = SkillVersionService(db_session)
    await service.commit(skill.id)

    agent = AgentModel(
        workspace_id=workspace_id,
        name="A",
        persona="",
        model_config_db={"model": "x"},
        mode="chat",
        skills=["d"],
    )
    db_session.add(agent)
    await db_session.flush()
    snap = await AgentVersionService(db_session).commit_version(agent.id)
    skill_entry = next(e for e in snap["ref_manifest"] if e.get("resource_type") == "skill")
    assert skill_entry["version"] is not None

    await _add_and_hash(db_session, skill, instructions="new")

    drift = await AgentVersionService(db_session).version_drift(agent.id, 1)
    assert drift["drifted"] == []


async def test_ref_manifest_precedence_is_storage_order_independent(db_session: AsyncSession) -> None:
    """Same-name project + user skills: project wins regardless of insertion order."""
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    # Insert user FIRST so storage order would favour it without precedence.
    user = SkillModel(**_make_skill_kwargs(name="same", workspace_id=workspace_id, provider="user"))
    project = SkillModel(**_make_skill_kwargs(name="same", workspace_id=workspace_id, provider="project"))
    await _add_and_hash(db_session, user)
    await _add_and_hash(db_session, project)

    agent = AgentModel(
        workspace_id=workspace_id,
        name="A",
        persona="",
        model_config_db={"model": "x"},
        mode="chat",
        skills=["same"],
    )
    db_session.add(agent)
    await db_session.flush()
    snap = await AgentVersionService(db_session).commit_version(agent.id)
    skill_entry = next(e for e in snap["ref_manifest"] if e.get("resource_type") == "skill")
    assert skill_entry["skill_id"] == str(project.id)
    assert skill_entry["provider"] == "project"


async def test_unpinned_entry_still_drifts(db_session: AsyncSession) -> None:
    """A never-committed skill (version=None) still surfaces drift when the live row moves."""
    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    skill = SkillModel(**_make_skill_kwargs(name="u", workspace_id=workspace_id, provider="project"))
    await _add_and_hash(db_session, skill, instructions="old")

    agent = AgentModel(
        workspace_id=workspace_id,
        name="A",
        persona="",
        model_config_db={"model": "x"},
        mode="chat",
        skills=["u"],
    )
    db_session.add(agent)
    await db_session.flush()
    snap = await AgentVersionService(db_session).commit_version(agent.id)
    skill_entry = next(e for e in snap["ref_manifest"] if e.get("resource_type") == "skill")
    assert skill_entry["version"] is None

    await _add_and_hash(db_session, skill, instructions="new")

    drift = await AgentVersionService(db_session).version_drift(agent.id, 1)
    assert len(drift["drifted"]) == 1
    assert drift["drifted"][0]["resource_id"] == "u"
