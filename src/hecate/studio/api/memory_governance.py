"""Memory governance REST API (memory-api / memory-lifecycle capabilities).

Management-plane routes added by the memory-lifecycle-governance change:
edit-log and consolidation/reflection run queries, memory-policy CRUD
with a resolved view, archive/restore, lifecycle statistics, and recall
search. The legacy memory-management surface (L1 blocks, L3 user
memories, L4 knowledge, compression status) already lives in the
``hecate_memory.api.memory`` module and is deliberately not duplicated
here — its list/search routes inherit the archive exclusion through the
shared service layer.

Conventions follow the other ``studio/api`` modules: relative paths on a
bare ``APIRouter`` (mounted under ``/api`` by the composition root),
``get_auth_context`` workspace isolation (the workspace never comes from
request parameters), and offset/limit pagination with
``{"items": [...], "total": int}`` envelopes.

Authorization: governance routes require ``editor`` or ``admin``. Actor
privacy: L3 user memories are visible to their owner or an admin only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.composition.memory_policy import (
    PolicyValidationError,
    delete_policy,
    resolve_policy,
    upsert_policy,
)
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.memory import (
    ConsolidationFlushWindowModel,
    ConsolidationRunModel,
    KnowledgeMemoryModel,
    MemoryEditLogModel,
    MemoryModel,
    MemoryPolicyModel,
    MemoryPolicyReadSchema,
    MemoryPolicyUpsertSchema,
)
from hecate.models.task_memory import ReflectionRunModel
from hecate.models.workspace_member import WorkspaceRole

router = APIRouter()

_ZERO_UUID = uuid.UUID(int=0)


def _require_governance_role(ctx: AuthContext) -> None:
    """Governance routes are editor+; viewers are rejected (403)."""
    if ctx.role not in (WorkspaceRole.ADMIN, WorkspaceRole.EDITOR):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="editor or admin role required")


def _workspace_id(ctx: AuthContext) -> uuid.UUID:
    """The caller's workspace — resolved from auth, never from parameters."""
    return ctx.workspace_id or _ZERO_UUID


def _page_params(page: int, page_size: int) -> tuple[int, int]:
    return (page - 1) * page_size, page_size


def _memory_to_dict(row: Any) -> dict[str, Any]:
    """Serialization for L3 rows (scope JSON + lifecycle marker included)."""
    return {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "content": row.content,
        "scope": row.scope or {},
        "memory_type": row.memory_type,
        "importance": row.importance,
        "access_count": row.access_count,
        "revision": row.revision,
        "archived": row.archived_at is not None,
        "last_confirmed_at": row.last_confirmed_at.isoformat() if row.last_confirmed_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Audit + runs
# ---------------------------------------------------------------------------


@router.get("/memory/governance/audit")
async def query_edit_log(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    target_type: Annotated[str | None, Query()] = None,
    tool_name: Annotated[str | None, Query()] = None,
    reason: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """Query the memory edit audit log (all sources: tools, consolidation, lifecycle)."""
    _require_governance_role(ctx)
    conditions = [~MemoryEditLogModel.deleted, MemoryEditLogModel.workspace_id == _workspace_id(ctx)]
    if agent_id is not None:
        conditions.append(MemoryEditLogModel.agent_id == agent_id)
    if target_type:
        conditions.append(MemoryEditLogModel.target_type == target_type)
    if tool_name:
        conditions.append(MemoryEditLogModel.tool_name == tool_name)
    if reason:
        conditions.append(MemoryEditLogModel.reason == reason)
    if since is not None:
        conditions.append(MemoryEditLogModel.created_at >= since)
    if until is not None:
        conditions.append(MemoryEditLogModel.created_at <= until)

    base = select(MemoryEditLogModel).where(*conditions)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    offset, limit = _page_params(page, page_size)
    rows = (
        (await db.execute(base.order_by(MemoryEditLogModel.created_at.desc()).offset(offset).limit(limit)))
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                "id": str(r.id),
                "agent_id": str(r.agent_id),
                "session_id": str(r.session_id) if r.session_id else None,
                "trace_id": r.trace_id,
                "tool_name": r.tool_name,
                "target_type": r.target_type,
                "target_id": str(r.target_id),
                "revision_before": r.revision_before,
                "revision_after": r.revision_after,
                "before_summary": r.before_summary,
                "after_summary": r.after_summary,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "total": total,
    }


@router.get("/memory/governance/runs")
async def query_consolidation_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    kind: Annotated[str, Query(pattern="^(consolidation|reflection)$")] = "consolidation",
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    trigger: Annotated[str | None, Query()] = None,
    run_status: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    """List consolidation or reflection runs with filters (failure causes visible)."""
    _require_governance_role(ctx)
    ws = _workspace_id(ctx)
    if kind == "reflection":
        model: Any = ReflectionRunModel
    else:
        model = ConsolidationRunModel
    conditions: list[Any] = [~model.deleted, model.workspace_id == ws]
    if agent_id is not None:
        conditions.append(model.agent_id == agent_id)
    if trigger:
        conditions.append(model.trigger == trigger)
    if run_status:
        conditions.append(model.status == run_status)

    base = select(model).where(*conditions)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    offset, limit = _page_params(page, page_size)
    rows: list[Any] = (
        (await db.execute(base.order_by(model.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    )
    items: list[dict[str, Any]] = []
    for r in rows:
        item: dict[str, Any] = {
            "id": str(r.id),
            "kind": kind,
            "agent_id": str(r.agent_id),
            "trigger": r.trigger,
            "window_start": r.window_start.isoformat(),
            "window_end": r.window_end.isoformat(),
            "status": r.status,
            "error": getattr(r, "error", None),
            "created_at": r.created_at.isoformat(),
        }
        if kind == "consolidation":
            item.update(
                {
                    "user_id": str(r.user_id) if r.user_id else None,
                    "candidate_count": r.candidate_count,
                    "adopted_count": r.adopted_count,
                    "rejected_count": r.rejected_count,
                    "failed_count": r.failed_count,
                    "llm_calls": r.llm_calls,
                    "degraded": r.degraded,
                }
            )
        else:
            item.update(
                {
                    "actor_id": str(r.actor_id) if r.actor_id else None,
                    "team_id": str(r.team_id) if r.team_id else None,
                    "adopted_count": r.adopted_count,
                    "rejected_count": r.rejected_count,
                    "llm_calls": r.llm_calls,
                }
            )
        items.append(item)
    return {"items": items, "total": total}


# ---------------------------------------------------------------------------
# Memory policies
# ---------------------------------------------------------------------------


@router.get("/memory/policies")
async def list_memory_policies(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """List the workspace-level and agent-level policy rows."""
    _require_governance_role(ctx)
    rows = (
        (
            await db.execute(
                select(MemoryPolicyModel).where(
                    MemoryPolicyModel.workspace_id == _workspace_id(ctx),
                    ~MemoryPolicyModel.deleted,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [
            {
                **MemoryPolicyReadSchema.model_validate(r).model_dump(mode="json"),
                "scope": "workspace" if r.agent_id == _ZERO_UUID else "agent",
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/memory/policies/resolved")
async def resolved_memory_policy(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict:
    """The effective policy for a scope, with each field's source level."""
    _require_governance_role(ctx)
    ws = _workspace_id(ctx)
    resolved = await resolve_policy(db, ws, agent_id)
    rows = (
        (
            await db.execute(
                select(MemoryPolicyModel).where(
                    MemoryPolicyModel.workspace_id == ws,
                    ~MemoryPolicyModel.deleted,
                )
            )
        )
        .scalars()
        .all()
    )
    workspace_row = next((r for r in rows if r.agent_id == _ZERO_UUID and r.enabled), None)
    agent_row = next((r for r in rows if r.agent_id == agent_id and r.enabled), None) if agent_id is not None else None

    def _source(from_agent: bool) -> str:
        if from_agent:
            return "agent"
        return "workspace" if workspace_row is not None else "platform"

    workspace_groups = set((workspace_row.params or {}).keys()) if workspace_row is not None else set()
    agent_groups = set((agent_row.params or {}).keys()) if agent_row is not None else set()
    return {
        "workspace_id": str(ws),
        "agent_id": str(agent_id) if agent_id else None,
        "values": {
            "tool_subset": sorted(resolved.tool_subset) if resolved.tool_subset is not None else None,
            "sharing_ceiling": resolved.sharing_ceiling,
            "ttl_days": resolved.ttl_days,
            "capacity": resolved.capacity,
            "eviction_budget_per_sweep": resolved.eviction_budget_per_sweep,
            "protection_window_days": resolved.protection_window_days,
            "promotion": {
                "enabled": resolved.promotion_enabled,
                "score_threshold": resolved.promotion_score_threshold,
                "min_hits": resolved.promotion_min_hits,
                "min_age_days": resolved.promotion_min_age_days,
            },
            "flush_enabled": resolved.flush_enabled,
            "max_llm_calls_per_run": resolved.max_llm_calls_per_run,
            "max_mutations_per_run": resolved.max_mutations_per_run,
        },
        "sources": {
            "tool_subset": _source(agent_row is not None and agent_row.tool_subset is not None),
            "sharing_ceiling": _source(agent_row is not None and agent_row.sharing_ceiling is not None),
            "ttl_days": _source("ttl" in agent_groups),
            "capacity": _source("capacity" in agent_groups),
            "promotion": _source("promotion" in agent_groups),
            "flush_enabled": _source("flush" in agent_groups or "flush" in workspace_groups),
            "eviction": _source("eviction" in agent_groups),
        },
    }


@router.put("/memory/policies/workspace")
async def put_workspace_policy(
    payload: MemoryPolicyUpsertSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create or replace the workspace-level memory policy."""
    _require_governance_role(ctx)
    return await _upsert_policy_response(db, ctx, payload, agent_id=None)


@router.put("/memory/policies/agents/{agent_id}")
async def put_agent_policy(
    agent_id: uuid.UUID,
    payload: MemoryPolicyUpsertSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create or replace one agent's memory-policy override."""
    _require_governance_role(ctx)
    return await _upsert_policy_response(db, ctx, payload, agent_id=agent_id)


async def _upsert_policy_response(
    db: AsyncSession,
    ctx: AuthContext,
    payload: MemoryPolicyUpsertSchema,
    *,
    agent_id: uuid.UUID | None,
) -> dict:
    try:
        row = await upsert_policy(
            db,
            _workspace_id(ctx),
            agent_id,
            enabled=payload.enabled,
            tool_subset=payload.tool_subset,
            sharing_ceiling=payload.sharing_ceiling,
            params=payload.params,
            actor_id=ctx.user_id,
        )
    except PolicyValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await db.commit()
    return MemoryPolicyReadSchema.model_validate(row).model_dump(mode="json", by_alias=True)


@router.delete("/memory/policies/workspace", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace_policy(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Remove the workspace-level policy (falls back to platform defaults)."""
    _require_governance_role(ctx)
    await delete_policy(db, _workspace_id(ctx), None, actor_id=ctx.user_id)
    await db.commit()


@router.delete("/memory/policies/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_policy(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Remove one agent's policy override (falls back to the workspace chain)."""
    _require_governance_role(ctx)
    await delete_policy(db, _workspace_id(ctx), agent_id, actor_id=ctx.user_id)
    await db.commit()


# ---------------------------------------------------------------------------
# Archive / restore / stats / recall
# ---------------------------------------------------------------------------


@router.post("/memory/governance/archive")
async def archive_memory(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Soft-archive an L3/L4 memory (leaves every retrieval path, restorable)."""
    _require_governance_role(ctx)

    return await _archive_lifecycle_action(db, ctx, payload, restore=False)


@router.post("/memory/governance/restore")
async def restore_memory(
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Restore an archived memory to every retrieval path."""
    _require_governance_role(ctx)

    return await _archive_lifecycle_action(db, ctx, payload, restore=True)


async def _archive_lifecycle_action(
    db: AsyncSession,
    ctx: AuthContext,
    payload: dict[str, Any],
    *,
    restore: bool,
) -> dict:
    target_type = str(payload.get("target_type", ""))
    if target_type not in ("user_memory", "knowledge_memory"):
        raise HTTPException(status_code=422, detail="target_type must be user_memory|knowledge_memory")
    try:
        memory_id = uuid.UUID(str(payload.get("memory_id", "")))
    except ValueError as e:
        raise HTTPException(status_code=422, detail="memory_id must be a UUID") from e
    raw_agent = payload.get("agent_id")
    agent_id = uuid.UUID(str(raw_agent)) if raw_agent else _ZERO_UUID

    if target_type == "user_memory":
        row = (
            await db.execute(
                select(MemoryModel).where(
                    MemoryModel.id == memory_id,
                    MemoryModel.workspace_id == _workspace_id(ctx),
                    ~MemoryModel.deleted,
                )
            )
        ).scalar_one_or_none()
        if row is not None and not _actor_or_admin_private(ctx, row.actor_id):
            raise HTTPException(status_code=403, detail="user memories are private to their owner")

    from hecate_memory.memory.lifecycle import archive_memory_by_id, restore_memory_by_id

    fn = restore_memory_by_id if restore else archive_memory_by_id
    ok = await fn(
        db,
        workspace_id=_workspace_id(ctx),
        agent_id=agent_id,
        target_type=target_type,
        memory_id=memory_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="memory not found or already in requested state")
    await db.commit()
    return {"ok": True, "memory_id": str(memory_id), "target_type": target_type}


def _actor_or_admin_private(ctx: AuthContext, actor_id: uuid.UUID | None) -> bool:
    """Actor privacy: an L3 memory is visible to its owner or an admin."""
    return ctx.role == WorkspaceRole.ADMIN or actor_id is None or actor_id == ctx.user_id


@router.get("/memory/governance/archived")
async def list_archived_memories(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    target_type: Annotated[str, Query(pattern="^(user_memory|knowledge_memory)$")] = "user_memory",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List archived (soft-deleted) memories with their latest lifecycle reason."""
    _require_governance_role(ctx)
    model = MemoryModel if target_type == "user_memory" else KnowledgeMemoryModel
    ws = _workspace_id(ctx)
    conditions: list[Any] = [model.workspace_id == ws, ~model.deleted, model.archived_at.is_not(None)]
    if target_type == "user_memory" and ctx.role != WorkspaceRole.ADMIN:
        conditions.append(MemoryModel.actor_id == ctx.user_id)

    base = select(model).where(*conditions)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    offset, limit = _page_params(page, page_size)
    rows = (await db.execute(base.order_by(model.archived_at.desc()).offset(offset).limit(limit))).scalars().all()
    # Latest lifecycle reason per row from the audit log.
    reasons: dict[uuid.UUID, str | None] = {}
    ids = [r.id for r in rows]
    if ids:
        log_rows = (
            (
                await db.execute(
                    select(MemoryEditLogModel)
                    .where(
                        MemoryEditLogModel.target_id.in_(ids),
                        MemoryEditLogModel.tool_name == "lifecycle",
                        ~MemoryEditLogModel.deleted,
                    )
                    .order_by(MemoryEditLogModel.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        for log_row in log_rows:
            reasons.setdefault(log_row.target_id, log_row.reason)

    return {
        "items": [
            {
                **_memory_to_dict(r),
                "archived_at": r.archived_at.isoformat() if r.archived_at else None,
                "archive_reason": reasons.get(r.id),
            }
            for r in rows
        ],
        "total": total,
    }


@router.get("/memory/governance/search")
async def search_workspace_memories(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    q: Annotated[str, Query(min_length=1)],
    target_type: Annotated[str, Query(pattern="^(user_memory|knowledge_memory)$")] = "user_memory",
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict:
    """Workspace-level semantic search over active memories (actor privacy applies)."""
    _require_governance_role(ctx)
    from hecate_memory.memory.user_memory import UserMemoryService

    scope: dict[str, str] | None = None
    if target_type == "user_memory" and ctx.role != WorkspaceRole.ADMIN:
        scope = {"user_id": str(ctx.user_id)}
    svc = UserMemoryService(db)
    hits = await svc.retrieve_memories_scored(_workspace_id(ctx), q, scope=scope, top_k=top_k)
    return {
        "items": [
            {
                "memory": _memory_to_dict(h.memory),
                "score": h.score,
                "breakdown": h.breakdown,
            }
            for h in hits
        ],
        "total": len(hits),
    }


@router.get("/memory/governance/stats")
async def lifecycle_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Overview counters: per-layer actives, lifecycle ops by reason, recent runs."""
    _require_governance_role(ctx)
    ws = _workspace_id(ctx)
    l3_active = (
        await db.execute(
            select(func.count())
            .select_from(MemoryModel)
            .where(MemoryModel.workspace_id == ws, ~MemoryModel.deleted, MemoryModel.archived_at.is_(None))
        )
    ).scalar_one()
    l4_active = (
        await db.execute(
            select(func.count())
            .select_from(KnowledgeMemoryModel)
            .where(
                KnowledgeMemoryModel.workspace_id == ws,
                ~KnowledgeMemoryModel.deleted,
                KnowledgeMemoryModel.archived_at.is_(None),
            )
        )
    ).scalar_one()
    archived_rows = (
        await db.execute(
            select(MemoryEditLogModel.reason, func.count())
            .select_from(MemoryEditLogModel)
            .where(
                MemoryEditLogModel.workspace_id == ws,
                MemoryEditLogModel.tool_name == "lifecycle",
                ~MemoryEditLogModel.deleted,
            )
            .group_by(MemoryEditLogModel.reason)
        )
    ).all()
    recent_runs = (
        (
            await db.execute(
                select(ConsolidationRunModel)
                .where(ConsolidationRunModel.workspace_id == ws, ~ConsolidationRunModel.deleted)
                .order_by(ConsolidationRunModel.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    pending_flush = (
        await db.execute(
            select(func.count())
            .select_from(ConsolidationFlushWindowModel)
            .where(
                ConsolidationFlushWindowModel.workspace_id == ws,
                ~ConsolidationFlushWindowModel.deleted,
            )
        )
    ).scalar_one()
    return {
        "counts": {"l3_active": l3_active, "l4_active": l4_active, "pending_flush_windows": pending_flush},
        "lifecycle_operations_by_reason": {reason or "manual_archive": count for reason, count in archived_rows},
        "recent_consolidation_runs": [
            {
                "id": str(r.id),
                "trigger": r.trigger,
                "status": r.status,
                "adopted": r.adopted_count,
                "created_at": r.created_at.isoformat(),
            }
            for r in recent_runs
        ],
    }


@router.post("/memory/governance/recall/search")
async def search_recall(
    payload: dict[str, Any],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Conversation recall search (same semantics as the conversation_search tool)."""
    _require_governance_role(ctx)
    from hecate.core.composition.memory_provider import (
        CAP_SEARCH_RECALL,
        provider_supports,
        resolve_memory_provider,
    )

    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    raw_agent = payload.get("agent_id")
    if not raw_agent:
        raise HTTPException(status_code=422, detail="agent_id is required")

    provider = resolve_memory_provider()
    if provider is None or not provider_supports(provider, CAP_SEARCH_RECALL):
        raise HTTPException(status_code=503, detail="recall search unavailable (indexing disabled?)")
    page = await provider.search_recall(
        query=query,
        workspace_id=_workspace_id(ctx),
        agent_id=uuid.UUID(str(raw_agent)),
        limit=int(payload.get("limit", 5)),
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
        roles=payload.get("roles"),
        cursor=payload.get("cursor"),
        exclude_session_ids=[uuid.UUID(str(s)) for s in (payload.get("exclude_session_ids") or [])],
    )
    return {
        "items": [
            {
                "recall_id": str(h.recall_id),
                "session_id": str(h.session_id),
                "conversation_id": str(h.conversation_id) if h.conversation_id else None,
                "role": h.role,
                "content": h.content,
                "score": h.score,
                "timestamp": h.timestamp.isoformat() if h.timestamp else None,
            }
            for h in page.hits
        ],
        "low_signal": page.low_signal,
        "next_cursor": page.next_cursor,
    }
