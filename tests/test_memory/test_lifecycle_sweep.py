"""Tests for the memory lifecycle sweeper (memory-lifecycle capability).

Covers TTL expiry (per-layer, L4 evergreen), capacity eviction (lowest
value first, protection window, budget), promotion gate (thresholds,
ceiling bound, disabled-by-default), archive/restore with lifecycle
audit, and the retrieval-path exclusion of archived rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hecate_memory.memory.lifecycle import (
    REASON_EVICTED,
    REASON_PROMOTED,
    REASON_RESTORED,
    REASON_TTL,
    archive_memory_by_id,
    restore_memory_by_id,
    run_lifecycle_sweep,
    start_lifecycle_sweeper,
)
from hecate_memory.memory.user_memory import UserMemoryService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.composition.memory_policy import invalidate_memory_policy_cache, upsert_policy
from hecate.models.memory import MemoryEditLogModel, MemoryModel

_WS = uuid.uuid4()
_AGENT = uuid.uuid4()
_USER = uuid.uuid4()
_NOW = datetime(2026, 9, 23, 4, 0, 0, tzinfo=UTC)


def _l3(
    content: str,
    *,
    memory_type: str = "episodic",
    confirmed_days_ago: float = 1.0,
    importance: float = 0.5,
    user: uuid.UUID | None = _USER,
    actor: uuid.UUID | None = None,
    access_count: int = 0,
) -> MemoryModel:
    confirmed = _NOW - timedelta(days=confirmed_days_ago)
    scope: dict = {}
    if user is not None:
        scope["user_id"] = str(user)
    if actor is not None:
        scope["actor_id"] = str(actor)
    return MemoryModel(
        workspace_id=_WS,
        content=content,
        scope=scope,
        memory_type=memory_type,
        importance=importance,
        embedding=[],
        access_count=access_count,
        last_confirmed_at=confirmed,
        created_at=confirmed,
        updated_at=confirmed,
        actor_id=actor,
    )


async def _audit_rows(db: AsyncSession, reason: str) -> list[MemoryEditLogModel]:
    return (
        (
            await db.execute(
                select(MemoryEditLogModel).where(
                    MemoryEditLogModel.tool_name == "lifecycle",
                    MemoryEditLogModel.reason == reason,
                )
            )
        )
        .scalars()
        .all()
    )


async def test_ttl_expires_l3_episodic_only(db_session: AsyncSession) -> None:
    await upsert_policy(
        db_session, _WS, None, params={"ttl": {"l3_episodic_days": 30, "l3_semantic_days": 0, "l4_days": 0}}
    )
    invalidate_memory_policy_cache()
    old_episodic = _l3("old episodic", confirmed_days_ago=60)
    recent_episodic = _l3("recent episodic", confirmed_days_ago=5)
    old_semantic = _l3("old semantic", memory_type="semantic", confirmed_days_ago=400)
    db_session.add_all([old_episodic, recent_episodic, old_semantic])
    await db_session.flush()

    stats = await run_lifecycle_sweep(db_session, now=_NOW)
    await db_session.flush()

    assert stats["ttl_archived"] == 1
    assert old_episodic.archived_at is not None
    assert recent_episodic.archived_at is None
    # Semantic TTL is 0 (never) — the 400-day-old row survives.
    assert old_semantic.archived_at is None
    assert len(await _audit_rows(db_session, REASON_TTL)) == 1


async def test_capacity_evicts_lowest_value_with_protection(db_session: AsyncSession) -> None:
    await upsert_policy(
        db_session,
        _WS,
        None,
        params={"capacity": {"l3": 2}, "eviction": {"budget_per_sweep": 1, "protection_window_days": 7}},
    )
    invalidate_memory_policy_cache()
    protected = _l3("recently confirmed", confirmed_days_ago=1)  # in protection window
    low_value = _l3("low value", confirmed_days_ago=100)
    mid_value = _l3("mid value", confirmed_days_ago=90, importance=0.7)
    db_session.add_all([protected, low_value, mid_value])
    await db_session.flush()

    stats = await run_lifecycle_sweep(db_session, now=_NOW)

    # Cap 2 with 3 rows → 1 eviction; the protected row is exempt, so the
    # lower-value candidate goes first; the budget caps it at exactly 1.
    assert stats["evicted"] == 1
    assert low_value.archived_at is not None
    assert protected.archived_at is None
    assert mid_value.archived_at is None
    assert len(await _audit_rows(db_session, REASON_EVICTED)) == 1


async def test_promotion_disabled_by_default_and_ceiling_bounded(db_session: AsyncSession) -> None:
    actor = uuid.uuid4()
    eligible = _l3(
        "shared fact worth promoting",
        memory_type="semantic",
        confirmed_days_ago=30,
        access_count=10,
        user=actor,
        actor=actor,
    )
    db_session.add(eligible)
    await db_session.flush()

    # Default: promotion off → nothing happens even for an eligible row.
    await run_lifecycle_sweep(db_session, now=_NOW)
    assert eligible.archived_at is None
    assert len(await _audit_rows(db_session, REASON_PROMOTED)) == 0

    # Ceiling 'team' cannot promote to the workspace-shared namespace.
    await upsert_policy(
        db_session,
        _WS,
        None,
        params={
            "promotion": {"enabled": True, "score_threshold": 0.1, "min_hits": 1, "min_age_days": 1},
        },
        sharing_ceiling="team",
    )
    invalidate_memory_policy_cache()
    await run_lifecycle_sweep(db_session, now=_NOW)
    shared = (await db_session.execute(select(MemoryModel).where(MemoryModel.actor_id.is_(None)))).scalars().all()
    assert shared == []

    # Workspace ceiling: the row is copied into the shared namespace.
    # Upsert is full-row replace, so the promotion params ride along.
    await upsert_policy(
        db_session,
        _WS,
        None,
        params={
            "promotion": {"enabled": True, "score_threshold": 0.1, "min_hits": 1, "min_age_days": 1},
        },
        sharing_ceiling="workspace",
    )
    invalidate_memory_policy_cache()
    stats = await run_lifecycle_sweep(db_session, now=_NOW)

    assert stats["promoted"] == 1
    shared = (await db_session.execute(select(MemoryModel).where(MemoryModel.actor_id.is_(None)))).scalars().all()
    assert len(shared) == 1
    assert shared[0].content == "shared fact worth promoting"
    assert shared[0].scope.get("user_id") is None  # actor identity stripped
    assert eligible.archived_at is None  # the original row is retained
    assert len(await _audit_rows(db_session, REASON_PROMOTED)) == 1


async def test_archive_and_restore_roundtrip(db_session: AsyncSession) -> None:
    row = _l3("archive me")
    db_session.add(row)
    await db_session.flush()

    assert (
        await archive_memory_by_id(
            db_session,
            workspace_id=_WS,
            agent_id=uuid.uuid4(),
            target_type="user_memory",
            memory_id=row.id,
        )
        is True
    )
    assert row.archived_at is not None
    assert row.deleted is False  # soft archive, not a delete

    assert (
        await restore_memory_by_id(
            db_session,
            workspace_id=_WS,
            agent_id=uuid.uuid4(),
            target_type="user_memory",
            memory_id=row.id,
        )
        is True
    )
    assert row.archived_at is None
    assert len(await _audit_rows(db_session, REASON_RESTORED)) == 1


async def test_archived_rows_leave_retrieval_paths(db_session: AsyncSession) -> None:
    keep = _l3("still retrievable", memory_type="semantic")
    gone = _l3("archived away", memory_type="semantic")
    db_session.add_all([keep, gone])
    await db_session.flush()
    gone.archived_at = datetime.now(UTC)
    await db_session.flush()

    service = UserMemoryService(db_session)
    hits = await service.retrieve_memories(workspace_id=_WS, query="retrievable", top_k=10)
    contents = [m.content for m in hits]
    assert "still retrievable" in contents
    assert "archived away" not in contents


def test_sweeper_start_is_gated_by_flag():
    """MEMORY_LIFECYCLE_ENABLED=false (default) → no sweeper starts."""
    assert start_lifecycle_sweeper() is None


async def test_l4_capacity_groups_by_agent(db_session: AsyncSession) -> None:
    from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService  # noqa: F401

    from hecate.models.memory import KnowledgeMemoryModel

    await upsert_policy(db_session, _WS, None, params={"capacity": {"l4": 1}})
    invalidate_memory_policy_cache()
    agent_a, agent_b = uuid.uuid4(), uuid.uuid4()
    old = (_NOW - timedelta(days=30)).replace(tzinfo=None)
    a1 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=agent_a, content="a1", last_confirmed_at=old)
    a2 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=agent_a, content="a2", importance=0.9, last_confirmed_at=old)
    b1 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=agent_b, content="b1", last_confirmed_at=old)
    db_session.add_all([a1, a2, b1])
    await db_session.flush()

    stats = await run_lifecycle_sweep(db_session, now=_NOW)

    # Agent A is over cap (2 > 1) → the lower-value a1 goes; agent B fits.
    assert stats["evicted"] == 1
    assert a1.archived_at is not None
    assert a2.archived_at is None
    assert b1.archived_at is None


async def test_l4_ttl_uses_agent_level_policy(db_session: AsyncSession) -> None:
    """L4 TTL resolves per agent: workspace TTL off, agent-A TTL on.

    A workspace-level resolution would apply the workspace's (zero) L4 TTL
    to every agent and archive nothing — agent-level tightening must win."""
    from hecate.models.memory import KnowledgeMemoryModel

    await upsert_policy(db_session, _WS, None, params={"ttl": {"l4_days": 0}})
    await upsert_policy(db_session, _WS, _AGENT, params={"ttl": {"l4_days": 7}})
    invalidate_memory_policy_cache()
    agent_b = uuid.uuid4()
    old = (_NOW - timedelta(days=30)).replace(tzinfo=None)
    a1 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=_AGENT, content="a1", last_confirmed_at=old)
    b1 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=agent_b, content="b1", last_confirmed_at=old)
    db_session.add_all([a1, b1])
    await db_session.flush()

    stats = await run_lifecycle_sweep(db_session, now=_NOW)

    assert stats["ttl_archived"] == 1
    assert a1.archived_at is not None
    assert b1.archived_at is None


async def test_l4_capacity_uses_agent_level_policy(db_session: AsyncSession) -> None:
    """L4 capacity resolves per agent: workspace cap loose, agent-A cap tight."""
    from hecate.models.memory import KnowledgeMemoryModel

    await upsert_policy(db_session, _WS, None, params={"capacity": {"l4": 5}})
    await upsert_policy(db_session, _WS, _AGENT, params={"capacity": {"l4": 1}})
    invalidate_memory_policy_cache()
    agent_b = uuid.uuid4()
    old = (_NOW - timedelta(days=30)).replace(tzinfo=None)
    a1 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=_AGENT, content="a1", last_confirmed_at=old)
    a2 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=_AGENT, content="a2", importance=0.9, last_confirmed_at=old)
    b1 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=agent_b, content="b1", last_confirmed_at=old)
    b2 = KnowledgeMemoryModel(workspace_id=_WS, agent_id=agent_b, content="b2", last_confirmed_at=old)
    db_session.add_all([a1, a2, b1, b2])
    await db_session.flush()

    stats = await run_lifecycle_sweep(db_session, now=_NOW)

    # Only agent A is over its own cap; agent B's two rows fit the workspace cap.
    assert stats["evicted"] == 1
    assert a1.archived_at is not None
    assert a2.archived_at is None
    assert b1.archived_at is None
    assert b2.archived_at is None


async def test_promotion_is_idempotent_across_sweeps(db_session: AsyncSession) -> None:
    """A source row promotes exactly once — re-running the sweep must not
    append another shared copy (B3 promotion-idempotency)."""
    actor = uuid.uuid4()
    eligible = _l3(
        "stable fact",
        memory_type="semantic",
        confirmed_days_ago=30,
        access_count=10,
        user=actor,
        actor=actor,
    )
    db_session.add(eligible)
    await db_session.flush()
    await upsert_policy(
        db_session,
        _WS,
        None,
        params={"promotion": {"enabled": True, "score_threshold": 0.1, "min_hits": 1, "min_age_days": 1}},
        sharing_ceiling="workspace",
    )
    invalidate_memory_policy_cache()

    first = await run_lifecycle_sweep(db_session, now=_NOW)
    second = await run_lifecycle_sweep(db_session, now=_NOW)

    assert first["promoted"] == 1
    assert second["promoted"] == 0
    shared = (await db_session.execute(select(MemoryModel).where(MemoryModel.actor_id.is_(None)))).scalars().all()
    assert len(shared) == 1
    assert shared[0].promoted_from_id == eligible.id


async def test_promotion_copy_blocks_recreation_after_withdrawal(db_session: AsyncSession) -> None:
    """The partial unique index has no deleted filter: a forgotten shared
    copy still blocks re-promotion — withdrawal wins over the sweeper."""
    actor = uuid.uuid4()
    source = _l3("fact", memory_type="semantic", confirmed_days_ago=30, access_count=10, user=actor, actor=actor)
    withdrawn_copy = MemoryModel(
        workspace_id=_WS,
        content="fact",
        scope={},
        memory_type="semantic",
        embedding=[],
        team_id=None,
        actor_id=None,
        promoted_from_id=source.id,
        deleted=True,
        deleted_at=_NOW.replace(tzinfo=None),
    )
    # Flush the source first: client-side id defaults evaluate at flush
    # time, and the copy's promoted_from_id must capture the real id.
    db_session.add(source)
    await db_session.flush()
    withdrawn_copy.promoted_from_id = source.id
    db_session.add(withdrawn_copy)
    await db_session.flush()
    await upsert_policy(
        db_session,
        _WS,
        None,
        params={"promotion": {"enabled": True, "score_threshold": 0.1, "min_hits": 1, "min_age_days": 1}},
        sharing_ceiling="workspace",
    )
    invalidate_memory_policy_cache()

    stats = await run_lifecycle_sweep(db_session, now=_NOW)

    assert stats["promoted"] == 0
    live_copies = (
        (await db_session.execute(select(MemoryModel).where(MemoryModel.actor_id.is_(None), ~MemoryModel.deleted)))
        .scalars()
        .all()
    )
    assert live_copies == []
