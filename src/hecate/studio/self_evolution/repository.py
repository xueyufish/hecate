"""Workspace-scoped persistence for the self-evolution loop.

All queries filter by ``workspace_id`` — learning inputs, runs, and
candidates never cross workspace boundaries (see the skill-evolution-pipeline
spec). Soft-deleted rows are excluded everywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionInputModel,
    EvolutionRunModel,
    SkillCandidateModel,
)


class EvolutionRepository:
    """CRUD access to evolution loop rows, always scoped to a workspace."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # --- learning inputs -------------------------------------------------

    async def create_input(self, input_row: EvolutionInputModel) -> EvolutionInputModel:
        """Persist a harvested learning input."""
        self._db.add(input_row)
        await self._db.flush()
        return input_row

    async def get_input(self, workspace_id: UUID, input_id: UUID) -> EvolutionInputModel | None:
        """Fetch one learning input by workspace and ID."""
        result = await self._db.execute(
            select(EvolutionInputModel).where(
                EvolutionInputModel.id == input_id,
                EvolutionInputModel.workspace_id == workspace_id,
                ~EvolutionInputModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def list_pending_inputs(self, workspace_id: UUID, limit: int = 50) -> list[EvolutionInputModel]:
        """List pending inputs awaiting a run, oldest first."""
        result = await self._db.execute(
            select(EvolutionInputModel)
            .where(
                EvolutionInputModel.workspace_id == workspace_id,
                EvolutionInputModel.status == "pending",
                ~EvolutionInputModel.deleted,
            )
            .order_by(EvolutionInputModel.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_inputs(self, workspace_id: UUID, limit: int = 100) -> list[EvolutionInputModel]:
        """List all learning inputs of a workspace, newest first."""
        result = await self._db.execute(
            select(EvolutionInputModel)
            .where(
                EvolutionInputModel.workspace_id == workspace_id,
                ~EvolutionInputModel.deleted,
            )
            .order_by(EvolutionInputModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- runs -------------------------------------------------------------

    async def create_run(self, run: EvolutionRunModel) -> EvolutionRunModel:
        """Persist a new evolution run in pending state."""
        run.started_at = datetime.now(UTC)
        self._db.add(run)
        await self._db.flush()
        return run

    async def get_run(self, workspace_id: UUID, run_id: UUID) -> EvolutionRunModel | None:
        """Fetch one run by workspace and ID."""
        result = await self._db.execute(
            select(EvolutionRunModel).where(
                EvolutionRunModel.id == run_id,
                EvolutionRunModel.workspace_id == workspace_id,
                ~EvolutionRunModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def list_runs(self, workspace_id: UUID, limit: int = 50) -> list[EvolutionRunModel]:
        """List runs of a workspace, newest first."""
        result = await self._db.execute(
            select(EvolutionRunModel)
            .where(
                EvolutionRunModel.workspace_id == workspace_id,
                ~EvolutionRunModel.deleted,
            )
            .order_by(EvolutionRunModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # --- candidates ---------------------------------------------------------

    async def create_candidate(self, candidate: SkillCandidateModel) -> SkillCandidateModel:
        """Persist a new skill candidate."""
        self._db.add(candidate)
        await self._db.flush()
        return candidate

    async def get_candidate(self, workspace_id: UUID, candidate_id: UUID) -> SkillCandidateModel | None:
        """Fetch one candidate by workspace and ID."""
        result = await self._db.execute(
            select(SkillCandidateModel).where(
                SkillCandidateModel.id == candidate_id,
                SkillCandidateModel.workspace_id == workspace_id,
                ~SkillCandidateModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def find_active_candidates_by_name(self, workspace_id: UUID, name: str) -> list[SkillCandidateModel]:
        """Find non-terminal candidates with the same name for merge decisions.

        Terminal states (published/rejected) are excluded: a rejected theme
        may be re-proposed by new evidence, and published themes are managed
        through skill updates instead.
        """
        result = await self._db.execute(
            select(SkillCandidateModel).where(
                SkillCandidateModel.workspace_id == workspace_id,
                SkillCandidateModel.name == name,
                SkillCandidateModel.status.in_(["pending", "validating", "insufficient_data"]),
                ~SkillCandidateModel.deleted,
            )
        )
        return list(result.scalars().all())

    async def list_candidates(
        self,
        workspace_id: UUID,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SkillCandidateModel]:
        """List candidates of a workspace, optionally filtered by status."""
        conditions = [
            SkillCandidateModel.workspace_id == workspace_id,
            ~SkillCandidateModel.deleted,
        ]
        if status is not None:
            conditions.append(SkillCandidateModel.status == status)
        result = await self._db.execute(
            select(SkillCandidateModel).where(*conditions).order_by(SkillCandidateModel.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
