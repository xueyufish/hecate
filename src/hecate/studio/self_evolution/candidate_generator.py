"""Candidate skill generation from attribution results.

Creates or updates knowledge-only candidate skills: new themes become
candidates, recurring themes are merged as structured deltas (version
increments, evidence accumulates). Executable content is rejected at
generation time; everything else passes the content scanner before the
gate.
"""

from __future__ import annotations

import logging
import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionInputModel,
    SkillCandidateModel,
    SkillCandidateStatus,
)
from hecate.studio.self_evolution.attribution import AttributionResult
from hecate.studio.self_evolution.repository import EvolutionRepository
from hecate.studio.self_evolution.scanner import CandidateContentScanner

logger = logging.getLogger(__name__)

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,62}$")

# Learned skills are knowledge-only (no executable surface in v1). Any
# fenced code block is rejected outright — markdown prose only.
_CODE_FENCE_PATTERN = re.compile(r"^\s*```", re.MULTILINE)


class CandidateGenerator:
    """Turns attribution results into persisted candidate skills."""

    def __init__(
        self,
        db: AsyncSession,
        scanner: CandidateContentScanner | None = None,
    ) -> None:
        self._db = db
        self._repo = EvolutionRepository(db)
        self._scanner = scanner or CandidateContentScanner()

    async def generate_or_update(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID | None,
        source_input: EvolutionInputModel,
        attribution: AttributionResult,
    ) -> SkillCandidateModel | None:
        """Create a candidate or merge a delta into an existing theme.

        Returns ``None`` when the attribution is not learnable or the draft
        was rejected (reason recorded on the source input).
        """
        if not attribution.learnable:
            source_input.status = "attributed"
            return None

        draft = attribution.skill_draft or {}
        name = _sanitize_name(str(draft.get("name", "")))
        if name is None:
            source_input.status = "failed"
            source_input.error = "candidate_rejected: invalid skill name"
            return None

        body = "\n".join(str(draft.get(key, "")) for key in ("description", "procedure", "guardrails"))
        if _CODE_FENCE_PATTERN.search(body):
            source_input.status = "failed"
            source_input.error = "candidate_rejected: executable content in knowledge-only skill"
            logger.info("Rejected candidate draft '%s' containing executable content", name)
            return None

        verdict = await self._scanner.scan(body)
        delta_entry = {
            "content": body,
            "root_cause": attribution.root_cause,
            "confidence": attribution.confidence,
            "evidence": attribution.evidence_refs,
            "source_input_id": str(source_input.id),
        }

        existing = await self._repo.find_active_candidates_by_name(workspace_id, name)
        if existing:
            candidate = existing[0]
            candidate.deltas = list(candidate.deltas or []) + [delta_entry]
            candidate.version += 1
            if verdict.status == "blocked":
                candidate.status = SkillCandidateStatus.BLOCKED.value
                candidate.scan_status = "blocked"
                candidate.scan_detail = {"findings": verdict.findings}
            await self._db.flush()
            logger.info("Merged delta into candidate '%s' (version %d)", name, candidate.version)
            return candidate

        initial_status = (
            SkillCandidateStatus.BLOCKED.value if verdict.status == "blocked" else SkillCandidateStatus.PENDING.value
        )
        candidate = SkillCandidateModel(
            workspace_id=workspace_id,
            run_id=run_id,
            name=name,
            description=str(draft.get("description", "")),
            procedure=str(draft.get("procedure", "")),
            guardrails=str(draft.get("guardrails", "")),
            failure_category=attribution.category.value,
            confidence=attribution.confidence,
            evidence=list(attribution.evidence_refs),
            deltas=[delta_entry],
            scan_status=verdict.status if verdict.status == "blocked" else "clean",
            scan_detail={"findings": verdict.findings} if verdict.findings else None,
            status=initial_status,
        )
        created = await self._repo.create_candidate(candidate)
        source_input.status = "attributed"
        source_input.run_id = run_id
        logger.info("Created candidate '%s' (%s)", name, initial_status)
        return created


def _sanitize_name(raw: str) -> str | None:
    """Normalize a model-proposed slug to the skill name pattern."""
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not _NAME_PATTERN.match(slug):
        return None
    return slug
