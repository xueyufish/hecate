"""Evolution meta-agent: scheduled entry point for the self-evolution loop.

Registered on the MetaAgentScheduler when ``SKILL_EVOLUTION_ENABLED`` is
set. Each tick harvests learning inputs and runs one pipeline cycle per
workspace that has accumulated pending inputs.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from hecate.studio.self_evolution.harvest import LearningInputHarvester
from hecate.studio.self_evolution.pipeline import EvolutionPipeline
from hecate.studio.self_evolution.repository import EvolutionRepository

logger = logging.getLogger(__name__)


class EvolutionAgent:
    """Periodic driver: harvest → run pipeline per workspace."""

    def __init__(self, event_store: Any | None = None) -> None:
        self._event_store = event_store

    async def run(self, db: Any) -> None:
        """One scheduler tick over all workspaces with recent quality data."""
        """One scheduler tick over all workspaces with recent quality data."""
        from hecate.models.conversation import ConversationModel

        workspace_ids = (
            (
                await db.execute(
                    select(ConversationModel.workspace_id)
                    .where(
                        ConversationModel.quality_scored_at.isnot(None),
                        ~ConversationModel.deleted,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )

        harvester = LearningInputHarvester(db, self._event_store)
        for workspace_id in workspace_ids:
            try:
                await harvester.harvest_recent_conversations(workspace_id)
                pending = await EvolutionRepository(db).list_pending_inputs(workspace_id, limit=1)
                if not pending:
                    continue
                pipeline = EvolutionPipeline(db, self._event_store, eval_runner=_build_eval_runner(db))
                await pipeline.run(workspace_id)
            except Exception:
                logger.exception("Evolution tick failed for workspace %s", workspace_id)


def _build_eval_runner(db: Any) -> Any:
    """Production eval_runner for the gate, honoring the wiring flag.

    Returns an :class:`OfflineEvaluationRunner` when
    ``SKILL_EVOLUTION_EVAL_WIRING_ENABLED`` is on; ``None`` (gate skips the
    behavioral checks, pre-wiring semantics) otherwise. The adapter itself
    degrades to skipped when the agent has no bound offline evaluation
    task — binding a task is the per-agent opt-in.
    """
    from hecate.core.config import settings

    if not settings.SKILL_EVOLUTION_EVAL_WIRING_ENABLED:
        return None
    from hecate.studio.self_evolution.eval_runner import OfflineEvaluationRunner

    return OfflineEvaluationRunner(db)
