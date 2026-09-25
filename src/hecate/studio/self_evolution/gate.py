"""Validation gate for candidate skills (skill-evolution-gate spec).

Four checks produce a structured report; any executed check that fails
blocks the candidate from review. Checks may be ``skipped`` with an
explicit reason (recorded in the report) — only golden-subset availability
is mandatory: without enough golden samples the candidate is suspended as
``insufficient_data`` rather than silently approved.

- ``dataset_regression`` / ``with_without_baseline`` run through an injected
  ``eval_runner`` adapter (backed by the evaluation framework in production
  wiring); skipped when no runner or no bound evaluation task exists.
- ``golden_subset_regression`` and ``trigger_test`` are LLM-judged over the
  agent's frozen golden samples (design D10 for the trigger decision).

When the behavioral checks (``dataset_regression`` / ``with_without_baseline``)
were skipped but every executed check passed, the overall outcome is
``pass_unverified`` — content checks alone must not present as fully
validated. The candidate still reaches human review; the report says what
was actually verified.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evidence import EvidenceModel
from hecate.models.evolution import EvolutionGoldenSubsetModel
from hecate.models.session import SessionModel

logger = logging.getLogger(__name__)

DEFAULT_GOLDEN_SAMPLES = 10
DEFAULT_MIN_GOLDEN = 3
BASELINE_REGRESSION_TOLERANCE = 0.05


def candidate_content_hash(candidate: Any) -> str:
    """Stable hash of the candidate content the gate verdict covers.

    Bound into ``validation_report.content_hash`` at validation time so the
    review service can detect content drift between gate and review (B3
    review-revalidation): an ``approved_with_edits`` path that changes the
    validated content marks the report stale. Covers the fields review can
    edit plus the failure category the published skill carries.
    """
    payload = {
        "name": candidate.name,
        "description": candidate.description,
        "procedure": candidate.procedure,
        "guardrails": candidate.guardrails,
        "failure_category": candidate.failure_category,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


@dataclass
class GateReport:
    """Aggregated gate outcome for one candidate."""

    overall: str  # pass | pass_unverified | fail | insufficient_data
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serializable report stored on the candidate row."""
        return {"overall": self.overall, "checks": self.checks}


class GoldenSubsetBuilder:
    """Freezes a per-agent golden subset from conversations with evidence."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def build(
        self,
        workspace_id: UUID,
        agent_id: UUID,
        *,
        max_samples: int = DEFAULT_GOLDEN_SAMPLES,
    ) -> int:
        """Replace the agent's golden subset with fresh frozen samples.

        Returns the number of samples frozen.
        """
        result = await self._db.execute(
            select(EvidenceModel.conversation_id, func.count(EvidenceModel.id))
            .join(SessionModel, EvidenceModel.session_id == SessionModel.id)
            .where(
                EvidenceModel.workspace_id == workspace_id,
                SessionModel.agent_id == agent_id,
                EvidenceModel.conversation_id.isnot(None),
                ~EvidenceModel.deleted,
            )
            .group_by(EvidenceModel.conversation_id)
            .order_by(func.count(EvidenceModel.id).desc())
            .limit(max_samples)
        )
        samples = [(conversation_id, count) for conversation_id, count in result.all() if conversation_id is not None]

        existing = await self._db.execute(
            select(EvolutionGoldenSubsetModel).where(
                EvolutionGoldenSubsetModel.workspace_id == workspace_id,
                EvolutionGoldenSubsetModel.agent_id == agent_id,
                ~EvolutionGoldenSubsetModel.deleted,
            )
        )
        for row in existing.scalars().all():
            row.deleted = True
            row.deleted_at = datetime.now(UTC)

        now = datetime.now(UTC)
        for conversation_id, count in samples:
            self._db.add(
                EvolutionGoldenSubsetModel(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    message_count=int(count),
                    frozen_at=now,
                )
            )
        await self._db.flush()
        logger.info("Froze %d golden samples for agent %s", len(samples), agent_id)
        return len(samples)


class EvolutionGate:
    """Runs the four validation checks over a candidate skill."""

    def __init__(
        self,
        db: AsyncSession,
        eval_runner: Any | None = None,
        *,
        judge_fn: Any | None = None,
        judge_model: str | None = None,
        min_golden: int = DEFAULT_MIN_GOLDEN,
        baseline_threshold: float = 0.6,
    ) -> None:
        self._db = db
        self._eval_runner = eval_runner
        self._judge_fn = judge_fn
        if judge_model is None:
            from hecate.core.config import settings

            judge_model = settings.SKILL_EVOLUTION_ATTRIBUTION_MODEL
        self._judge_model = judge_model
        self._min_golden = min_golden
        self._baseline_threshold = baseline_threshold

    async def validate(
        self,
        candidate: Any,  # SkillCandidateModel
        workspace_id: UUID,
        agent_id: UUID,
    ) -> GateReport:
        """Run all checks and return the aggregated report."""
        report = GateReport(overall="pass")

        golden = await self._load_golden(workspace_id, agent_id)

        # 2. Golden subset non-regression
        if len(golden) < self._min_golden:
            report.checks.append(self._check("golden_subset_regression", "skipped", "insufficient_golden_samples"))
            report.checks.append(self._check("trigger_test", "skipped", "insufficient_golden_samples"))
            report.overall = "insufficient_data"
        else:
            degradation = await self._judge_golden_degradation(candidate, golden)
            report.checks.append(
                self._check(
                    "golden_subset_regression",
                    "pass" if not degradation else "fail",
                    {"degraded_samples": degradation},
                )
            )
            trigger = await self._judge_trigger(candidate, golden)
            report.checks.append(
                self._check(
                    "trigger_test",
                    "pass" if trigger else "fail",
                    {"detail": "would-load votes did not meet majority"},
                )
            )
            if degradation or not trigger:
                report.overall = "fail"

        # 1 + 4. Dataset regression and with/without baseline via eval runner
        if self._eval_runner is None:
            report.checks.append(self._check("dataset_regression", "skipped", "no_eval_runner"))
            report.checks.append(self._check("with_without_baseline", "skipped", "no_eval_runner"))
        else:
            with_score = await self._run_eval(candidate, bind_skill=True, agent_id=agent_id)
            without_score = await self._run_eval(candidate, bind_skill=False, agent_id=agent_id)
            if with_score is None or without_score is None:
                report.checks.append(self._check("dataset_regression", "skipped", "runner_returned_none"))
                report.checks.append(self._check("with_without_baseline", "skipped", "runner_returned_none"))
            else:
                regression_ok = with_score >= without_score - BASELINE_REGRESSION_TOLERANCE
                improvement = with_score > without_score
                threshold_ok = with_score >= self._baseline_threshold
                report.checks.append(
                    self._check(
                        "dataset_regression",
                        "pass" if threshold_ok else "fail",
                        {"pass_rate": with_score, "threshold": self._baseline_threshold},
                    )
                )
                report.checks.append(
                    self._check(
                        "with_without_baseline",
                        "pass" if improvement else "fail",
                        {"with_skill": with_score, "without_skill": without_score},
                    )
                )
                if not (regression_ok and threshold_ok and improvement):
                    report.overall = "fail"

        # 4.21 path c — reflection participation. When the candidate
        # skill references one or more reflection ids (carried on
        # ``candidate.evidence`` or ``candidate.linked_reflection_ids``),
        # the gate checks the reflection layer is still ``approved``
        # and within confidence bounds. This guards against an
        # approved skill that pivots on a deprecated reflection.
        try:
            reflection_check = await self._check_reflection_relevance(candidate)
        except Exception as e:  # pragma: no cover — best-effort
            logger.warning("EvolutionGate reflection_relevance failed: %s", e)
            reflection_check = ("reflection_relevance", "skipped", {"error": str(e)})
        report.checks.append(self._check(*reflection_check))
        if reflection_check[1] == "fail":
            report.overall = "fail"

        if report.overall == "pass":
            has_fail = any(c["status"] == "fail" for c in report.checks)
            if has_fail:
                report.overall = "fail"
            elif any(
                c["name"] in ("dataset_regression", "with_without_baseline") and c["status"] == "skipped"
                for c in report.checks
            ):
                report.overall = "pass_unverified"
        return report

    # --- helpers -----------------------------------------------------------

    async def _check_reflection_relevance(self, candidate: Any) -> tuple[str, str, dict[str, Any]]:
        """Path c — verify every reflection referenced by ``candidate`` is still approved.

        Reads ``candidate.linked_reflection_ids`` (list of UUIDs); each
        id is looked up in ``reflections`` and its ``status`` must be
        ``approved`` and ``confidence >= 0.4``. A single non-approved
        reflection flips the check to ``fail`` with the offending ids
        in the detail payload so the caller can surface them.
        """
        from hecate.core.config import settings
        from hecate.models.task_memory import (
            REFLECTION_DEPRECATION_CONFIDENCE,
            REFLECTION_STATUS_APPROVED,
            REFLECTION_STATUS_DEPRECATED,
            ReflectionModel,
        )

        ids: list[uuid.UUID] = list(getattr(candidate, "linked_reflection_ids", []) or [])
        if not ids:
            return ("reflection_relevance", "skipped", {"reason": "no_linked_reflections"})
        if not settings.REFLECTION_ENABLED:
            return (
                "reflection_relevance",
                "skipped",
                {"reason": "REFLECTION_ENABLED is off", "reflection_ids": [str(i) for i in ids]},
            )

        from sqlalchemy import select

        stmt = select(ReflectionModel.id, ReflectionModel.status, ReflectionModel.confidence).where(
            ReflectionModel.id.in_(ids)
        )
        rows = list((await self._db.execute(stmt)).all())
        by_id = {r[0]: (r[1], r[2]) for r in rows}
        bad: list[dict[str, Any]] = []
        for rid in ids:
            status, conf = by_id.get(rid, (None, None))
            if status is None:
                bad.append({"reflection_id": str(rid), "reason": "missing"})
            elif status == REFLECTION_STATUS_DEPRECATED:
                bad.append({"reflection_id": str(rid), "reason": "deprecated"})
            elif status != REFLECTION_STATUS_APPROVED:
                bad.append({"reflection_id": str(rid), "reason": f"status={status}"})
            elif (conf or 0.0) < REFLECTION_DEPRECATION_CONFIDENCE:
                bad.append({"reflection_id": str(rid), "reason": "low_confidence"})
        if bad:
            return ("reflection_relevance", "fail", {"offending_reflections": bad})
        return (
            "reflection_relevance",
            "pass",
            {"checked": len(ids), "all_approved": True},
        )

    def _check(self, name: str, status: str, detail: Any) -> dict[str, Any]:
        """Build one check entry for the report."""
        return {"name": name, "status": status, "detail": detail}

    async def _load_golden(self, workspace_id: UUID, agent_id: UUID) -> list[EvolutionGoldenSubsetModel]:
        """Load the agent's active golden samples."""
        result = await self._db.execute(
            select(EvolutionGoldenSubsetModel).where(
                EvolutionGoldenSubsetModel.workspace_id == workspace_id,
                EvolutionGoldenSubsetModel.agent_id == agent_id,
                ~EvolutionGoldenSubsetModel.deleted,
            )
        )
        return list(result.scalars().all())

    async def _judge_golden_degradation(self, candidate: Any, golden: list[EvolutionGoldenSubsetModel]) -> list[UUID]:
        """Ask the judge whether the skill would degrade each golden sample."""
        degraded: list[UUID] = []
        for sample in golden:
            answer = await self._ask_judge(
                "Would applying the skill guidance below degrade or contradict any "
                "correct behaviour in the trajectory? Answer YES or NO.\n\n"
                f"Skill: {candidate.name}\nGuidance: {candidate.procedure}\n"
                f"Guardrails: {candidate.guardrails}",
                sample,
            )
            if "yes" in answer.lower():
                degraded.append(sample.conversation_id)
        return degraded

    async def _judge_trigger(self, candidate: Any, golden: list[EvolutionGoldenSubsetModel]) -> bool:
        """Majority would-load vote across golden samples (design D10)."""
        votes = 0
        for sample in golden:
            answer = await self._ask_judge(
                "An agent's skill catalog advertises the skill below. Based on the "
                "trajectory, would the agent need to load this skill? Answer YES or NO.\n\n"
                f"Skill: {candidate.name}\n"
                f"Description: {candidate.description}",
                sample,
            )
            votes += 1 if "yes" in answer.lower() else 0
        return votes > len(golden) / 2

    async def _ask_judge(self, instruction: str, sample: EvolutionGoldenSubsetModel) -> str:
        """One judge call over a golden sample transcript."""
        from hecate.ops.ops_center.conversation_messages import (
            project_conversation_messages,
        )

        try:
            messages = await project_conversation_messages(self._db, sample.conversation_id, None)
        except Exception:
            logger.warning(
                "Transcript projection failed for golden sample %s",
                sample.conversation_id,
                exc_info=True,
            )
            return "no"

        transcript = "\n".join(f"[{m.get('role', '?')}] {str(m.get('content', ''))[:400]}" for m in messages[:30])

        if self._judge_fn is not None:
            return await self._judge_fn(instruction, transcript)

        from hecate_llm.service import llm_service

        try:
            response = await llm_service.chat(
                messages=[
                    {
                        "role": "user",
                        "content": f"{instruction}\n\nTrajectory:\n{transcript}",
                    }
                ],
                model=self._judge_model,
                temperature=0.0,
                max_tokens=10,
                timeout=30.0,
            )
        except Exception:
            logger.warning("Gate judge call failed", exc_info=True)
            return "no"
        return str(getattr(response, "content", "") or "")

    async def _run_eval(self, candidate: Any, *, bind_skill: bool, agent_id: UUID) -> float | None:
        """Delegate to the injected eval runner; None when not runnable.

        The runner contract is ``async (candidate, *, bind_skill, agent_id)``
        — one behavioral-evaluation leg per call (the gate invokes it twice:
        with and without the candidate skill bound).
        """
        try:
            return await self._eval_runner(candidate, bind_skill=bind_skill, agent_id=agent_id)
        except Exception:
            logger.warning("Eval runner failed", exc_info=True)
            return None
