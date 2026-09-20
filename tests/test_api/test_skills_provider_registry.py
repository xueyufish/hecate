"""API tests for the skill provider registry (5.9-enh) spec scenarios."""

from __future__ import annotations

import io
import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.skill import SkillModel

_HEADERS = {"Authorization": "Bearer test-key"}


async def _create_skill_in_db(
    db: AsyncSession,
    name: str,
    workspace_id: uuid.UUID,
    source: str = "user",
    auto_load: bool = False,
) -> SkillModel:
    from hecate.tools.skill.provider_registry import derive_provider

    skill = SkillModel(
        workspace_id=workspace_id,
        name=name,
        description=f"Description for {name}",
        source=source,
        instructions=f"Instructions for {name}",
        auto_load=auto_load,
        provider=derive_provider(source),
    )
    db.add(skill)
    await db.flush()
    return skill


class TestCreateProviderRegistry:
    async def test_create_returns_registry_metadata(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={
                "name": "meta-skill",
                "description": "d",
                "source": "user",
                "instructions": "i",
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["provider"] == "user"
        assert data["trust_tier"] == "community"
        assert data["model_invocable"] is True
        assert data["user_invocable"] is True
        assert data["content_hash"]

    async def test_same_provider_duplicate_conflicts(self, client: AsyncClient) -> None:
        payload = {
            "name": "dup-provider",
            "description": "d",
            "source": "user",
            "instructions": "i",
        }
        assert (await client.post("/api/skills", json=payload, headers=_HEADERS)).status_code == 201
        assert (await client.post("/api/skills", json=payload, headers=_HEADERS)).status_code == 409

    async def test_cross_provider_same_name_coexists(self, client: AsyncClient) -> None:
        first = await client.post(
            "/api/skills",
            json={"name": "coexist", "description": "d", "source": "user", "instructions": "u"},
            headers=_HEADERS,
        )
        assert first.status_code == 201
        second = await client.post(
            "/api/skills",
            json={"name": "coexist", "description": "d", "source": "project", "instructions": "p"},
            headers=_HEADERS,
        )
        assert second.status_code == 201
        assert second.json()["provider"] == "project"

    async def test_auto_load_with_hidden_model_invocation_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={
                "name": "autoload-conflict",
                "description": "d",
                "source": "user",
                "instructions": "i",
                "auto_load": True,
                "model_invocable": False,
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    async def test_flags_default_true(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "flag-defaults", "description": "d", "source": "user", "instructions": "i"},
            headers=_HEADERS,
        )
        data = resp.json()
        assert data["model_invocable"] is True and data["user_invocable"] is True

    async def test_invalid_source_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "bad-source", "description": "d", "source": "invalid", "instructions": "i"},
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    async def test_plugin_source_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "plug-src", "description": "d", "source": "plugin", "instructions": "i"},
            headers=_HEADERS,
        )
        assert resp.status_code == 422


class TestUpdateProviderRegistry:
    async def test_update_invocation_policy(self, client: AsyncClient, db_session: AsyncSession) -> None:
        skill = await _create_skill_in_db(db_session, "toggle-me", uuid.UUID(int=0))
        resp = await client.put(
            f"/api/skills/{skill.id}",
            json={"model_invocable": False},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["model_invocable"] is False

    async def test_update_auto_load_conflict_with_stored_flag_rejected(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        skill = await _create_skill_in_db(db_session, "conflict-me", uuid.UUID(int=0))
        await client.put(f"/api/skills/{skill.id}", json={"model_invocable": False}, headers=_HEADERS)
        resp = await client.put(f"/api/skills/{skill.id}", json={"auto_load": True}, headers=_HEADERS)
        assert resp.status_code == 422

    async def test_update_auto_load_combo_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "combo-skill", "description": "d", "source": "user", "instructions": "i"},
            headers=_HEADERS,
        )
        skill_id = resp.json()["id"]
        resp = await client.put(
            f"/api/skills/{skill_id}",
            json={"auto_load": True, "model_invocable": False},
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    async def test_trust_tier_not_updatable(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "tier-locked", "description": "d", "source": "user", "instructions": "i"},
            headers=_HEADERS,
        )
        skill_id = resp.json()["id"]
        resp = await client.put(f"/api/skills/{skill_id}", json={"trust_tier": "official"}, headers=_HEADERS)
        assert resp.status_code == 422

    async def test_provider_not_updatable(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "provider-locked", "description": "d", "source": "user", "instructions": "i"},
            headers=_HEADERS,
        )
        skill_id = resp.json()["id"]
        resp = await client.put(f"/api/skills/{skill_id}", json={"provider": "project"}, headers=_HEADERS)
        assert resp.status_code == 422

    async def test_content_hash_tracks_edits(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/skills",
            json={"name": "hash-track", "description": "d", "source": "user", "instructions": "v1"},
            headers=_HEADERS,
        )
        old_hash = resp.json()["content_hash"]
        skill_id = resp.json()["id"]
        resp = await client.put(f"/api/skills/{skill_id}", json={"instructions": "v2"}, headers=_HEADERS)
        assert resp.json()["content_hash"] != old_hash


class TestImportProviderRegistry:
    async def _import(self, client: AsyncClient, name: str, body: str) -> object:
        content = f"---\nname: {name}\ndescription: Imported skill\n---\n{body}"
        return await client.post(
            "/api/skills/import",
            files={"file": ("SKILL.md", io.BytesIO(content.encode()), "text/markdown")},
            headers=_HEADERS,
        )

    async def test_import_carries_registry_metadata(self, client: AsyncClient) -> None:
        resp = await self._import(client, "imported-skill", "Body")
        assert resp.status_code == 201
        data = resp.json()
        assert data["provider"] == "user"
        assert data["trust_tier"] == "community"
        assert data["content_hash"]

    async def test_import_same_provider_conflicts(self, client: AsyncClient) -> None:
        assert (await self._import(client, "import-dup", "One")).status_code == 201
        assert (await self._import(client, "import-dup", "Two")).status_code == 409

    async def test_import_invalid_name_rejected(self, client: AsyncClient) -> None:
        content = "---\nname: My Skill!\ndescription: Bad\n---\nBody"
        resp = await client.post(
            "/api/skills/import",
            files={"file": ("SKILL.md", io.BytesIO(content.encode()), "text/markdown")},
            headers=_HEADERS,
        )
        assert resp.status_code == 422


class TestListExposesRegistryMetadata:
    async def test_list_items_include_registry_fields(self, client: AsyncClient) -> None:
        created = await client.post(
            "/api/skills",
            json={"name": "listed-skill", "description": "d", "source": "user", "instructions": "i"},
            headers=_HEADERS,
        )
        assert created.status_code == 201
        resp = await client.get("/api/skills", headers=_HEADERS)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items, "expected at least one skill"
        for item in items:
            for field in (
                "provider",
                "trust_tier",
                "model_invocable",
                "user_invocable",
                "content_hash",
            ):
                assert field in item
