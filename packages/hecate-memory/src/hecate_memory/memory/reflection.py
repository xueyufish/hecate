"""Sleep-time reflection engine — 4.21 ReflectionEngine + four quality gates.

The reflection engine is the *sister mechanism* to the consolidation engine
(``memory_consolidation``): both share the same ``ExtractFn / PlanFn /
ScanFn / EmbedFn`` seam type aliases from ``consolidation.py``; both
flow through the same scheduler's advisory-lock plumbing; both emit
append-only audit rows. They differ in what they operate on:

- **Consolidation** — conversation transcripts → L3 user memory / L4
  knowledge memory / L1 ``learned_context`` block. Operates on
  free-form facts about users.
- **Reflection** — closed ``episodes`` rows → ``reflections`` rows
  (typed records with ``title / use_cases / hints / confidence``) +
  Work Context Graph nodes (Group 6). Operates on the agent's *experience*
  (what worked, what failed, how the agent should approach similar work
  next time).

Pipeline per reflection unit ``(workspace_id, agent_id, actor_id | null,
team_id | null)`` — four stages with quality gates interleaved:

1. **Reflect** (LLM, gated by capability) — extract typed reflection
   candidates from the closed episodes in the unit's review window.
2. **Score & dedupe** (code-side) — code-side checks:
   - ``source_episode_ids >= 2`` (gate 3)
   - ``hints`` ≤ 300 words (gate 1's output contract)
   - dedupe against existing reflections by ``title`` cosine / exact-text
     fallback.
3. **Plan & judge** (LLM-as-Judge) — emit ``isrel / issup / isuse`` per
   survivor. Code-side: ``all three > 0.5`` flips status to ``approved``;
   any single one below the floor flips status to ``rejected`` with a
   per-rejection reason code.
4. **Apply** — write the row(s) and (Group 6) create the matching Work
   Context Graph node. ``operator='update'`` when an active reflection
   with the same ``title`` already exists (monotonic ``version``).

Quality gate 4 (confidence auto-deprecation) runs on a separate cadence:
a small ``ConfidenceEvaluator`` job that walks approved reflections and
increments ``deprecation_streak`` when ``confidence < 0.4``. After three
consecutive misses the reflection is ``status='deprecated'``.

Budgets live on ``ReflectionSettings`` (mirroring
``ConsolidationSettings``). Both engines honor ``LLM_CALLS_PER_RUN`` and
``MUTATIONS_PER_RUN`` ceilings.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.task_memory import (
    REFLECTION_GATE_ISREL_MIN,
    REFLECTION_GATE_ISSUP_MIN,
    REFLECTION_GATE_ISUSE_MIN,
    REFLECTION_GATE_SOURCE_EPISODE_MIN,
    REFLECTION_HINTS_WORD_CAP,
    REFLECTION_OPERATOR_ADD,
    REFLECTION_OPERATOR_UPDATE,
    REFLECTION_STATUS_APPROVED,
    REFLECTION_STATUS_DEPRECATED,
    EpisodeModel,
    ReflectionModel,
)
from hecate_memory.memory.consolidation import (
    EmbedFn,
    ScanFn,
    _as_uuid,
    _ensure_utc,
)

logger = logging.getLogger(__name__)


# ──────────────────────────── type aliases ────────────────────────────


# ReflectFn: same signature as PlanFn. The consolidation pipeline uses
# ExtractFn for fact extraction and PlanFn for plan emission; reflection
# uses a single call (ReflectFn) for both — the prompt is the difference.
ReflectFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]

# JudgeFn: emits one score triple per survivor. Returns the input list
# augmented with ``isrel / issup / isuse`` floats.
JudgeFn = Callable[[list[dict[str, Any]]], Awaitable[list[dict[str, Any]]]]


# ──────────────────────────── settings ────────────────────────────


@dataclass(frozen=True)
class ReflectionSettings:
    """Engine budgets and thresholds for one reflection run."""

    similarity_threshold: float = 0.85
    max_llm_calls: int = 6
    max_mutations: int = 30
    max_episodes_per_run: int = 24
    max_reflection_hints_words: int = REFLECTION_HINTS_WORD_CAP
    min_source_episodes: int = REFLECTION_GATE_SOURCE_EPISODE_MIN
    gate_isrel_min: float = REFLECTION_GATE_ISREL_MIN
    gate_issup_min: float = REFLECTION_GATE_ISSUP_MIN
    gate_isuse_min: float = REFLECTION_GATE_ISUSE_MIN


# ──────────────────────────── operations ────────────────────────────


@dataclass
class ReflectionOperation:
    """One row the reflection engine intends to write.

    ``op`` is one of:
    - ``"add"`` -- new reflection row, optionally also creating a
      Work Context Graph node (Group 6).
    - ``"update"`` -- increment an existing row's ``version`` and set
      ``superseded_by``; the new row is inserted with status pending
      and is judged in the same run.
    - ``"reject"`` -- write the row with ``status='rejected'`` and the
      supplied ``rejection_reason``. Used when gate 2 or gate 3 fires.
    """

    op: str
    payload: dict[str, Any]
    rejection_reason: str | None = None
    superseded_by_id: uuid.UUID | None = None
    operator_name: str = "add"


@dataclass
class ReflectionOutcome:
    """Per-candidate outcome recorded in ``reflection_runs.rejection_reasons``."""

    op: str
    rejection_reason: str | None
    reflection_id: uuid.UUID | None
    detail: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────── engine ────────────────────────────


REFLECTION_TRIGGER = "reflection_trigger"
REFLECTION_TRIGGER_SCHEDULE = "fixed_interval"
REFLECTION_TRIGGER_IDLE = "idle"
REFLECTION_TRIGGER_PRESSURE = "pressure_flag"


class ReflectionEngine:
    """Four-stage reflection engine; sister to ``ConsolidationEngine``.

    LLM seams are injected (``reflect`` / ``judge`` callables) so the
    composition root wires the production gateway and tests wire
    stubs; the optional ``scanner`` and ``embed`` seams are reused
    from consolidation (one scanner, one embedder, two engines).
    """

    def __init__(
        self,
        reflect: ReflectFn,
        judge: JudgeFn,
        *,
        scanner: ScanFn | None = None,
        embed: EmbedFn | None = None,
        settings: ReflectionSettings | None = None,
    ) -> None:
        self._reflect = reflect
        self._judge = judge
        self._scanner = scanner
        self._embed = embed
        self.settings = settings or ReflectionSettings()

    # ───────────── public entry ─────────────

    async def run_unit(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
        window_start: datetime,
        window_end: datetime,
        trigger: str = REFLECTION_TRIGGER,
    ) -> dict[str, Any]:
        """Reflect over the closed episodes in the unit's review window.

        Returns a summary dict for the caller / scheduler; the engine
        does not write a ``reflection_runs`` row directly — that audit
        row is the caller's responsibility (mirroring how
        ``ConsolidationEngine.run_unit`` returns a ``ConsolidationRunModel``
        and the caller persists it).
        """
        start = _ensure_utc(window_start)
        end = _ensure_utc(window_end)
        outcomes: list[ReflectionOutcome] = []
        adopted = 0
        rejected = 0
        llm_calls = 0
        degraded = False

        episodes = await self._load_closed_episodes(db, workspace_id, agent_id, actor_id, team_id, start, end)
        if not episodes:
            return self._summary(
                episodes=[],
                candidates=0,
                adopted=0,
                rejected=0,
                llm_calls=0,
                degraded=False,
                outcomes=outcomes,
            )
        episodes = episodes[: self.settings.max_episodes_per_run]

        # ── stage 1: reflect ──
        try:
            candidates = await self._reflect(self._reflect_payload(workspace_id, agent_id, episodes))
        except Exception as e:
            logger.warning("ReflectionEngine reflect() failed: %s", e)
            return self._summary(
                episodes=episodes,
                candidates=0,
                adopted=0,
                rejected=len(episodes),
                llm_calls=llm_calls,
                degraded=True,
                outcomes=outcomes,
            )
        llm_calls += 1
        if llm_calls > self.settings.max_llm_calls:
            degraded = True

        # ── stage 2: score & dedupe (code-side) ──
        survivors: list[dict[str, Any]] = []
        for cand in candidates or []:
            rejection = await self._gate1_score(cand)
            if rejection is not None:
                outcomes.append(
                    ReflectionOutcome(
                        op="reject",
                        rejection_reason=rejection,
                        reflection_id=None,
                        detail={"title": cand.get("title")},
                    )
                )
                rejected += 1
                continue
            survivors.append(cand)
        existing_titles = await self._load_existing_titles(db, workspace_id, agent_id)

        # ── stage 3: judge (LLM-as-Judge) ──
        if survivors:
            try:
                judged = await self._judge(self._judge_payload(workspace_id, agent_id, survivors))
            except Exception as e:
                logger.warning("ReflectionEngine judge() failed: %s", e)
                judged = survivors  # degrade: fall back to no scores
                degraded = True
            llm_calls += 1
        else:
            judged = []

        for cand, scores in zip(survivors, judged or [], strict=False):
            cand.setdefault("isrel", scores.get("isrel"))
            cand.setdefault("issup", scores.get("issup"))
            cand.setdefault("isuse", scores.get("isuse"))
            cand.setdefault("confidence", scores.get("confidence", 0.5))
            rejection = self._gate2_judge(cand)
            if rejection is not None:
                outcomes.append(
                    ReflectionOutcome(
                        op="reject",
                        rejection_reason=rejection,
                        reflection_id=None,
                        detail={"title": cand.get("title")},
                    )
                )
                rejected += 1
                continue
            gate3 = self._gate3_source_episodes(cand)
            if gate3 is not None:
                outcomes.append(
                    ReflectionOutcome(
                        op="reject",
                        rejection_reason=gate3,
                        reflection_id=None,
                        detail={"title": cand.get("title")},
                    )
                )
                rejected += 1
                continue

            # ── stage 4: apply ──
            operation = self._plan_operation(cand, existing_titles)
            try:
                reflection_id = await self._apply(db, cand, operation)
            except Exception as e:
                logger.warning("ReflectionEngine apply() failed for title=%s: %s", cand.get("title"), e)
                degraded = True
                continue
            existing_titles.discard(cand.get("title", "").lower())
            outcomes.append(
                ReflectionOutcome(
                    op=operation.op,
                    rejection_reason=None,
                    reflection_id=reflection_id,
                    detail={"title": cand.get("title"), "operator": operation.operator_name},
                )
            )
            adopted += 1
            if llm_calls > self.settings.max_llm_calls or adopted > self.settings.max_mutations:
                degraded = True
                break

        return self._summary(
            episodes=episodes,
            candidates=len(candidates or []),
            adopted=adopted,
            rejected=rejected,
            llm_calls=llm_calls,
            degraded=degraded,
            outcomes=outcomes,
        )

    # ───────────── quality gates ─────────────

    async def _gate1_score(self, cand: dict[str, Any]) -> str | None:
        """Pre-LLM-as-Judge score & dedupe gate (gate 1 + gate 3 pre-check).

        Returns ``None`` when the candidate passes; a short rejection
        reason code (``'hints_too_long'`` / ``'single_episode_hallucination'`` /
        ``'injection_detected'``) otherwise. Security scan uses the same
        shared ``ScanFn`` as consolidation so a single scanner instance
        covers both pipelines.
        """
        hints = (cand.get("hints") or "").strip()
        if not hints:
            return "hints_empty"
        if _word_count(hints) > self.settings.max_reflection_hints_words:
            return "hints_too_long"
        episode_ids = [_as_uuid(e) for e in cand.get("source_episode_ids") or []]
        episode_ids = [e for e in episode_ids if e is not None]
        if len(episode_ids) < self.settings.min_source_episodes:
            return "single_episode_hallucination"
        if self._scanner is not None:
            try:
                verdict = await self._scanner(hints)
            except Exception as e:
                logger.warning("ReflectionEngine scanner raised: %s", e)
                verdict = None
            if verdict is not None:
                return verdict  # scanner-supplied reason code
        return None

    def _gate2_judge(self, cand: dict[str, Any]) -> str | None:
        """LLM-as-Judge three-token gate (gate 2)."""
        try:
            isrel = float(cand.get("isrel"))
            issup = float(cand.get("issup"))
            isuse = float(cand.get("isuse"))
        except (TypeError, ValueError):
            return "missing_judge_scores"
        if isrel < self.settings.gate_isrel_min:
            return "low_isrel_score"
        if issup < self.settings.gate_issup_min:
            return "low_issup_score"
        if isuse < self.settings.gate_isuse_min:
            return "low_isuse_score"
        return None

    @staticmethod
    def _gate3_source_episodes(cand: dict[str, Any]) -> str | None:
        """Code-side gate 3 — redundant with gate 1's check but kept as
        the second pass after judge() merged scores into the candidate.
        Any drift between stage 2 and stage 3 (e.g. a typo in judge
        output) is caught here.
        """
        ids = cand.get("source_episode_ids") or []
        return None if len(ids) >= REFLECTION_GATE_SOURCE_EPISODE_MIN else "single_episode_hallucination"

    # ───────────── stage 4 helpers ─────────────

    def _plan_operation(
        self,
        cand: dict[str, Any],
        existing_titles: set[str],
    ) -> ReflectionOperation:
        """Decide add vs update based on title collision."""
        title_lc = (cand.get("title") or "").strip().lower()
        if title_lc in existing_titles:
            return ReflectionOperation(
                op=REFLECTION_OPERATOR_UPDATE,
                payload=cand,
                operator_name="update",
            )
        return ReflectionOperation(
            op=REFLECTION_OPERATOR_ADD,
            payload=cand,
            operator_name="add",
        )

    async def _apply(
        self,
        db: AsyncSession,
        cand: dict[str, Any],
        operation: ReflectionOperation,
    ) -> uuid.UUID:
        new_id = uuid.uuid4()
        episode_ids = [_as_uuid(e) for e in cand.get("source_episode_ids") or []]
        episode_ids = [e for e in episode_ids if e is not None]

        if operation.op == REFLECTION_OPERATOR_UPDATE:
            existing_id = await self._lookup_existing_id(
                db,
                workspace_id=cand.get("workspace_id"),
                agent_id=cand.get("agent_id"),
                title=(cand.get("title") or "").strip(),
            )
            if existing_id is not None:
                # Flip the prior reflection's linked graph nodes to
                # ``active=False`` first. The supersession edge is
                # written AFTER the new node row goes in
                # (see ``create_node_for_reflection`` below).
                from hecate_memory.memory.work_context_graph import (
                    supersede_nodes_for_reflection,
                )

                await supersede_nodes_for_reflection(db, reflection_id=existing_id)
                await db.execute(
                    update(ReflectionModel)
                    .where(ReflectionModel.id == existing_id)
                    .values(superseded_by=new_id)
                )

        row = ReflectionModel(
            id=new_id,
            workspace_id=cand["workspace_id"],
            agent_id=cand["agent_id"],
            actor_id=cand.get("actor_id"),
            session_id=cand.get("session_id"),
            title=(cand.get("title") or "").strip()[:120],
            use_cases=list(cand.get("use_cases") or []),
            hints=(cand.get("hints") or "").strip(),
            confidence=float(cand.get("confidence") or 0.5),
            source_episode_ids=[str(e) for e in episode_ids],
            operator=operation.op,
            superseded_by=None,
            version=2 if operation.op == REFLECTION_OPERATOR_UPDATE else 1,
            status=REFLECTION_STATUS_APPROVED,
            isrel=float(cand.get("isrel") or 0.0),
            issup=float(cand.get("issup") or 0.0),
            isuse=float(cand.get("isuse") or 0.0),
        )
        db.add(row)
        await db.flush()

        # Work Context Graph (KM6) write — same transaction, atomic.
        # The new node materializes the approved reflection; the
        # prior node was already flipped inactive above when
        # operator='update'. ``create_node_for_reflection`` only
        # writes for newly-approved rows; for ``update`` it points
        # the new node at the new reflection.
        from hecate_memory.memory.work_context_graph import (
            create_node_for_reflection,
            write_supersession_edge,
        )

        await create_node_for_reflection(
            db,
            reflection_id=new_id,
            workspace_id=row.workspace_id,
            agent_id=row.agent_id,
            title=row.title,
            hints=row.hints,
            use_cases=list(row.use_cases or []),
            confidence=row.confidence,
        )
        # For ``operator='update'``: now that both prior and new
        # graph nodes exist, write the corrected_by edge.
        if operation.op == REFLECTION_OPERATOR_UPDATE and existing_id is not None:
            await write_supersession_edge(
                db,
                prior_reflection_id=existing_id,
                new_reflection_id=new_id,
            )
        return new_id

    @staticmethod
    async def _lookup_existing_id(
        db: AsyncSession,
        *,
        workspace_id: Any,
        agent_id: Any,
        title: str,
    ) -> uuid.UUID | None:
        stmt = (
            select(ReflectionModel.id)
            .where(
                ReflectionModel.workspace_id == workspace_id,
                ReflectionModel.agent_id == agent_id,
                ReflectionModel.title == title,
                ReflectionModel.status != REFLECTION_STATUS_DEPRECATED,
                ReflectionModel.superseded_by.is_(None),
                ~ReflectionModel.deleted,
            )
            .order_by(ReflectionModel.version.desc())
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    # ───────────── data loaders ─────────────

    async def _load_closed_episodes(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        team_id: uuid.UUID | None,  # noqa: ARG002 — symmetric w/ consolidation unit
        start: datetime,
        end: datetime,
    ) -> list[EpisodeModel]:
        stmt = select(EpisodeModel).where(
            EpisodeModel.workspace_id == workspace_id,
            EpisodeModel.agent_id == agent_id,
            EpisodeModel.closed_at.is_not(None),
            EpisodeModel.closed_at >= start,
            EpisodeModel.closed_at <= end,
            ~EpisodeModel.deleted,
        )
        if actor_id is not None:
            stmt = stmt.where(EpisodeModel.actor_id == actor_id)
        stmt = stmt.order_by(EpisodeModel.closed_at.asc())
        return list((await db.execute(stmt)).scalars().all())

    async def _load_existing_titles(
        self,
        db: AsyncSession,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> set[str]:
        stmt = select(ReflectionModel.title).where(
            ReflectionModel.workspace_id == workspace_id,
            ReflectionModel.agent_id == agent_id,
            ReflectionModel.status != REFLECTION_STATUS_DEPRECATED,
            ReflectionModel.superseded_by.is_(None),
            ~ReflectionModel.deleted,
        )
        rows = (await db.execute(stmt)).scalars().all()
        return {str(t).strip().lower() for t in rows}

    # ───────────── payload builders ─────────────

    @staticmethod
    def _reflect_payload(workspace_id: uuid.UUID, agent_id: uuid.UUID, episodes: list[EpisodeModel]) -> dict[str, Any]:
        return {
            "workspace_id": str(workspace_id),
            "agent_id": str(agent_id),
            "episodes": [
                {
                    "id": str(ep.id),
                    "task_type": ep.task_type,
                    "situation": ep.situation,
                    "intent": ep.intent,
                    "actions": list(ep.actions or []),
                    "outcomes": list(ep.outcomes or []),
                    "closed_at": ep.closed_at.isoformat() if ep.closed_at else None,
                }
                for ep in episodes
            ],
        }

    @staticmethod
    def _judge_payload(
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        survivors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "workspace_id": str(workspace_id),
            "agent_id": str(agent_id),
            "candidates": [
                {
                    "title": c.get("title"),
                    "use_cases": c.get("use_cases"),
                    "hints": c.get("hints"),
                    "source_episode_ids": c.get("source_episode_ids"),
                }
                for c in survivors
            ],
        }

    @staticmethod
    def _summary(
        *,
        episodes: list[Any],
        candidates: int,
        adopted: int,
        rejected: int,
        llm_calls: int,
        degraded: bool,
        outcomes: list[ReflectionOutcome],
    ) -> dict[str, Any]:
        return {
            "episodes_considered": len(episodes),
            "candidates_extracted": candidates,
            "adopted": adopted,
            "rejected": rejected,
            "llm_calls": llm_calls,
            "degraded": degraded,
            "outcomes": [
                {
                    "op": o.op,
                    "rejection_reason": o.rejection_reason,
                    "reflection_id": str(o.reflection_id) if o.reflection_id else None,
                    "detail": o.detail,
                }
                for o in outcomes
            ],
        }


# ──────────────────────────── gate 4 (confidence evaluator) ────────────────────────────


@dataclass
class ConfidenceEvaluator:
    """Standalone gate-4 runner that walks approved reflections.

    Not part of the per-unit ``ReflectionEngine.run_unit`` flow: gate 4
    needs multi-pass history (deprecation_streak / last_confirmed_at),
    so it lives on a separate cadence. Composition root schedules it
    once per ``REFLECTION_CONFIDENCE_INTERVAL``.

    Behavior:

    - If ``confidence < 0.4`` -> increment ``deprecation_streak`` and
      set ``last_confirmed_at = now`` (timestamp refresh, NOT the
      decay anchor).
    - When ``deprecation_streak >= 3`` -> flip ``status='deprecated'``
      so the row stops surfacing in ``reflection_search``.
    - If ``confidence >= 0.4`` -> reset ``deprecation_streak`` to 0.
    """

    low_confidence_threshold: float = 0.4
    deprecation_streak_max: int = 3

    async def run(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """Evaluate one pass. Returns counters: {scanned, deprecated, reset}."""
        stmt = select(ReflectionModel).where(
            ReflectionModel.status == REFLECTION_STATUS_APPROVED,
            ~ReflectionModel.deleted,
        )
        if workspace_id is not None:
            stmt = stmt.where(ReflectionModel.workspace_id == workspace_id)
        rows = list((await db.execute(stmt)).scalars().all())
        now = datetime.now(UTC)
        scanned = 0
        deprecated = 0
        reset = 0
        for row in rows:
            scanned += 1
            if row.confidence < self.low_confidence_threshold:
                row.deprecation_streak += 1
                row.last_confirmed_at = now
                if row.deprecation_streak >= self.deprecation_streak_max:
                    row.status = REFLECTION_STATUS_DEPRECATED
                    deprecated += 1
            else:
                if row.deprecation_streak:
                    row.deprecation_streak = 0
                    reset += 1
        if scanned:
            await db.commit()
        return {"scanned": scanned, "deprecated": deprecated, "reset": reset}


# ──────────────────────────── helpers ────────────────────────────


def _word_count(text: str) -> int:
    """Lenient whitespace-split word count."""
    return len([w for w in text.split() if w.strip()])
