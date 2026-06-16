"""Tests for skill management API endpoints."""

from __future__ import annotations

import io
import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.skill import SkillModel


async def _create_skill_in_db(
    db: AsyncSession,
    name: str,
    workspace_id: uuid.UUID | None = None,
    source: str = "user",
    auto_load: bool = False,
) -> SkillModel:
    skill = SkillModel(
        workspace_id=workspace_id or uuid.UUID(int=0),
        name=name,
        description=f"Description for {name}",
        source=source,
        instructions=f"Instructions for {name}",
        auto_load=auto_load,
    )
    db.add(skill)
    await db.flush()
    return skill


class TestSkillCRUD:
    """Tests for POST/GET/PUT/DELETE /api/skills."""

    async def test_create_skill(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={
                "name": "new-skill",
                "description": "A new skill",
                "source": "user",
                "instructions": "Do something",
            },
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "new-skill"
        assert data["source"] == "user"

    async def test_create_duplicate_name(self, client: AsyncClient) -> None:
        headers = {"Authorization": "Bearer test-key"}
        payload = {
            "name": "dup-skill",
            "description": "First",
            "source": "user",
            "instructions": "First",
        }
        resp1 = await client.post("/api/skills", json=payload, headers=headers)
        assert resp1.status_code == 201

        resp2 = await client.post("/api/skills", json=payload, headers=headers)
        assert resp2.status_code == 409

    async def test_list_skills(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/skills",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    async def test_get_skill(self, client: AsyncClient, db_session: AsyncSession) -> None:
        skill = await _create_skill_in_db(db_session, "get-me")
        resp = await client.get(
            f"/api/skills/{skill.id}",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "get-me"

    async def test_get_nonexistent_skill(self, client: AsyncClient) -> None:
        resp = await client.get(
            f"/api/skills/{uuid.uuid4()}",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 404

    async def test_update_skill(self, client: AsyncClient, db_session: AsyncSession) -> None:
        skill = await _create_skill_in_db(db_session, "update-me")
        resp = await client.put(
            f"/api/skills/{skill.id}",
            json={"description": "Updated description"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "Updated description"

    async def test_delete_skill(self, client: AsyncClient, db_session: AsyncSession) -> None:
        skill = await _create_skill_in_db(db_session, "delete-me")
        resp = await client.delete(
            f"/api/skills/{skill.id}",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 204

    async def test_import_skill_md(self, client: AsyncClient) -> None:
        content = b"---\nname: imported-skill\ndescription: Imported from file\n---\n# Instructions\nDo stuff."
        resp = await client.post(
            "/api/skills/import",
            files={"file": ("SKILL.md", io.BytesIO(content), "text/markdown")},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "imported-skill"

    async def test_import_invalid_format(self, client: AsyncClient) -> None:
        content = b"Not a SKILL.md file"
        resp = await client.post(
            "/api/skills/import",
            files={"file": ("SKILL.md", io.BytesIO(content), "text/markdown")},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 422


class TestAgentSkillAssociation:
    """Tests for POST/DELETE /api/agents/{id}/skills."""

    async def test_add_skill_to_agent(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _create_skill_in_db(db_session, "attach-me")
        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="test-agent",
            model_config_db={"model": "gpt-4o"},
            skills=[],
        )
        db_session.add(agent)
        await db_session.flush()

        resp = await client.post(
            f"/api/agents/{agent.id}/skills",
            json={"skill_name": "attach-me"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert "attach-me" in resp.json()["skills"]

    async def test_add_skill_idempotent(self, client: AsyncClient, db_session: AsyncSession) -> None:
        await _create_skill_in_db(db_session, "idempotent-skill")
        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="test-agent",
            model_config_db={"model": "gpt-4o"},
            skills=["idempotent-skill"],
        )
        db_session.add(agent)
        await db_session.flush()

        resp = await client.post(
            f"/api/agents/{agent.id}/skills",
            json={"skill_name": "idempotent-skill"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["skills"].count("idempotent-skill") == 1

    async def test_remove_skill_from_agent(self, client: AsyncClient, db_session: AsyncSession) -> None:
        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="test-agent",
            model_config_db={"model": "gpt-4o"},
            skills=["remove-me"],
        )
        db_session.add(agent)
        await db_session.flush()

        resp = await client.delete(
            f"/api/agents/{agent.id}/skills/remove-me",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        assert "remove-me" not in resp.json()["skills"]

    async def test_add_nonexistent_skill(self, client: AsyncClient, db_session: AsyncSession) -> None:
        agent = AgentModel(
            workspace_id=uuid.UUID(int=0),
            name="test-agent",
            model_config_db={"model": "gpt-4o"},
            skills=[],
        )
        db_session.add(agent)
        await db_session.flush()

        resp = await client.post(
            f"/api/agents/{agent.id}/skills",
            json={"skill_name": "does-not-exist"},
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 404
