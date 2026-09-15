"""API integration tests for the self-evolution loop endpoints."""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import EvolutionInputModel, SkillCandidateModel

HEADERS = {"Authorization": "Bearer test-key"}
WS_ZERO = uuid.UUID(int=0)


async def _seed_candidate(db: AsyncSession) -> uuid.UUID:
    row = SkillCandidateModel(
        workspace_id=WS_ZERO,
        name="verify-tool-args",
        description="Verify tool arguments",
        procedure="Read the schema.",
        guardrails="Never guess formats.",
        failure_category="invalid_invocation",
        status="validated",
        validation_report={"overall": "pass", "checks": []},
    )
    db.add(row)
    await db.flush()
    return row.id


async def _seed_input(db: AsyncSession, candidate: SkillCandidateModel) -> None:
    row = EvolutionInputModel(
        workspace_id=WS_ZERO,
        conversation_id=uuid.uuid4(),
        signal_type="quality_below_threshold",
        quality_score=0.2,
    )
    db.add(row)
    await db.flush()
    candidate.deltas = [{"content": "b", "source_input_id": str(row.id)}]
    await db.flush()


class TestCandidatesAPI:
    async def test_list_and_detail(self, client: AsyncClient, db_session: AsyncSession) -> None:
        candidate_id = await _seed_candidate(db_session)

        listing = await client.get("/api/self-evolution/candidates", headers=HEADERS)
        assert listing.status_code == 200
        assert listing.json()["total"] == 1

        detail = await client.get(f"/api/self-evolution/candidates/{candidate_id}", headers=HEADERS)
        assert detail.status_code == 200
        assert detail.json()["status"] == "validated"

    async def test_review_approve_publishes(self, client: AsyncClient, db_session: AsyncSession) -> None:
        candidate_id = await _seed_candidate(db_session)

        resp = await client.post(
            f"/api/self-evolution/candidates/{candidate_id}/review",
            json={"decision": "approved", "reviewer": "admin@example.com"},
            headers=HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "published"
        assert body["published_skill_id"] is not None

    async def test_review_invalid_decision_conflicts(self, client: AsyncClient, db_session: AsyncSession) -> None:
        candidate_id = await _seed_candidate(db_session)

        resp = await client.post(
            f"/api/self-evolution/candidates/{candidate_id}/review",
            json={"decision": "auto", "reviewer": "admin"},
            headers=HEADERS,
        )
        assert resp.status_code == 409

    async def test_lineage_endpoint(self, client: AsyncClient, db_session: AsyncSession) -> None:
        candidate_id = await _seed_candidate(db_session)

        resp = await client.get(f"/api/self-evolution/candidates/{candidate_id}/lineage", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["failure_category"] == "invalid_invocation"

    async def test_unpublish_requires_published_skill(self, client: AsyncClient, db_session: AsyncSession) -> None:
        candidate_id = await _seed_candidate(db_session)

        resp = await client.post(f"/api/self-evolution/candidates/{candidate_id}/unpublish", headers=HEADERS)
        assert resp.status_code == 409


class TestUsageAPI:
    async def test_usage_counts_without_events(self, client: AsyncClient) -> None:
        resp = await client.get("/api/self-evolution/skills/never-used-skill/usage", headers=HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["catalog_served"] == 0
        assert body["l2_loads"] == 0
        assert body["quality_comparison"] is None
