"""Tests for EvolutionRepository workspace isolation and queries."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionInputModel,
    EvolutionRunModel,
    SkillCandidateModel,
)
from hecate.studio.self_evolution.repository import EvolutionRepository

WS_A = uuid.uuid4()
WS_B = uuid.uuid4()


async def _add_input(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    status: str = "pending",
) -> EvolutionInputModel:
    row = EvolutionInputModel(
        workspace_id=workspace_id,
        signal_type="quality_below_threshold",
        quality_score=0.3,
        status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def _add_candidate(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    name: str,
    status: str = "pending",
) -> SkillCandidateModel:
    row = SkillCandidateModel(
        workspace_id=workspace_id,
        name=name,
        description="desc",
        procedure="proc",
        guardrails="guard",
        failure_category="invalid_invocation",
        status=status,
    )
    db.add(row)
    await db.flush()
    return row


class TestInputQueries:
    async def test_pending_inputs_scoped_to_workspace(self, db_session: AsyncSession) -> None:
        await _add_input(db_session, WS_A, status="pending")
        await _add_input(db_session, WS_B, status="pending")

        repo = EvolutionRepository(db_session)
        pending_a = await repo.list_pending_inputs(WS_A)

        assert all(row.workspace_id == WS_A for row in pending_a)
        assert len(pending_a) == 1

    async def test_pending_excludes_non_pending(self, db_session: AsyncSession) -> None:
        await _add_input(db_session, WS_A, status="pending")
        await _add_input(db_session, WS_A, status="skipped")

        repo = EvolutionRepository(db_session)
        pending = await repo.list_pending_inputs(WS_A)

        assert len(pending) == 1

    async def test_get_input_rejects_foreign_workspace(self, db_session: AsyncSession) -> None:
        row = await _add_input(db_session, WS_A)

        repo = EvolutionRepository(db_session)
        assert await repo.get_input(WS_B, row.id) is None
        assert (await repo.get_input(WS_A, row.id)).id == row.id

    async def test_list_inputs_excludes_soft_deleted(self, db_session: AsyncSession) -> None:
        row = await _add_input(db_session, WS_A)
        row.deleted = True

        repo = EvolutionRepository(db_session)
        assert await repo.list_inputs(WS_A) == []


class TestRunQueries:
    async def test_create_run_sets_started_at(self, db_session: AsyncSession) -> None:
        repo = EvolutionRepository(db_session)
        run = await repo.create_run(EvolutionRunModel(workspace_id=WS_A, input_count=3))

        assert run.started_at is not None
        assert run.status == "pending"

    async def test_get_run_rejects_foreign_workspace(self, db_session: AsyncSession) -> None:
        repo = EvolutionRepository(db_session)
        run = await repo.create_run(EvolutionRunModel(workspace_id=WS_A))

        assert await repo.get_run(WS_B, run.id) is None

    async def test_list_runs_scoped_to_workspace(self, db_session: AsyncSession) -> None:
        repo = EvolutionRepository(db_session)
        await repo.create_run(EvolutionRunModel(workspace_id=WS_A))
        await repo.create_run(EvolutionRunModel(workspace_id=WS_B))

        runs_a = await repo.list_runs(WS_A)
        assert len(runs_a) == 1
        assert runs_a[0].workspace_id == WS_A


class TestCandidateQueries:
    async def test_candidates_scoped_to_workspace(self, db_session: AsyncSession) -> None:
        await _add_candidate(db_session, WS_A, name="verify-tool-args")
        await _add_candidate(db_session, WS_B, name="verify-tool-args")

        repo = EvolutionRepository(db_session)
        candidates_a = await repo.list_candidates(WS_A)

        assert len(candidates_a) == 1
        assert candidates_a[0].workspace_id == WS_A

    async def test_find_active_excludes_terminal_states(self, db_session: AsyncSession) -> None:
        await _add_candidate(db_session, WS_A, name="theme-x", status="pending")
        await _add_candidate(db_session, WS_A, name="theme-x", status="published")
        await _add_candidate(db_session, WS_A, name="theme-x", status="rejected")
        await _add_candidate(db_session, WS_A, name="theme-x", status="insufficient_data")

        repo = EvolutionRepository(db_session)
        active = await repo.find_active_candidates_by_name(WS_A, "theme-x")

        assert {row.status for row in active} == {"pending", "insufficient_data"}

    async def test_find_active_scoped_to_workspace(self, db_session: AsyncSession) -> None:
        await _add_candidate(db_session, WS_B, name="shared-theme", status="pending")

        repo = EvolutionRepository(db_session)
        assert await repo.find_active_candidates_by_name(WS_A, "shared-theme") == []

    async def test_list_candidates_status_filter(self, db_session: AsyncSession) -> None:
        await _add_candidate(db_session, WS_A, name="a", status="validated")
        await _add_candidate(db_session, WS_A, name="b", status="pending")

        repo = EvolutionRepository(db_session)
        validated = await repo.list_candidates(WS_A, status="validated")

        assert [row.name for row in validated] == ["a"]
