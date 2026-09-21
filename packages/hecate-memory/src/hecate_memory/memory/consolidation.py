"""Sleep-time memory consolidation — engine, trigger bus, and scheduler.

Reviews conversation transcripts during idle periods and consolidates
durable facts into the memory stores (L3 user memory, L4 knowledge memory,
L1 ``learned_context`` block). ADR-024 §3 (KM3 / feature 4.5).

Pipeline per consolidation unit ``(workspace, agent, user_id | None)``:

1. **Extract** — one bounded LLM call pulls candidate durable facts from
   the review window transcript.
2. **Score & dedupe** — code-side: security scan, credential rejection,
   embedding-based (or exact-text fallback) similarity against the unit's
   existing memories and within the batch.
3. **Plan** — a second bounded LLM call turns surviving candidates into
   typed operations (``ADD`` / ``UPDATE`` / ``SUPERSEDE`` / ``NOOP`` /
   ``UPDATE_BLOCK``).
4. **Apply** — deterministic code validates each operation (allowlist,
   scope, revision, budgets) and applies it through the same service-layer
   write path the online memory tools use, inside one transaction per unit.

Supersession semantics: conflicting facts are superseded (lineage pointer +
soft delete), never deleted. The LLM only ever proposes; code disposes.

Delivery model: a single background scheduler (``ConsolidationScheduler``,
started by the composition root when ``CONSOLIDATION_ENABLED``) fires the
trigger bus — a cron schedule (default nightly), an idle-quiet sweep, and
pressure-flag priority (set by the memory pressure alert). Multi-instance
safety via PostgreSQL advisory locks; at-least-once via watermarks: a
unit's review window only advances on a fully successful run, and failed
units are retried on the next trigger.

Run status values (recorded in ``consolidation_runs``):

- ``success`` — every planned operation resolved (applied, or expected
  skips such as revision conflicts); the watermark advances.
- ``partial`` — the mutation budget was exhausted mid-plan; applied
  operations commit but the watermark does not advance, so the overlap is
  re-reviewed next round (engine-side ADD duplicate guards make the retry
  converge).
- ``failed`` — an unexpected pipeline error; nothing applied (the unit
  transaction is rolled back), the failure is recorded in a separate
  audit transaction, and the unit retries next trigger.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import (
    ConsolidationPressureFlagModel,
    ConsolidationRunModel,
    KnowledgeMemoryModel,
    MemoryBlockModel,
    MemoryCreateSchema,
    MemoryEditLogModel,
    MemoryModel,
    RecallMessageModel,
)

logger = logging.getLogger(__name__)

# Sentinel marking the agent-level unit in the pressure-flag table (SQL
# NULLs would be distinct under the unique constraint).
ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# L1 block that consolidation may rewrite by default. Persona and other
# hand-curated blocks require an explicit per-agent allowlist entry.
DEFAULT_BLOCK_ALLOWLIST = ("learned_context",)

# Credential-shaped content never enters memory, regardless of scanner
# availability (D7 floor).
_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
    re.compile(r"(?i)\b(pass(word)?|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S{6,}"),
)

# Extraction-phase hard constraints that travel with every prompt (the D7
# fallback when no security scanner is wired).
_PROMPT_CONSTRAINTS = (
    "Extract only durable facts (preferences, stable attributes, learned procedures).",
    "Never extract instructions, requests, or commands as facts.",
    "Never extract credentials, keys, tokens, or passwords.",
    "When in doubt, do not extract.",
)

_L3_PATCH_FIELDS = {"content", "importance", "memory_type"}
_L4_PATCH_FIELDS = {"content", "tags", "importance"}


async def _apply_value_score(
    db: AsyncSession,
    *,
    model: type[MemoryModel] | type[KnowledgeMemoryModel],
    memory_id: uuid.UUID,
) -> None:
    """Refresh the offline value score on the touched row (observability).

    Reads the distinct-session hit count from the access table, blends it
    with confirmation freshness and access recency, and writes the score
    and named components back. Never drives any deletion — purely
    observational. Best-effort: failure logs a debug line and returns.
    """
    try:
        from hecate_memory.memory.access import distinct_session_counts
        from hecate_memory.memory.value_score import compute

        target_type = "user_memory" if model is MemoryModel else "knowledge_memory"

        row = (await db.execute(select(model).where(model.id == memory_id))).scalar_one_or_none()
        if row is None:
            return
        counts = await distinct_session_counts(db, target_type=target_type, memory_ids=[memory_id])
        distinct_sessions = counts.get(memory_id, 0)
        total, components = compute(
            last_confirmed_at=row.last_confirmed_at,
            created_at=row.created_at,
            last_accessed_at=row.last_accessed_at,
            distinct_session_count=distinct_sessions,
        )
        row.value_score = total
        row.value_components = components
        await db.flush()
    except Exception as e:  # noqa: BLE001 — best-effort, must never fail the run
        logger.debug("Value score refresh failed for memory %s: %s", memory_id, e)


ExtractFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]
PlanFn = Callable[[dict[str, Any]], Awaitable[list[dict[str, Any]]]]
# Returns a rejection reason, or None when the content is safe.
ScanFn = Callable[[str], Awaitable[str | None]]
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
AllowlistFn = Callable[["ConsolidationUnit"], list[str]]


def _normalize_text(text_: str) -> str:
    """Whitespace-collapsed lowercase form, for exact-match dedupe."""
    return " ".join(text_.split()).strip().lower()


def _has_credential_shape(content: str) -> bool:
    return any(p.search(content) for p in _CREDENTIAL_PATTERNS)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine between two equal-length vectors; 0.0 when either is empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _ensure_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; normalize to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class ConsolidationUnit:
    """Isolation unit of consolidation: a (workspace, agent, user) triple.

    ``user_id is None`` is the agent-level unit (facts not tied to one
    user); user-level units keep personalization facts inside their user
    scope.
    """

    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    user_id: uuid.UUID | None = None

    @property
    def key(self) -> str:
        return f"{self.workspace_id}:{self.agent_id}:{self.user_id or 'agent'}"

    @property
    def flag_user_id(self) -> uuid.UUID:
        return self.user_id or ZERO_UUID


@dataclass
class UnitWindow:
    """A unit's pending review window (transcript created_at range)."""

    unit: ConsolidationUnit
    window_start: datetime
    window_end: datetime


@dataclass
class OperationOutcome:
    """One applied/rejected/skipped operation, for the run audit record."""

    op: str
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    content_summary: str | None = None
    outcome: str = "applied"  # applied | rejected | skipped | failed
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "target_type": self.target_type,
            "target_id": str(self.target_id) if self.target_id else None,
            "content_summary": self.content_summary,
            "outcome": self.outcome,
            "detail": self.detail,
        }


@dataclass
class _ExistingMemory:
    """An existing memory row participating in similarity comparison."""

    target_type: str  # user_memory | knowledge_memory
    target_id: uuid.UUID
    content: str
    revision: int
    row: Any = None


@dataclass
class ConsolidationSettings:
    """Engine budgets and thresholds (per unit run)."""

    similarity_threshold: float = 0.85
    max_llm_calls: int = 10
    max_mutations: int = 50
    max_candidates: int = 40
    max_existing: int = 200
    max_transcript_messages: int = 120


class _BudgetExhaustedError(Exception):
    """Raised internally when the mutation budget runs out mid-plan."""


class ConsolidationEngine:
    """Plan-then-apply consolidation engine for one unit at a time.

    LLM access is injected (``extract`` / ``plan`` callables) so the host
    composition root wires its gateway and tests wire stubs; the same goes
    for the optional security ``scanner`` and ``embed`` seams.
    """

    def __init__(
        self,
        extract: ExtractFn,
        plan: PlanFn,
        *,
        scanner: ScanFn | None = None,
        embed: EmbedFn | None = None,
        block_allowlist_for: AllowlistFn | None = None,
        vector_store: Any | None = None,
        settings: ConsolidationSettings | None = None,
    ) -> None:
        self._extract = extract
        self._plan = plan
        self._scanner = scanner
        self._embed = embed
        self._block_allowlist_for = block_allowlist_for
        self._vector_store = vector_store
        self.settings = settings or ConsolidationSettings()

    # -- public entry ------------------------------------------------------------

    async def run_unit(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        window_start: datetime,
        window_end: datetime,
        *,
        trigger: str,
    ) -> ConsolidationRunModel:
        """Consolidate one unit over one review window.

        Flushes but does not commit — the caller owns the transaction so the
        unit runs atomically (promote-before-checkpoint: the run row only
        records ``success`` — and the watermark with it — when everything
        applied). Unexpected exceptions propagate after recording a
        ``failed`` run; the caller rolls back and re-persists that row.
        """
        start = _ensure_utc(window_start)
        end = _ensure_utc(window_end)
        outcomes: list[OperationOutcome] = []
        llm_calls = 0
        degraded = False

        run = ConsolidationRunModel(
            workspace_id=unit.workspace_id,
            agent_id=unit.agent_id,
            user_id=unit.user_id,
            trigger=trigger,
            window_start=start,
            window_end=end,
            status="running",
        )
        db.add(run)
        await db.flush()

        try:
            transcript = await self._load_transcript(db, unit, start, end)

            # -- stage 1: extract candidates ------------------------------------
            candidates = await self._extract(self._extraction_payload(unit, transcript))
            llm_calls += 1
            candidates = candidates[: self.settings.max_candidates]

            existing = await self._load_existing(db, unit)
            rejected = await self._screen_candidates(candidates, outcomes)
            survivors = [c for c in candidates if id(c) not in rejected]

            # -- stage 2: score & dedupe (code-side) ---------------------------
            degraded = not await self._attach_similarity(survivors, existing, outcomes)
            survivors = [c for c in survivors if id(c) not in rejected and not c.get("_rejected")]

            if not survivors:
                return self._finish(run, outcomes, llm_calls, degraded, "success")

            # -- stage 3: plan ---------------------------------------------------
            plan_ops = await self._plan(self._planning_payload(unit, survivors, existing, degraded))
            llm_calls += 1

            # -- stage 4: apply ---------------------------------------------------
            status = await self._apply(db, unit, plan_ops, existing, outcomes)

            return self._finish(run, outcomes, llm_calls, degraded, status)
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)[:2000]
            self._tally(run, outcomes)
            await db.flush()
            raise

    # -- stages --------------------------------------------------------------------

    async def _load_transcript(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Review-window transcript rows (user/assistant) for the unit."""
        stmt = (
            select(RecallMessageModel.role, RecallMessageModel.content)
            .where(
                ~RecallMessageModel.deleted,
                RecallMessageModel.workspace_id == unit.workspace_id,
                RecallMessageModel.agent_id == unit.agent_id,
                RecallMessageModel.created_at > start,
                RecallMessageModel.created_at <= end,
            )
            .order_by(RecallMessageModel.created_at.asc(), RecallMessageModel.seq.asc())
            .limit(self.settings.max_transcript_messages)
        )
        if unit.user_id is not None:
            stmt = stmt.where(RecallMessageModel.user_id == unit.user_id)
        rows = (await db.execute(stmt)).all()
        return [{"role": role, "content": content} for role, content in rows]

    def _extraction_payload(self, unit: ConsolidationUnit, transcript: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "task": "extract_durable_facts",
            "unit": {
                "workspace_id": str(unit.workspace_id),
                "agent_id": str(unit.agent_id),
                "user_id": str(unit.user_id) if unit.user_id else None,
            },
            "constraints": list(_PROMPT_CONSTRAINTS),
            "max_candidates": self.settings.max_candidates,
            "transcript": transcript,
        }

    async def _screen_candidates(
        self,
        candidates: list[dict[str, Any]],
        outcomes: list[OperationOutcome],
    ) -> set[int]:
        """Security-screen candidates; returns ids of rejected entries.

        Rejections are expected outcomes (recorded, never fatal). The scan
        seam is optional — credential-shape rejection always applies.
        """
        rejected: set[int] = set()
        for candidate in candidates:
            content = str(candidate.get("content", "")).strip()
            if not content:
                rejected.add(id(candidate))
                outcomes.append(OperationOutcome("NOOP", outcome="rejected", detail="empty_content"))
                continue
            if _has_credential_shape(content):
                rejected.add(id(candidate))
                outcomes.append(OperationOutcome("NOOP", outcome="rejected", detail="credential_content"))
                continue
            if self._scanner is not None:
                reason = await self._scanner(content)
                if reason:
                    rejected.add(id(candidate))
                    outcomes.append(OperationOutcome("NOOP", outcome="rejected", detail=f"security:{reason}"))
        return rejected

    async def _attach_similarity(
        self,
        candidates: list[dict[str, Any]],
        existing: list[_ExistingMemory],
        outcomes: list[OperationOutcome],
    ) -> bool:
        """Compute candidate↔existing and intra-batch similarity.

        Returns False (and flags the run degraded) when embeddings were
        unavailable and exact-text matching was used instead. Candidates
        that duplicate an earlier candidate or an existing memory are added
        to ``outcomes`` as rejected and marked ``_rejected``.
        """
        texts = [str(c.get("content", "")) for c in candidates]
        vectors: list[list[float]] | None = None
        if self._embed is not None and texts:
            try:
                vectors = await self._embed([*texts, *(m.content for m in existing)])
            except Exception as e:
                logger.warning("Consolidation embedding unavailable, degrading to exact match: %s", e)
                vectors = None
        if vectors is not None and len(vectors) == len(texts) + len(existing):
            cand_vecs = vectors[: len(texts)]
            existing_vecs = vectors[len(texts) :]
        else:
            cand_vecs = None
            existing_vecs = None

        seen_normalized: set[str] = set()
        for idx, candidate in enumerate(candidates):
            content = str(candidate.get("content", ""))
            normalized = _normalize_text(content)

            # Intra-batch duplicate.
            if normalized in seen_normalized:
                outcomes.append(OperationOutcome("NOOP", outcome="rejected", detail="duplicate_candidate"))
                candidate["_rejected"] = True
                continue
            seen_normalized.add(normalized)

            similar: list[dict[str, Any]] = []
            for j, memory in enumerate(existing):
                if cand_vecs is not None and existing_vecs is not None:
                    score = cosine_similarity(cand_vecs[idx], existing_vecs[j])
                elif normalized == _normalize_text(memory.content):
                    score = 1.0
                else:
                    score = 0.0
                if score >= self.settings.similarity_threshold:
                    similar.append(
                        {
                            "target_type": memory.target_type,
                            "target_id": str(memory.target_id),
                            "content": memory.content,
                            "revision": memory.revision,
                            "similarity": round(score, 4),
                        }
                    )
            candidate["_similar"] = similar
        return cand_vecs is not None

    def _planning_payload(
        self,
        unit: ConsolidationUnit,
        candidates: list[dict[str, Any]],
        existing: list[_ExistingMemory],
        degraded: bool,
    ) -> dict[str, Any]:
        clean = [
            {k: v for k, v in c.items() if not k.startswith("_") and k != "_rejected"}
            | {"_similar": c.get("_similar", [])}
            for c in candidates
            if not c.get("_rejected")
        ]
        return {
            "task": "plan_memory_operations",
            "unit": {
                "workspace_id": str(unit.workspace_id),
                "agent_id": str(unit.agent_id),
                "user_id": str(unit.user_id) if unit.user_id else None,
            },
            "similarity_mode": "exact_text" if degraded else "embedding",
            "operations_vocabulary": ["ADD", "UPDATE", "SUPERSEDE", "NOOP", "UPDATE_BLOCK"],
            "existing_memories": [
                {
                    "target_type": m.target_type,
                    "target_id": str(m.target_id),
                    "content": m.content,
                    "revision": m.revision,
                }
                for m in existing
            ],
            "candidates": clean,
        }

    async def _apply(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        plan_ops: list[dict[str, Any]],
        existing: list[_ExistingMemory],
        outcomes: list[OperationOutcome],
    ) -> str:
        """Validate and apply planned operations. Returns the run status."""
        applied_contents: set[str] = {_normalize_text(m.content) for m in existing}
        mutations = 0
        exhausted = False

        for raw in plan_ops:
            op = str(raw.get("op", "")).upper()
            target_type = raw.get("target_type")
            target_id = raw.get("target_id")
            content = str(raw.get("content", "")).strip() or None
            summary = (content or f"{op} {target_type or ''}")[:120]

            if op in ("ADD", "UPDATE", "SUPERSEDE", "UPDATE_BLOCK"):
                if mutations >= self.settings.max_mutations:
                    exhausted = True
                    outcomes.append(
                        OperationOutcome(
                            op, str(target_type), _as_uuid(target_id), summary, "rejected", "mutation_budget_exhausted"
                        )
                    )
                    continue
                mutations += 1

            try:
                if op == "NOOP":
                    outcomes.append(
                        OperationOutcome(
                            "NOOP", outcome="skipped", detail=str(raw.get("detail") or "model_decided_noop")
                        )
                    )
                elif op == "ADD":
                    new_id = await self._apply_add(db, unit, raw, content, applied_contents, outcomes)
                    if new_id is not None:
                        applied_contents.add(_normalize_text(content or ""))
                elif op == "UPDATE":
                    await self._apply_update(db, unit, raw, content, outcomes)
                elif op == "SUPERSEDE":
                    new_id = await self._apply_supersede(db, unit, raw, content, applied_contents, outcomes)
                    if new_id is not None:
                        applied_contents.add(_normalize_text(content or ""))
                elif op == "UPDATE_BLOCK":
                    await self._apply_update_block(db, unit, raw, content, outcomes)
                else:
                    outcomes.append(
                        OperationOutcome(
                            op or "UNKNOWN", str(target_type), _as_uuid(target_id), summary, "rejected", "unknown_op"
                        )
                    )
            except Exception as e:
                # Unexpected per-op error: record and re-raise so the unit
                # transaction rolls back and the run retries next trigger.
                outcomes.append(
                    OperationOutcome(
                        op or "UNKNOWN", str(target_type), _as_uuid(target_id), summary, "failed", str(e)[:300]
                    )
                )
                raise

        return "partial" if exhausted else "success"

    async def _apply_add(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        raw: dict[str, Any],
        content: str | None,
        applied_contents: set[str],
        outcomes: list[OperationOutcome],
    ) -> uuid.UUID | None:
        if not content:
            outcomes.append(OperationOutcome("ADD", outcome="rejected", detail="empty_content"))
            return None
        if _normalize_text(content) in applied_contents:
            outcomes.append(OperationOutcome("ADD", outcome="rejected", detail="duplicate_content"))
            return None

        target_type = raw.get("target_type", "user_memory")
        importance = float(raw.get("importance", 0.5) or 0.5)
        embedding = await self._embed_one(content)

        if target_type == "knowledge_memory":
            from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

            schema = await KnowledgeMemoryService(db, self._vector_store).insert_knowledge(
                agent_id=unit.agent_id,
                workspace_id=unit.workspace_id,
                content=content,
                importance=max(0.0, min(1.0, importance)),
                user_id=unit.user_id,
                source="consolidation",
            )
            new_id = schema.id
        else:
            from hecate_memory.memory.user_memory import UserMemoryService

            scope = {"user_id": str(unit.user_id)} if unit.user_id else {}
            schema = await UserMemoryService(db).store_memory(
                unit.workspace_id,
                MemoryCreateSchema(
                    content=content,
                    scope=scope,
                    memory_type=str(raw.get("memory_type", "semantic") or "semantic"),
                    importance=max(0.0, min(1.0, importance)),
                ),
                embedding=embedding,
            )
            new_id = schema.id

        await self._audit(db, unit, "consolidation_add", str(target_type), new_id, None, None, content)
        await _apply_value_score(
            db,
            model=KnowledgeMemoryModel if target_type == "knowledge_memory" else MemoryModel,
            memory_id=new_id,
        )
        outcomes.append(OperationOutcome("ADD", str(target_type), new_id, content[:120], "applied"))
        return new_id

    async def _apply_update(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        raw: dict[str, Any],
        content: str | None,
        outcomes: list[OperationOutcome],
    ) -> None:
        row, layer = await self._load_target(db, unit, raw, outcomes, "UPDATE")
        if row is None:
            return
        expected = raw.get("expected_revision")
        if expected is not None and int(expected) != row.revision:
            outcomes.append(
                OperationOutcome("UPDATE", layer, row.id, (content or "")[:120], "skipped", "revision_conflict")
            )
            return

        allowed = _L4_PATCH_FIELDS if layer == "knowledge_memory" else _L3_PATCH_FIELDS
        patch = {k: v for k, v in raw.items() if k in allowed and v is not None}
        if content is not None:
            patch["content"] = content
        if not patch:
            outcomes.append(OperationOutcome("UPDATE", layer, row.id, None, "rejected", "empty_patch"))
            return

        before = row.content
        for key, value in patch.items():
            setattr(row, key, value)
        row.revision += 1
        # Decay anchor: refreshed by consolidation writes only, never by
        # retrieval. Set just before flush so the UPDATE lands in the same
        # transaction as the patch.
        row.last_confirmed_at = datetime.now(UTC)
        if layer == "knowledge_memory" and "content" in patch:
            from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

            await KnowledgeMemoryService(db, self._vector_store).reindex(row)
        await db.flush()
        await self._audit(db, unit, "consolidation_update", layer, row.id, row.revision, before, patch.get("content"))
        await _apply_value_score(
            db,
            model=KnowledgeMemoryModel if layer == "knowledge_memory" else MemoryModel,
            memory_id=row.id,
        )
        outcomes.append(OperationOutcome("UPDATE", layer, row.id, (patch.get("content") or before)[:120], "applied"))

    async def _apply_supersede(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        raw: dict[str, Any],
        content: str | None,
        applied_contents: set[str],
        outcomes: list[OperationOutcome],
    ) -> uuid.UUID | None:
        if not content:
            outcomes.append(OperationOutcome("SUPERSEDE", outcome="rejected", detail="empty_content"))
            return None
        row, layer = await self._load_target(db, unit, raw, outcomes, "SUPERSEDE")
        if row is None:
            return None
        expected = raw.get("expected_revision")
        if expected is not None and int(expected) != row.revision:
            outcomes.append(OperationOutcome("SUPERSEDE", layer, row.id, content[:120], "skipped", "revision_conflict"))
            return None
        if _normalize_text(content) in applied_contents:
            outcomes.append(
                OperationOutcome("SUPERSEDE", layer, row.id, content[:120], "rejected", "duplicate_content")
            )
            return None

        # Revision guard passed against the pre-write snapshot — create the
        # successor first so the lineage pointer always lands on a real row,
        # then supersede the old row (its service function writes the audit).
        if layer == "knowledge_memory":
            new_id = await self._apply_add(
                db, unit, {**raw, "target_type": "knowledge_memory"}, content, applied_contents, outcomes
            )
            if new_id is None:
                return None
            from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

            superseded = await KnowledgeMemoryService(db, self._vector_store).supersede_knowledge(
                agent_id=unit.agent_id,
                workspace_id=unit.workspace_id,
                memory_id=row.id,
                superseded_by=new_id,
                expected_revision=int(expected) if expected is not None else None,
            )
        else:
            new_id = await self._apply_add(
                db, unit, {**raw, "target_type": "user_memory"}, content, applied_contents, outcomes
            )
            if new_id is None:
                return None
            from hecate_memory.memory.user_memory import UserMemoryService

            superseded = await UserMemoryService(db).supersede_memory(
                workspace_id=unit.workspace_id,
                memory_id=row.id,
                superseded_by=new_id,
                expected_revision=int(expected) if expected is not None else None,
                agent_id=unit.agent_id,
            )
        if superseded is None:
            outcomes.append(OperationOutcome("SUPERSEDE", layer, row.id, content[:120], "skipped", "target_vanished"))
            return None
        outcomes.append(OperationOutcome("SUPERSEDE", layer, row.id, f"{row.id} -> {new_id}", "applied", content[:120]))
        return new_id

    async def _apply_update_block(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        raw: dict[str, Any],
        content: str | None,
        outcomes: list[OperationOutcome],
    ) -> None:
        label = str(raw.get("label", "")).strip()
        allowlist = self._block_allowlist_for(unit) if self._block_allowlist_for else list(DEFAULT_BLOCK_ALLOWLIST)
        if label not in allowlist:
            outcomes.append(
                OperationOutcome("UPDATE_BLOCK", "memory_block", None, label, "rejected", "label_not_allowed")
            )
            return
        if not content:
            outcomes.append(OperationOutcome("UPDATE_BLOCK", "memory_block", None, label, "rejected", "empty_content"))
            return

        from hecate_memory.memory.working_memory import WorkingMemoryService

        service = WorkingMemoryService(db)
        block = await service.get_block_by_label(unit.agent_id, unit.workspace_id, label)
        if block is None:
            block = MemoryBlockModel(
                workspace_id=unit.workspace_id,
                agent_id=unit.agent_id,
                label=label,
                content="",
                position=999,
                limit=2000,
            )
            db.add(block)
            await db.flush()

        expected = raw.get("expected_revision")
        if expected is not None and int(expected) != block.revision:
            outcomes.append(
                OperationOutcome("UPDATE_BLOCK", "memory_block", block.id, label, "skipped", "revision_conflict")
            )
            return
        # Same limit contract as the memory_replace tool: overflow is an
        # error, not a silent truncation.
        if len(content) // 4 > block.limit:
            outcomes.append(
                OperationOutcome("UPDATE_BLOCK", "memory_block", block.id, label, "rejected", "block_limit_overflow")
            )
            return

        before = block.content
        block.content = content
        block.revision += 1
        await db.flush()
        await self._audit(
            db, unit, "consolidation_update_block", "memory_block", block.id, block.revision, before, content
        )
        outcomes.append(OperationOutcome("UPDATE_BLOCK", "memory_block", block.id, label, "applied"))

    # -- helpers --------------------------------------------------------------------

    async def _load_target(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        raw: dict[str, Any],
        outcomes: list[OperationOutcome],
        op: str,
    ) -> tuple[Any, str] | tuple[None, None]:
        """Load the operation's target row inside the unit scope."""
        target_id = _as_uuid(raw.get("target_id"))
        target_type = str(raw.get("target_type", ""))
        if target_id is None:
            outcomes.append(OperationOutcome(op, target_type or None, None, None, "rejected", "missing_target_id"))
            return None, None
        row = (
            await self._load_l4(db, unit, target_id)
            if target_type == "knowledge_memory"
            else await self._load_l3(db, unit, target_id)
        )
        if row is None:
            outcomes.append(OperationOutcome(op, target_type or None, target_id, None, "skipped", "target_not_found"))
            return None, None
        return row, target_type

    async def _load_l3(self, db: AsyncSession, unit: ConsolidationUnit, memory_id: uuid.UUID) -> MemoryModel | None:
        stmt = select(MemoryModel).where(
            MemoryModel.id == memory_id,
            MemoryModel.workspace_id == unit.workspace_id,
            ~MemoryModel.deleted,
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        scope_user = (row.scope or {}).get("user_id")
        if unit.user_id is not None:
            if scope_user != str(unit.user_id):
                return None
        elif scope_user is not None:
            return None
        return row

    async def _load_l4(
        self, db: AsyncSession, unit: ConsolidationUnit, memory_id: uuid.UUID
    ) -> KnowledgeMemoryModel | None:
        stmt = select(KnowledgeMemoryModel).where(
            KnowledgeMemoryModel.id == memory_id,
            KnowledgeMemoryModel.workspace_id == unit.workspace_id,
            KnowledgeMemoryModel.agent_id == unit.agent_id,
            ~KnowledgeMemoryModel.deleted,
        )
        if unit.user_id is not None:
            stmt = stmt.where(KnowledgeMemoryModel.user_id == unit.user_id)
        else:
            stmt = stmt.where(KnowledgeMemoryModel.user_id.is_(None))
        return (await db.execute(stmt)).scalar_one_or_none()

    async def _load_existing(self, db: AsyncSession, unit: ConsolidationUnit) -> list[_ExistingMemory]:
        """The unit's existing memories, as similarity-comparison inputs."""
        existing: list[_ExistingMemory] = []

        l3_filter = (
            ~MemoryModel.deleted,
            MemoryModel.workspace_id == unit.workspace_id,
        )
        if unit.user_id is not None:
            l3_filter = (*l3_filter, MemoryModel.scope["user_id"].as_string() == str(unit.user_id))
        else:
            l3_filter = (*l3_filter, MemoryModel.scope["user_id"].as_string().is_(None))
        rows = (
            (await db.execute(select(MemoryModel).where(*l3_filter).limit(self.settings.max_existing))).scalars().all()
        )
        existing.extend(_ExistingMemory("user_memory", row.id, row.content, row.revision, row) for row in rows)

        l4_filter = [
            ~KnowledgeMemoryModel.deleted,
            KnowledgeMemoryModel.workspace_id == unit.workspace_id,
            KnowledgeMemoryModel.agent_id == unit.agent_id,
        ]
        if unit.user_id is not None:
            l4_filter.append(KnowledgeMemoryModel.user_id == unit.user_id)
        else:
            l4_filter.append(KnowledgeMemoryModel.user_id.is_(None))
        rows = (
            (
                await db.execute(
                    select(KnowledgeMemoryModel).where(*l4_filter).limit(self.settings.max_existing - len(existing))
                )
            )
            .scalars()
            .all()
        )
        existing.extend(_ExistingMemory("knowledge_memory", row.id, row.content, row.revision, row) for row in rows)
        return existing

    async def _embed_one(self, content: str) -> list[float] | None:
        """Real embedding for one new row; None (mock fallback) if unavailable."""
        if self._embed is None:
            return None
        try:
            vectors = await self._embed([content])
            return vectors[0] if vectors else None
        except Exception as e:
            logger.debug("Consolidation embed failed: %s", e)
            return None

    async def _audit(
        self,
        db: AsyncSession,
        unit: ConsolidationUnit,
        tool_name: str,
        target_type: str,
        target_id: uuid.UUID,
        revision_after: int | None,
        before: str | None,
        after: str | None,
    ) -> None:
        """Append one edit-log row for an applied consolidation mutation."""
        db.add(
            MemoryEditLogModel(
                workspace_id=unit.workspace_id,
                agent_id=unit.agent_id,
                session_id=None,
                trace_id=None,
                tool_name=tool_name,
                target_type=target_type,
                target_id=target_id,
                revision_before=(revision_after - 1) if revision_after else None,
                revision_after=revision_after,
                before_summary=before[:200] if before else None,
                after_summary=after[:200] if after else None,
            )
        )
        await db.flush()

    def _finish(
        self,
        run: ConsolidationRunModel,
        outcomes: list[OperationOutcome],
        llm_calls: int,
        degraded: bool,
        status: str,
    ) -> ConsolidationRunModel:
        run.llm_calls = llm_calls
        run.degraded = degraded
        run.status = status
        run.operations = [o.as_dict() for o in outcomes]
        self._tally(run, outcomes)
        return run

    @staticmethod
    def _tally(run: ConsolidationRunModel, outcomes: list[OperationOutcome]) -> None:
        run.candidate_count = sum(1 for o in outcomes if o.op == "NOOP" and o.outcome == "rejected") + sum(
            1 for o in outcomes if o.op != "NOOP"
        )
        run.adopted_count = sum(1 for o in outcomes if o.outcome == "applied")
        run.rejected_count = sum(1 for o in outcomes if o.outcome in ("rejected", "skipped"))
        run.failed_count = sum(1 for o in outcomes if o.outcome == "failed")


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# -- trigger bus ---------------------------------------------------------------------


async def pending_units(db: AsyncSession, *, now: datetime | None = None) -> list[UnitWindow]:
    """Units with transcript newer than their consolidation watermark.

    One query per side: per-unit watermarks come from the latest SUCCESS
    run, unit activity from ``recall_messages``. A unit is pending when its
    latest transcript row is newer than its watermark (or it has never been
    consolidated).
    """
    _ = now or datetime.now(UTC)

    watermarks: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None], datetime] = {}
    rows = (
        await db.execute(
            select(
                ConsolidationRunModel.workspace_id,
                ConsolidationRunModel.agent_id,
                ConsolidationRunModel.user_id,
                func.max(ConsolidationRunModel.window_end),
            )
            .where(
                ~ConsolidationRunModel.deleted,
                ConsolidationRunModel.status == "success",
            )
            .group_by(
                ConsolidationRunModel.workspace_id,
                ConsolidationRunModel.agent_id,
                ConsolidationRunModel.user_id,
            )
        )
    ).all()
    for ws, agent, user, watermark in rows:
        watermarks[(ws, agent, user)] = _ensure_utc(watermark)

    activity = (
        await db.execute(
            select(
                RecallMessageModel.workspace_id,
                RecallMessageModel.agent_id,
                RecallMessageModel.user_id,
                func.max(RecallMessageModel.created_at),
            )
            .where(~RecallMessageModel.deleted)
            .group_by(
                RecallMessageModel.workspace_id,
                RecallMessageModel.agent_id,
                RecallMessageModel.user_id,
            )
        )
    ).all()

    pending: list[UnitWindow] = []
    for ws, agent, user, last_at in activity:
        if last_at is None:
            continue
        last = _ensure_utc(last_at)
        unit = ConsolidationUnit(ws, agent, user)
        window_start = watermarks.get((ws, agent, user), _EPOCH)
        if last > window_start:
            pending.append(UnitWindow(unit, window_start, last))
    pending.sort(key=lambda w: w.window_end, reverse=True)
    return pending


async def pressure_flagged_units(db: AsyncSession) -> set[str]:
    """Unit keys that carry an unconsumed pressure marker."""
    rows = (
        await db.execute(
            select(
                ConsolidationPressureFlagModel.workspace_id,
                ConsolidationPressureFlagModel.agent_id,
                ConsolidationPressureFlagModel.user_id,
            ).where(~ConsolidationPressureFlagModel.deleted)
        )
    ).all()
    return {ConsolidationUnit(ws, agent, None if user == ZERO_UUID else user).key for ws, agent, user in rows}


async def mark_unit_pressure(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    session_factory: Any | None = None,
) -> bool:
    """Best-effort pressure mark for a unit (idempotent). False on failure.

    Called by the memory pressure alert on threshold crossing; failures are
    non-fatal by contract (the alert must never fail the turn).
    """
    try:
        from hecate.core.database import async_session_factory as default_factory

        factory = session_factory or default_factory
        async with factory() as db:
            db.add(
                ConsolidationPressureFlagModel(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    user_id=user_id or ZERO_UUID,
                )
            )
            await db.commit()
        return True
    except Exception as e:
        logger.warning("Failed to mark consolidation pressure for %s/%s: %s", agent_id, user_id, e)
        return False


class ConsolidationScheduler:
    """Trigger bus + runner: cron, idle sweep, pressure priority.

    Owns the unit-processing transaction: commit on success/partial,
    rollback + separate failed-run audit row on unexpected errors, and
    pressure-flag consumption after each flagged unit's attempt.
    """

    def __init__(
        self,
        engine: ConsolidationEngine,
        *,
        schedule: str = "0 2 * * *",
        idle_check_interval: int = 300,
        idle_quiet_seconds: int = 1800,
        session_factory: Any | None = None,
    ) -> None:
        self.engine = engine
        self.schedule = schedule
        self.idle_check_interval = max(int(idle_check_interval), 0)
        self.idle_quiet_seconds = int(idle_quiet_seconds)
        self._session_factory = session_factory
        self._task: asyncio.Task[None] | None = None

    def _factory(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory
        from hecate.core.database import async_session_factory

        return async_session_factory

    # -- unit processing ---------------------------------------------------------

    async def process_unit(
        self,
        db: AsyncSession,
        window: UnitWindow,
        *,
        trigger: str,
    ) -> bool:
        """Run one unit under the advisory lock; returns True when executed.

        On SQLite (tests) the lock is skipped — single instance by
        construction there.
        """
        lock_id, locked = await self._acquire_lock(db, window.unit)
        if not locked:
            logger.debug("Unit %s locked elsewhere, skipping", window.unit.key)
            return False
        try:
            run = await self.engine.run_unit(
                db,
                window.unit,
                window.window_start,
                window.window_end,
                trigger=trigger,
            )
            await self._consume_flag(db, window.unit)
            await db.commit()
            logger.info(
                "Consolidation %s for %s: %s adopted / %s rejected / %s skipped",
                run.status,
                window.unit.key,
                run.adopted_count,
                run.rejected_count,
                run.failed_count,
            )
            # Memory-importance-fusion: refresh any pinned prefetch for the
            # unit on the next call, so the next turn sees the just-applied
            # memory updates without the prior pinned snapshot going stale.
            try:
                from hecate.runtime.context_processors import invalidate_prefetch_cache_for_unit

                invalidate_prefetch_cache_for_unit(window.unit.key)
            except Exception as inv_err:  # noqa: BLE001 — never fails the run
                logger.debug("Prefetch cache invalidation failed: %s", inv_err)
            return True
        except Exception as exc:
            await db.rollback()
            await self._record_failed_run(db, window, trigger, exc)
            await self._consume_flag(db, window.unit)
            await db.commit()
            return True
        finally:
            await self._release_lock(db, lock_id)

    async def run_due_units(
        self,
        trigger: str,
        *,
        quiet_seconds: int | None = None,
        now: datetime | None = None,
    ) -> int:
        """Process all pending units for one trigger; returns units run.

        Pressure-flagged units sort first regardless of trigger.
        """
        now = _ensure_utc(now or datetime.now(UTC))
        factory = self._factory()
        async with factory() as db:
            pending = await pending_units(db, now=now)
            if quiet_seconds is not None:
                cutoff = now - timedelta(seconds=quiet_seconds)
                pending = [w for w in pending if w.window_end <= cutoff]

            flagged = await pressure_flagged_units(db)
            pending.sort(key=lambda w: w.unit.key not in flagged)

            processed = 0
            for window in pending:
                if await self.process_unit(db, window, trigger=trigger):
                    processed += 1
            return processed

    async def _record_failed_run(
        self,
        db: AsyncSession,
        window: UnitWindow,
        trigger: str,
        exc: Exception,
    ) -> None:
        """Persist a failed-run audit row after the unit rollback."""
        db.add(
            ConsolidationRunModel(
                workspace_id=window.unit.workspace_id,
                agent_id=window.unit.agent_id,
                user_id=window.unit.user_id,
                trigger=trigger,
                window_start=window.window_start,
                window_end=window.window_end,
                status="failed",
                error=str(exc)[:2000],
            )
        )
        await db.flush()

    async def _consume_flag(self, db: AsyncSession, unit: ConsolidationUnit) -> None:
        """Delete the unit's pressure marker after its run attempt."""
        await db.execute(
            delete(ConsolidationPressureFlagModel).where(
                ConsolidationPressureFlagModel.workspace_id == unit.workspace_id,
                ConsolidationPressureFlagModel.agent_id == unit.agent_id,
                ConsolidationPressureFlagModel.user_id == unit.flag_user_id,
            )
        )

    async def _acquire_lock(self, db: AsyncSession, unit: ConsolidationUnit) -> tuple[int | None, bool]:
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return None, True
        lock_id = int(hashlib.sha256(f"consolidation:{unit.key}".encode()).hexdigest()[:15], 16)
        result = await db.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
        return lock_id, bool(result.scalar_one())

    async def _release_lock(self, db: AsyncSession, lock_id: int | None) -> None:
        if lock_id is None:
            return
        with contextlib.suppress(Exception):
            await db.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})

    # -- lifecycle ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Trigger loop: cron fire + idle sweep each tick.

        ``CONSOLIDATION_IDLE_CHECK_INTERVAL_SECONDS=0`` disables the idle
        sweep (cron still fires).
        """
        while True:
            sleep_for = max(self.idle_check_interval, 30) if self.idle_check_interval else 30
            try:
                now = datetime.now(UTC)
                await self._cron_if_due(now)
                if self.idle_check_interval:
                    await self.run_due_units(
                        "idle",
                        quiet_seconds=self.idle_quiet_seconds,
                        now=now,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Consolidation trigger tick failed: %s", e)
            await asyncio.sleep(sleep_for)

    async def _cron_if_due(self, now: datetime) -> None:
        """Fire the cron trigger when the schedule boundary has passed.

        Boundaries come from croniter; if the last boundary is within one
        tick behind ``now``, the trigger is due. Missed boundaries while the
        process was down do not queue (catch_up is out of scope).
        """
        try:
            import croniter

            boundary = now - timedelta(seconds=max(self.idle_check_interval, 30) or 30)
            cron = croniter.croniter(self.schedule, boundary)
            next_fire = cron.get_next(datetime)
            if next_fire <= now:
                await self.run_due_units("cron", now=now)
        except ImportError:
            logger.debug("croniter unavailable — cron trigger disabled, idle sweep only")
        except ValueError as e:
            logger.warning("Invalid CONSOLIDATION_SCHEDULE %r: %s", self.schedule, e)

    def start(self) -> None:
        """Start the background loop (no-op when already running)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self.run_forever())
        logger.info("Consolidation scheduler started (schedule=%s)", self.schedule)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Consolidation scheduler stopped")


_scheduler: ConsolidationScheduler | None = None


def start_consolidation_scheduler(
    engine: ConsolidationEngine,
    **kwargs: Any,
) -> ConsolidationScheduler | None:
    """Start the module-level scheduler singleton (no-op when disabled)."""
    global _scheduler  # noqa: PLW0603
    if _scheduler is not None:
        return _scheduler
    _scheduler = ConsolidationScheduler(engine, **kwargs)
    _scheduler.start()
    return _scheduler


async def stop_consolidation_scheduler() -> None:
    global _scheduler  # noqa: PLW0603
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
