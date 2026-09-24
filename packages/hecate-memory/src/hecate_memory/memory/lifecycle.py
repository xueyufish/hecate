"""Memory lifecycle sweeper — TTL expiry, capacity eviction, promotion.

Implements the ``memory-lifecycle`` capability on top of the L3/L4 fact
stores. A background pass (own cron schedule, default off via
``MEMORY_LIFECYCLE_ENABLED``) runs three bounded stages:

1. **TTL expiry** — rows whose ``last_confirmed_at`` anchor is older than
   the layer's effective TTL are archived (soft delete). L4 defaults to
   never expiring. TTL bounds *survival*; the retrieval-time decay
   (``ranking``) bounds *ordering* — they are independent knobs.
2. **Capacity eviction** — when a scope exceeds its capacity cap, the
   lowest-value rows are archived until the scope fits, bounded by a
   per-sweep budget. Recently confirmed/accessed rows are protected.
   The eviction score reuses the offline value score (confirmation
   freshness × access heat, lazily computed for rows that never had one)
   — the same signal family the fusion ranking consumes, so "why was
   this evicted" is explainable with existing components.
3. **Promotion gate** — actor-scoped L3 rows meeting all of (score
   threshold, access-count floor, minimum age) are copied to the
   workspace-shared namespace through the cross-thread write path. Off
   unless a policy enables it; the resolved sharing ceiling constrains
   the target width.

Every archive/restore/promotion writes a ``memory_edit_log`` row with
the lifecycle tool source and a machine-readable ``reason``. Nothing is
ever physically deleted — archived rows keep their lineage and can be
restored.

Multi-instance safety mirrors the consolidation trigger bus: the sweep
takes the same per-unit advisory locks (identical hash), so a lifecycle
pass and a consolidation run on the same unit serialize. Policy values
(TTLs, caps, budgets, thresholds) come from the resolved memory-policy
chain; the workspace-level resolution drives the L3 pass and the per
``(workspace, agent)`` resolution drives the L4 pass.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import (
    KnowledgeMemoryModel,
    MemoryEditLogModel,
    MemoryModel,
)

logger = logging.getLogger(__name__)

ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

TOOL_NAME = "lifecycle"
REASON_TTL = "ttl_expired"
REASON_EVICTED = "capacity_evicted"
REASON_RESTORED = "restored"
REASON_PROMOTED = "promoted"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _unit_lock_id(key: str) -> int:
    """Same hash as the consolidation trigger bus — locks mutually exclude."""
    return int(hashlib.sha256(f"consolidation:{key}".encode()).hexdigest()[:15], 16)


async def _acquire_unit_lock(db: AsyncSession, key: str) -> tuple[int | None, bool]:
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return None, True
    lock_id = _unit_lock_id(key)
    result = await db.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
    return lock_id, bool(result.scalar_one())


async def _release_unit_lock(db: AsyncSession, lock_id: int | None) -> None:
    if lock_id is None:
        return
    with contextlib.suppress(Exception):
        await db.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})


async def _value_scores(
    db: AsyncSession,
    *,
    rows: list[Any],
    target_type: str,
) -> dict[uuid.UUID, float]:
    """Eviction score per row: stored value_score, computed when absent."""
    from hecate_memory.memory.access import distinct_session_counts
    from hecate_memory.memory.value_score import compute

    missing = [r for r in rows if r.value_score is None]
    counts: dict[uuid.UUID, int] = {}
    if missing:
        counts = await distinct_session_counts(db, target_type=target_type, memory_ids=[r.id for r in missing])
    scores: dict[uuid.UUID, float] = {}
    for r in rows:
        if r.value_score is not None:
            scores[r.id] = float(r.value_score)
            continue
        total, _components = compute(
            last_confirmed_at=r.last_confirmed_at,
            created_at=r.created_at,
            last_accessed_at=r.last_accessed_at,
            distinct_session_count=counts.get(r.id, 0),
        )
        scores[r.id] = total
    return scores


async def _archive_row(
    db: AsyncSession,
    *,
    row: Any,
    target_type: str,
    agent_id: uuid.UUID,
    reason: str,
) -> None:
    """Archive one row (soft) and append the lifecycle audit entry."""
    row.archived_at = datetime.now(UTC)
    db.add(
        MemoryEditLogModel(
            workspace_id=row.workspace_id,
            agent_id=agent_id,
            tool_name=TOOL_NAME,
            target_type=target_type,
            target_id=row.id,
            before_summary=(row.content or "")[:200],
            reason=reason,
        )
    )


async def _ttl_expired_ids(
    db: AsyncSession,
    *,
    model: type,
    workspace_id: uuid.UUID,
    agent_column: Any | None,
    agent_id: uuid.UUID | None,
    type_column: Any | None,
    memory_type: str | None,
    ttl_days: int,
    now: datetime,
) -> list[Any]:
    """Rows of one layer/type group past the effective TTL (anchors only)."""
    conditions = [
        ~model.deleted,
        model.archived_at.is_(None),
        model.workspace_id == workspace_id,
        model.last_confirmed_at.is_not(None),
        model.last_confirmed_at < now - timedelta(days=ttl_days),
    ]
    if agent_column is not None and agent_id is not None:
        conditions.append(agent_column == agent_id)
    if type_column is not None and memory_type is not None:
        conditions.append(type_column == memory_type)
    rows = (await db.execute(select(model).where(*conditions))).scalars().all()
    return rows


async def run_lifecycle_sweep(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One lifecycle pass across all workspaces. Returns stage counters.

    Caller gates on ``MEMORY_LIFECYCLE_ENABLED``; this function assumes
    enabled. All stages share the per-sweep eviction budget.
    """
    from hecate.core.composition.memory_policy import resolve_policy

    now = _ensure_utc(now or datetime.now(UTC))
    stats = {"ttl_archived": 0, "evicted": 0, "promoted": 0, "workspaces": 0}

    workspace_rows = (await db.execute(select(MemoryModel.workspace_id).where(~MemoryModel.deleted).distinct())).all()
    knowledge_workspaces = (
        await db.execute(select(KnowledgeMemoryModel.workspace_id).where(~KnowledgeMemoryModel.deleted).distinct())
    ).all()
    workspaces = sorted({row[0] for row in workspace_rows} | {row[0] for row in knowledge_workspaces})

    for workspace_id in workspaces:
        stats["workspaces"] += 1
        policy = await resolve_policy(db, workspace_id, None)
        budget = policy.eviction_budget_per_sweep
        protection_cutoff = now - timedelta(days=policy.protection_window_days)

        # -- TTL expiry (L3 per type, L4 per workspace-level TTL) ----------
        for layer in (
            (MemoryModel, None, MemoryModel.memory_type, "episodic", "l3_episodic", "user_memory"),
            (MemoryModel, None, MemoryModel.memory_type, "semantic", "l3_semantic", "user_memory"),
            (KnowledgeMemoryModel, KnowledgeMemoryModel.agent_id, None, None, "l4", "knowledge_memory"),
        ):
            model, agent_col, type_col, memory_type, ttl_key, target_type = layer
            ttl_days = policy.ttl_days.get(ttl_key, 0)
            if ttl_days <= 0:
                continue
            rows = await _ttl_expired_ids(
                db,
                model=model,
                workspace_id=workspace_id,
                agent_column=agent_col,
                agent_id=None,
                type_column=type_col,
                memory_type=memory_type,
                ttl_days=ttl_days,
                now=now,
            )
            for row in rows:
                if stats["ttl_archived"] + stats["evicted"] >= budget:
                    break
                agent_id = getattr(row, "agent_id", None)
                await _archive_row(
                    db,
                    row=row,
                    target_type=target_type,
                    agent_id=agent_id or ZERO_UUID,
                    reason=REASON_TTL,
                )
                stats["ttl_archived"] += 1
        await db.flush()

        # -- Capacity eviction (L3 per workspace, L4 per agent) ------------
        def budget_left(budget: int = budget) -> int:
            return budget - stats["ttl_archived"] - stats["evicted"]

        if policy.capacity.get("l3", 0) > 0:
            stats["evicted"] += await _evict_over_capacity(
                db,
                model=MemoryModel,
                target_type="user_memory",
                workspace_id=workspace_id,
                cap=policy.capacity["l3"],
                budget_left=budget_left,
                protection_cutoff=protection_cutoff,
                group_by_agent=False,
            )
        if policy.capacity.get("l4", 0) > 0:
            stats["evicted"] += await _evict_over_capacity(
                db,
                model=KnowledgeMemoryModel,
                target_type="knowledge_memory",
                workspace_id=workspace_id,
                cap=policy.capacity["l4"],
                budget_left=budget_left,
                protection_cutoff=protection_cutoff,
                group_by_agent=True,
            )
        await db.flush()

        # -- Promotion gate -------------------------------------------------
        stats["promoted"] += await _promote_eligible(
            db,
            workspace_id=workspace_id,
            policy=policy,
            now=now,
        )
        await db.flush()

    logger.info(
        "Lifecycle sweep: %s workspaces, ttl_archived=%s evicted=%s promoted=%s",
        stats["workspaces"],
        stats["ttl_archived"],
        stats["evicted"],
        stats["promoted"],
    )
    return stats


async def _evict_over_capacity(
    db: AsyncSession,
    *,
    model: type,
    target_type: str,
    workspace_id: uuid.UUID,
    cap: int,
    budget_left: Any,
    protection_cutoff: datetime,
    group_by_agent: bool,
) -> int:
    """Archive lowest-value rows until the scope fits its cap. Budget-bounded."""
    conditions = [
        ~model.deleted,
        model.archived_at.is_(None),
        model.workspace_id == workspace_id,
    ]
    rows = (await db.execute(select(model).where(*conditions))).scalars().all()
    if not rows:
        return 0

    groups: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        key = row.agent_id if group_by_agent else ZERO_UUID
        groups.setdefault(key, []).append(row)

    evicted = 0
    for _key, group_rows in groups.items():
        over = len(group_rows) - cap
        if over <= 0:
            continue
        scores = await _value_scores(db, rows=group_rows, target_type=target_type)
        candidates = [
            r
            for r in group_rows
            if (r.last_confirmed_at is None or _ensure_utc(r.last_confirmed_at) < protection_cutoff)
            and (r.last_accessed_at is None or _ensure_utc(r.last_accessed_at) < protection_cutoff)
        ]
        candidates.sort(key=lambda r: scores.get(r.id, 0.0))
        for row in candidates:
            if over <= 0 or evicted >= budget_left():
                break
            await _archive_row(
                db,
                row=row,
                target_type=target_type,
                agent_id=row.agent_id if group_by_agent else ZERO_UUID,
                reason=REASON_EVICTED,
            )
            evicted += 1
            over -= 1
    return evicted


async def _promote_eligible(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    policy: Any,
    now: datetime,
) -> int:
    """Copy eligible actor-scoped L3 rows into the shared namespace.

    Target width is bounded by the resolved sharing ceiling: only a
    ``workspace`` ceiling promotes to the workspace-shared namespace;
    a ``team`` ceiling would need a team target, which actor rows do not
    carry — those are skipped until a team mapping exists.
    """
    if not policy.promotion_enabled:
        return 0
    if policy.sharing_ceiling != "workspace":
        return 0

    conditions = [
        ~MemoryModel.deleted,
        MemoryModel.archived_at.is_(None),
        MemoryModel.workspace_id == workspace_id,
        MemoryModel.actor_id.is_not(None),
        MemoryModel.team_id.is_(None),
        MemoryModel.access_count >= policy.promotion_min_hits,
        MemoryModel.created_at <= now - timedelta(days=policy.promotion_min_age_days),
    ]
    rows = (await db.execute(select(MemoryModel).where(*conditions))).scalars().all()
    if not rows:
        return 0

    scores = await _value_scores(db, rows=rows, target_type="user_memory")
    promoted = 0
    for row in rows:
        if scores.get(row.id, 0.0) < policy.promotion_score_threshold:
            continue
        # Workspace-shared copy: the namespace-visible shape with both
        # team_id and actor_id NULL; the original actor row is retained.
        db.add(
            MemoryModel(
                workspace_id=row.workspace_id,
                content=row.content,
                scope={k: v for k, v in (row.scope or {}).items() if k != "user_id"},
                memory_type=row.memory_type,
                importance=row.importance,
                embedding=list(row.embedding or []),
                embedding_real=row.embedding_real,
                last_confirmed_at=row.last_confirmed_at,
                team_id=None,
                actor_id=None,
            )
        )
        db.add(
            MemoryEditLogModel(
                workspace_id=row.workspace_id,
                agent_id=row.agent_id if hasattr(row, "agent_id") else ZERO_UUID,
                tool_name=TOOL_NAME,
                target_type="user_memory",
                target_id=row.id,
                after_summary="promoted to workspace-shared namespace",
                reason=REASON_PROMOTED,
            )
        )
        promoted += 1
    return promoted


async def archive_memory_by_id(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    target_type: str,
    memory_id: uuid.UUID,
    reason: str = "manual_archive",
) -> bool:
    """Manual archive from the governance API (soft delete + audit)."""
    model = MemoryModel if target_type == "user_memory" else KnowledgeMemoryModel
    row = (
        await db.execute(
            select(model).where(
                model.id == memory_id,
                model.workspace_id == workspace_id,
                ~model.deleted,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.archived_at is not None:
        return False
    await _archive_row(db, row=row, target_type=target_type, agent_id=agent_id, reason=reason)
    await db.flush()
    return True


async def restore_memory_by_id(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    target_type: str,
    memory_id: uuid.UUID,
) -> bool:
    """Clear the archive marker; the row rejoins every retrieval path."""
    model = MemoryModel if target_type == "user_memory" else KnowledgeMemoryModel
    row = (
        await db.execute(
            select(model).where(
                model.id == memory_id,
                model.workspace_id == workspace_id,
                ~model.deleted,
            )
        )
    ).scalar_one_or_none()
    if row is None or row.archived_at is None:
        return False
    row.archived_at = None
    db.add(
        MemoryEditLogModel(
            workspace_id=workspace_id,
            agent_id=agent_id,
            tool_name=TOOL_NAME,
            target_type=target_type,
            target_id=memory_id,
            after_summary="restored from archive",
            reason=REASON_RESTORED,
        )
    )
    await db.flush()
    return True


class LifecycleSweeper:
    """Background lifecycle pass on its own cron schedule."""

    def __init__(
        self,
        *,
        schedule: str = "30 3 * * *",
        session_factory: Any | None = None,
    ) -> None:
        self.schedule = schedule
        self._session_factory = session_factory
        self._task: asyncio.Task[None] | None = None
        self._last_fired: datetime | None = None

    def _factory(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory
        from hecate.core.database import async_session_factory

        return async_session_factory

    async def _cron_if_due(self, now: datetime, tick: int) -> None:
        try:
            import croniter
        except ImportError:
            logger.debug("croniter unavailable — lifecycle cron disabled")
            return
        boundary = now - timedelta(seconds=max(tick, 30))
        try:
            cron = croniter.croniter(self.schedule, boundary)
            next_fire = cron.get_next(datetime)
        except ValueError as e:
            logger.warning("Invalid MEMORY_LIFECYCLE_SWEEP_SCHEDULE %r: %s", self.schedule, e)
            return
        if next_fire <= now:
            await self.run_once(now=now)

    async def run_once(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        """One sweep under the per-unit advisory locks (serialized with consolidation)."""
        factory = self._factory()
        stats: dict[str, Any] | None = None
        async with factory() as db:
            bind = db.get_bind()
            if bind.dialect.name == "postgresql":
                # A single global sweep lock (same namespace as unit locks)
                # keeps one sweeper per instance fleet; per-unit locks below
                # still serialize against consolidation runs.
                sweep_key = "lifecycle:sweep"
                lock_id = _unit_lock_id(sweep_key)
                result = await db.execute(text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": lock_id})
                if not result.scalar_one():
                    logger.debug("Lifecycle sweep locked elsewhere, skipping")
                    return None
            try:
                stats = await run_lifecycle_sweep(db, now=now)
                await db.commit()
            except Exception as e:  # noqa: BLE001 — a sweep failure is never fatal
                await db.rollback()
                logger.warning("Lifecycle sweep failed: %s", e)
                return None
            finally:
                if bind.dialect.name == "postgresql":
                    with contextlib.suppress(Exception):
                        await db.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
        return stats

    async def run_forever(self) -> None:
        """Tick loop; the interval bounds cron granularity (like consolidation)."""
        tick = 300
        while True:
            try:
                now = datetime.now(UTC)
                await self._cron_if_due(now, tick)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the loop outlives bad ticks
                logger.warning("Lifecycle sweep tick failed: %s", e)
            await asyncio.sleep(tick)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self.run_forever())
        logger.info("Lifecycle sweeper started (schedule=%s)", self.schedule)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Lifecycle sweeper stopped")


_sweeper: LifecycleSweeper | None = None


def start_lifecycle_sweeper(**kwargs: Any) -> LifecycleSweeper | None:
    """Start the module-level sweeper singleton (no-op when disabled)."""
    from hecate.core.config import settings

    global _sweeper  # noqa: PLW0603
    if not settings.MEMORY_LIFECYCLE_ENABLED:
        return None
    if _sweeper is not None:
        return _sweeper
    _sweeper = LifecycleSweeper(schedule=settings.MEMORY_LIFECYCLE_SWEEP_SCHEDULE, **kwargs)
    _sweeper.start()
    return _sweeper


async def stop_lifecycle_sweeper() -> None:
    global _sweeper  # noqa: PLW0603
    if _sweeper is None:
        return
    await _sweeper.stop()
    _sweeper = None
