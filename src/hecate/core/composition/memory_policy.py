"""Memory policy resolution (memory-policy capability).

Resolves the effective memory policy for a ``(workspace, agent)`` scope
over a fixed chain: platform env defaults → workspace-level policy row →
agent-level override row. Convergence rules:

- **Permission-surface fields** (memory tool subset, namespace sharing
  ceiling) can only *narrow* down the chain — an agent override may never
  widen what its workspace grants, and nothing may widen the platform
  flag surface.
- **Numeric fields** (TTLs, capacities, budgets, thresholds) may be
  freely overridden but are clamped to platform hard caps.

An empty ``memory_policies`` table resolves every scope to the platform
defaults, keeping behavior byte-identical until a policy is written.
Resolution results are cached in-process (short TTL) and invalidated on
policy writes so changes take effect on the next resolution without a
restart.

All policy writes land in ``memory_edit_log`` (``target_type=
"memory_policy"``) so the governance UI's audit view covers them too.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings
from hecate.models.memory import MemoryEditLogModel, MemoryPolicyModel

logger = logging.getLogger(__name__)

ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# Widest-permitted share levels, narrowest first. A ceiling deeper in this
# ordering widens what may be shared; children may not move it wider.
SHARING_WIDTH: dict[str, int] = {"actor": 0, "team": 1, "workspace": 2}

# Policy params groups and the hard caps for their numeric members.
_PARAM_CAPS: dict[str, dict[str, int]] = {
    "ttl": {
        "l3_episodic_days": settings.MEMORY_POLICY_MAX_TTL_DAYS,
        "l3_semantic_days": settings.MEMORY_POLICY_MAX_TTL_DAYS,
        "l4_days": settings.MEMORY_POLICY_MAX_TTL_DAYS,
    },
    "capacity": {
        "l3": settings.MEMORY_POLICY_MAX_CAPACITY,
        "l4": settings.MEMORY_POLICY_MAX_CAPACITY,
    },
    "eviction": {
        "budget_per_sweep": settings.MEMORY_POLICY_MAX_EVICTION_BUDGET,
        "protection_window_days": settings.MEMORY_POLICY_MAX_TTL_DAYS,
    },
    "promotion": {
        "min_hits": settings.MEMORY_POLICY_MAX_CAPACITY,
        "min_age_days": settings.MEMORY_POLICY_MAX_TTL_DAYS,
    },
    "budgets": {
        "max_llm_calls_per_run": settings.MEMORY_POLICY_MAX_LLM_CALLS_PER_RUN,
        "max_mutations_per_run": settings.MEMORY_POLICY_MAX_MUTATIONS_PER_RUN,
    },
}

_CACHE_TTL_SECONDS = 60.0
_cache: dict[tuple[uuid.UUID, uuid.UUID], tuple[float, ResolvedMemoryPolicy]] = {}


class PolicyValidationError(ValueError):
    """A policy payload violates validation or narrowing rules."""


@dataclass(frozen=True)
class ResolvedMemoryPolicy:
    """Effective memory policy for one ``(workspace, agent)`` scope."""

    workspace_id: uuid.UUID
    agent_id: uuid.UUID | None
    # None = no policy narrows the tool surface; the platform flag surface
    # applies unchanged. Non-empty = the only permitted memory tools.
    tool_subset: frozenset[str] | None
    sharing_ceiling: str
    ttl_days: dict[str, int]
    capacity: dict[str, int]
    eviction_budget_per_sweep: int
    protection_window_days: int
    promotion_enabled: bool
    promotion_score_threshold: float
    promotion_min_hits: int
    promotion_min_age_days: int
    flush_enabled: bool
    max_llm_calls_per_run: int
    max_mutations_per_run: int


def _platform_defaults(agent_id: uuid.UUID | None) -> ResolvedMemoryPolicy:
    """The platform env-default policy (the chain's root)."""
    return ResolvedMemoryPolicy(
        workspace_id=ZERO_UUID,
        agent_id=agent_id,
        tool_subset=None,
        sharing_ceiling="workspace",
        ttl_days={
            "l3_episodic": settings.MEMORY_TTL_L3_EPISODIC_DAYS,
            "l3_semantic": settings.MEMORY_TTL_L3_SEMANTIC_DAYS,
            "l4": settings.MEMORY_TTL_L4_DAYS,
        },
        capacity={"l3": settings.MEMORY_CAPACITY_L3, "l4": settings.MEMORY_CAPACITY_L4},
        eviction_budget_per_sweep=settings.MEMORY_LIFECYCLE_EVICTION_BUDGET_PER_SWEEP,
        protection_window_days=settings.MEMORY_LIFECYCLE_PROTECTION_WINDOW_DAYS,
        promotion_enabled=False,
        promotion_score_threshold=settings.MEMORY_PROMOTION_SCORE_THRESHOLD,
        promotion_min_hits=settings.MEMORY_PROMOTION_MIN_HITS,
        promotion_min_age_days=settings.MEMORY_PROMOTION_MIN_AGE_DAYS,
        flush_enabled=settings.MEMORY_FLUSH_ENABLED,
        max_llm_calls_per_run=settings.CONSOLIDATION_MAX_LLM_CALLS_PER_RUN,
        max_mutations_per_run=settings.CONSOLIDATION_MAX_MUTATIONS_PER_RUN,
    )


def _apply_row(base: ResolvedMemoryPolicy, row: MemoryPolicyModel) -> ResolvedMemoryPolicy:
    """Fold one policy row into the resolved state (parent → child)."""
    tool_subset = base.tool_subset
    if row.tool_subset is not None:
        row_subset = frozenset(row.tool_subset)
        # Narrowing only: intersect with whatever the parent grants.
        tool_subset = row_subset if tool_subset is None else (tool_subset & row_subset)

    sharing_ceiling = base.sharing_ceiling
    if (
        row.sharing_ceiling is not None
        # Narrowing only: a child ceiling may not be wider than the parent's.
        and SHARING_WIDTH[row.sharing_ceiling] <= SHARING_WIDTH[base.sharing_ceiling]
    ):
        sharing_ceiling = row.sharing_ceiling

    ttl_days = dict(base.ttl_days)
    capacity = dict(base.capacity)
    eviction_budget = base.eviction_budget_per_sweep
    protection_days = base.protection_window_days
    promotion_enabled = base.promotion_enabled
    promotion_score = base.promotion_score_threshold
    promotion_min_hits = base.promotion_min_hits
    promotion_min_age = base.promotion_min_age_days
    flush_enabled = base.flush_enabled
    max_llm_calls = base.max_llm_calls_per_run
    max_mutations = base.max_mutations_per_run

    params = row.params or {}
    ttl = params.get("ttl") or {}
    for key, cap in _PARAM_CAPS["ttl"].items():
        if ttl.get(key) is not None:
            ttl_days[key.removesuffix("_days")] = min(int(ttl[key]), cap)
    cap_params = params.get("capacity") or {}
    for key, cap in _PARAM_CAPS["capacity"].items():
        if cap_params.get(key) is not None:
            capacity[key] = min(int(cap_params[key]), cap)
    evict = params.get("eviction") or {}
    if evict.get("budget_per_sweep") is not None:
        eviction_budget = min(int(evict["budget_per_sweep"]), _PARAM_CAPS["eviction"]["budget_per_sweep"])
    if evict.get("protection_window_days") is not None:
        protection_days = min(int(evict["protection_window_days"]), _PARAM_CAPS["eviction"]["protection_window_days"])
    promo = params.get("promotion") or {}
    if promo.get("enabled") is not None:
        promotion_enabled = bool(promo["enabled"])
    if promo.get("score_threshold") is not None:
        promotion_score = max(0.0, min(float(promo["score_threshold"]), 1.0))
    if promo.get("min_hits") is not None:
        promotion_min_hits = min(int(promo["min_hits"]), _PARAM_CAPS["promotion"]["min_hits"])
    if promo.get("min_age_days") is not None:
        promotion_min_age = min(int(promo["min_age_days"]), _PARAM_CAPS["promotion"]["min_age_days"])
    flush = params.get("flush") or {}
    if flush.get("enabled") is not None:
        # The platform flag is the hard upper bound — policy can only narrow.
        flush_enabled = base.flush_enabled and bool(flush["enabled"])
    budgets = params.get("budgets") or {}
    if budgets.get("max_llm_calls_per_run") is not None:
        max_llm_calls = min(int(budgets["max_llm_calls_per_run"]), _PARAM_CAPS["budgets"]["max_llm_calls_per_run"])
    if budgets.get("max_mutations_per_run") is not None:
        max_mutations = min(int(budgets["max_mutations_per_run"]), _PARAM_CAPS["budgets"]["max_mutations_per_run"])

    return ResolvedMemoryPolicy(
        workspace_id=row.workspace_id,
        agent_id=base.agent_id,
        tool_subset=tool_subset,
        sharing_ceiling=sharing_ceiling,
        ttl_days=ttl_days,
        capacity=capacity,
        eviction_budget_per_sweep=eviction_budget,
        protection_window_days=protection_days,
        promotion_enabled=promotion_enabled,
        promotion_score_threshold=promotion_score,
        promotion_min_hits=promotion_min_hits,
        promotion_min_age_days=promotion_min_age,
        flush_enabled=flush_enabled,
        max_llm_calls_per_run=max_llm_calls,
        max_mutations_per_run=max_mutations,
    )


def invalidate_memory_policy_cache(
    workspace_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
) -> None:
    """Drop cached resolutions (all, or one scope's). Called on writes."""
    if workspace_id is None:
        _cache.clear()
        return
    agent_key = agent_id or ZERO_UUID
    for key in [k for k in _cache if k[0] == workspace_id]:
        if agent_id is None or key[1] == agent_key or key[1] == ZERO_UUID:
            del _cache[key]


async def resolve_policy(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> ResolvedMemoryPolicy:
    """Resolve the effective policy for a scope (cached).

    Chain: platform defaults → workspace-level row → agent-level row.
    Disabled rows are treated as absent. An empty table yields exactly the
    platform defaults.
    """
    agent_key = agent_id or ZERO_UUID
    now = time.monotonic()
    cached = _cache.get((workspace_id, agent_key))
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    resolved = _platform_defaults(agent_id)
    rows = (
        (
            await db.execute(
                select(MemoryPolicyModel)
                .where(
                    MemoryPolicyModel.workspace_id == workspace_id,
                    MemoryPolicyModel.agent_id.in_([ZERO_UUID, agent_key]),
                    MemoryPolicyModel.enabled.is_(True),
                    ~MemoryPolicyModel.deleted,
                )
                .order_by(MemoryPolicyModel.agent_id.asc())  # workspace (zero) row first
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        resolved = _apply_row(resolved, row)

    _cache[(workspace_id, agent_key)] = (now, resolved)
    return resolved


async def narrowed_tool_names(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    tool_names: list[str],
) -> list[str]:
    """Apply the resolved policy's tool subset to an agent's tool list.

    Non-memory tools pass through untouched; memory tools are kept only
    when the resolved policy grants them (no policy → unchanged). Used at
    the agent tool-resolution points so per-agent narrowing takes effect
    at execution time, on top of the platform flag gate at seeding.
    """
    try:
        from hecate_memory.memory.tools_backend import get_memory_tool_names

        memory_tools = get_memory_tool_names()
    except ImportError:
        # Without the memory package no memory tool can be seeded anyway —
        # nothing to narrow.
        return tool_names
    if not any(name in memory_tools for name in tool_names):
        return tool_names
    policy = await resolve_policy(db, workspace_id, agent_id)
    if policy.tool_subset is None:
        return tool_names
    return [name for name in tool_names if name not in memory_tools or name in policy.tool_subset]


def _validate_param_shape(params: dict[str, Any]) -> None:
    """Type/cap checks on the params payload (numeric fields, known keys)."""
    for group, members in _PARAM_CAPS.items():
        value = params.get(group)
        if value is None:
            continue
        if not isinstance(value, dict):
            raise PolicyValidationError(f"policy params.{group} must be an object")
        for key, cap in members.items():
            if key not in value or value[key] is None:
                continue
            raw = value[key]
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise PolicyValidationError(f"policy params.{group}.{key} must be an integer")
            if raw < 0:
                raise PolicyValidationError(f"policy params.{group}.{key} must be >= 0")
            if raw > cap:
                raise PolicyValidationError(f"policy params.{group}.{key}={raw} exceeds platform hard cap {cap}")
    flush = params.get("flush")
    if flush is not None:
        if not isinstance(flush, dict):
            raise PolicyValidationError("policy params.flush must be an object")
        if "enabled" in flush and not isinstance(flush["enabled"], bool):
            raise PolicyValidationError("policy params.flush.enabled must be a boolean")
    promotion = params.get("promotion")
    if promotion is not None and promotion.get("score_threshold") is not None:
        raw = promotion["score_threshold"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PolicyValidationError("policy params.promotion.score_threshold must be a number")
        if not 0.0 <= float(raw) <= 1.0:
            raise PolicyValidationError("policy params.promotion.score_threshold must be within [0, 1]")
    known_groups = set(_PARAM_CAPS) | {"flush"}
    unknown = set(params) - known_groups
    if unknown:
        raise PolicyValidationError(f"unknown policy params groups: {sorted(unknown)}")


async def _parent_payload(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> tuple[MemoryPolicyModel | None, MemoryPolicyModel | None]:
    """Fetch the workspace-level and agent-level rows for validation."""
    rows = (
        (
            await db.execute(
                select(MemoryPolicyModel).where(
                    MemoryPolicyModel.workspace_id == workspace_id,
                    ~MemoryPolicyModel.deleted,
                )
            )
        )
        .scalars()
        .all()
    )
    parent = next((r for r in rows if r.agent_id == ZERO_UUID), None)
    own = None if agent_id is None else next((r for r in rows if r.agent_id == agent_id), None)
    return parent, own


async def validate_policy_payload(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    *,
    tool_subset: list[str] | None,
    sharing_ceiling: str | None,
    params: dict[str, Any] | None,
) -> None:
    """Validate a payload against known names, hard caps and narrowing rules.

    Raises :class:`PolicyValidationError` with a readable message on any
    violation. Narrowing is checked against the parent-level row (the
    workspace row for agent payloads); the workspace payload has no parent
    constraints beyond the platform surface.
    """
    from hecate_memory.memory.tools_backend import get_memory_tool_names

    if tool_subset is not None:
        known = get_memory_tool_names()
        unknown = sorted(set(tool_subset) - known)
        if unknown:
            raise PolicyValidationError(f"unknown memory tool names: {unknown}")

    if params is not None:
        _validate_param_shape(params)

    if agent_id is not None:
        parent, _own = await _parent_payload(db, workspace_id, None)
        if parent is not None and parent.enabled:
            if tool_subset is not None and parent.tool_subset is not None:
                wider = sorted(set(tool_subset) - set(parent.tool_subset))
                if wider:
                    raise PolicyValidationError(f"agent tool subset widens the workspace subset: {wider}")
            if (
                sharing_ceiling is not None
                and parent.sharing_ceiling is not None
                and SHARING_WIDTH[sharing_ceiling] > SHARING_WIDTH[parent.sharing_ceiling]
            ):
                raise PolicyValidationError(
                    f"agent sharing ceiling '{sharing_ceiling}' widens the workspace ceiling '{parent.sharing_ceiling}'"
                )


async def upsert_policy(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    *,
    enabled: bool = True,
    tool_subset: list[str] | None = None,
    sharing_ceiling: str | None = None,
    params: dict[str, Any] | None = None,
    actor_id: uuid.UUID | None = None,
) -> MemoryPolicyModel:
    """Create or update one scope's policy row (validated + audited).

    ``agent_id=None`` targets the workspace-level row (zero-UUID sentinel).
    Raises :class:`PolicyValidationError` on any rule violation; the caller
    (REST layer) maps that to a client error.
    """
    await validate_policy_payload(
        db,
        workspace_id,
        agent_id,
        tool_subset=tool_subset,
        sharing_ceiling=sharing_ceiling,
        params=params,
    )

    scope_agent = agent_id or ZERO_UUID
    row = (
        await db.execute(
            select(MemoryPolicyModel).where(
                MemoryPolicyModel.workspace_id == workspace_id,
                MemoryPolicyModel.agent_id == scope_agent,
                ~MemoryPolicyModel.deleted,
            )
        )
    ).scalar_one_or_none()
    action = "update"
    if row is None:
        row = MemoryPolicyModel(workspace_id=workspace_id, agent_id=scope_agent)
        db.add(row)
        action = "create"
    row.enabled = enabled
    row.tool_subset = tool_subset
    row.sharing_ceiling = sharing_ceiling
    row.params = params
    await db.flush()

    db.add(
        MemoryEditLogModel(
            workspace_id=workspace_id,
            agent_id=actor_id or scope_agent,
            tool_name="policy_upsert",
            target_type="memory_policy",
            target_id=row.id,
            after_summary=f"{action} enabled={enabled} tools={tool_subset} "
            f"ceiling={sharing_ceiling} params_keys={sorted((params or {}).keys())}",
        )
    )
    await db.flush()

    invalidate_memory_policy_cache(workspace_id, agent_id)
    logger.info("Memory policy %s for ws=%s agent=%s", action, workspace_id, agent_id)
    return row


async def delete_policy(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    *,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Remove one scope's policy row (hard delete; audit keeps the trail).

    Returns True when a row existed. The scope falls back up the chain on
    the next resolution.
    """
    scope_agent = agent_id or ZERO_UUID
    row = (
        await db.execute(
            select(MemoryPolicyModel).where(
                MemoryPolicyModel.workspace_id == workspace_id,
                MemoryPolicyModel.agent_id == scope_agent,
                ~MemoryPolicyModel.deleted,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.flush()

    db.add(
        MemoryEditLogModel(
            workspace_id=workspace_id,
            agent_id=actor_id or scope_agent,
            tool_name="policy_delete",
            target_type="memory_policy",
            target_id=row.id,
            before_summary=f"deleted scope agent={scope_agent}",
        )
    )
    await db.flush()

    invalidate_memory_policy_cache(workspace_id, agent_id)
    logger.info("Memory policy deleted for ws=%s agent=%s", workspace_id, agent_id)
    return True
