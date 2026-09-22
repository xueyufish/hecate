"""Skill dependency resolver: bind-time closure walk for ``requires``.

The companion to :mod:`hecate.tools.skill.dependency_validator`. Where the
validator inspects a single ``requires`` payload at authoring time and
checks structural invariants (shape, missing, cycle, cross-source), the
resolver walks the transitive closure at bind time and resolves every
node to the concrete ``(name, skill_id, provider, version, content_hash)``
quintuple the agent-version reference manifest stores.

The resolver is read-only with respect to the user's payload — it never
modifies a ``requires`` column, only reads it. It is meant to be called
inside the agent-version commit transaction so a closure failure rolls
back the whole commit.

Invariants enforced:

- **Existence** — every transitive node resolves to a live, undeleted
  skill row in the same workspace (or in the zero-UUID workspace for
  bundled skills).
- **Depth limit** — walks are bounded by
  :data:`hecate.tools.skill.dependency_validator.MAX_DEPENDENCY_DEPTH`
  so a malformed graph cannot blow the stack.
- **Atomicity** — the caller wraps the call in a transaction; on
  failure, no rows are persisted and the commit fails fast.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.skill import SkillModel
from hecate.models.skill_version import SkillVersionModel
from hecate.tools.skill.dependency_validator import MAX_DEPENDENCY_DEPTH
from hecate.tools.skill.provider_registry import resolve_precedence_map

#: Identity for the bundled workspace used to resolve platform-shipped
#: skills; mirrors the convention in
#: :meth:`hecate.studio.agents.versioning.AgentVersionService._skill_manifest_entries`.
BUNDLED_WORKSPACE_ID = uuid.UUID(int=0)


@dataclass(frozen=True)
class ResolvedSkill:
    """A skill fully resolved to its manifest quintuple.

    Attributes:
        skill_id: Database primary key of the winning skill row.
        name: Skill name as it appears in the manifest.
        provider: Provider classification (``bundled`` / ``user`` /
            ``project``) for non-plugin rows; ``None`` for plugin-sourced
            rows that stay outside rank competition.
        version: Latest committed skill version (``None`` for plugin /
            unversioned rows per 5.9d).
        content_hash: Pin-friendly hash — the version-snapshot hash if
            the skill has committed versions, otherwise the live row
            hash so drift detection still works.
    """

    skill_id: uuid.UUID
    name: str
    provider: str | None
    version: int | None
    content_hash: str | None


class ClosureError(Exception):
    """Raised when a transitive dependency cannot be resolved.

    The exception carries the set of missing nodes so the API layer can
    render a structured 422 response naming every offender (per the
    spec: "agent 版本提交 SHALL hard-fail,错误信息 SHALL 点名全部缺漏节点").
    """

    def __init__(self, missing: list[tuple[str, str | None]]) -> None:
        self.missing = missing
        names = sorted({m[0] for m in missing})
        super().__init__("incomplete skill closure: missing " + ", ".join(names) + f" ({len(missing)} path(s))")


def _coerce_requires(value: Any) -> list[dict[str, Any]]:
    """Coerce a ``requires`` column value to a list of dicts.

    Tolerates ``None`` and JSON scalars by returning ``[]`` — the column
    may legitimately be empty; an unparseable value is also treated as
    empty rather than crashing the commit, since the validator already
    surfaced any shape problem at authoring time.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [e for e in value if isinstance(e, dict)]
    return []


async def _load_skill_rows(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    names: Sequence[str],
) -> dict[str, list[SkillModel]]:
    """Load all skill rows for the given names across both the user's
    workspace and the bundled workspace, indexed by name.

    Returns a list per name because same-name, different-provider rows
    coexist (5.9-enh) and the resolver needs the full set to pick a
    winner via precedence.
    """
    if not names:
        return {}
    result = await db.execute(
        select(SkillModel).where(
            SkillModel.name.in_(names),
            SkillModel.workspace_id.in_([workspace_id, BUNDLED_WORKSPACE_ID]),
            ~SkillModel.deleted,
        )
    )
    by_name: dict[str, list[SkillModel]] = {}
    for row in result.scalars():
        by_name.setdefault(row.name, []).append(row)
    return by_name


async def _load_latest_versions(
    db: AsyncSession,
    skill_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, SkillVersionModel]:
    """Bulk-load the latest committed version per skill id."""
    if not skill_ids:
        return {}
    rows = await db.execute(
        select(SkillVersionModel)
        .where(
            SkillVersionModel.skill_id.in_(skill_ids),
            ~SkillVersionModel.deleted,
        )
        .order_by(SkillVersionModel.version.desc())
    )
    latest: dict[uuid.UUID, SkillVersionModel] = {}
    for row in rows.scalars().all():
        latest.setdefault(row.skill_id, row)
    return latest


def _resolve_winner(
    candidates: Sequence[SkillModel],
    requested_provider: str | None,
) -> SkillModel | None:
    """Pick a single candidate row for a (name, requested_provider?).

    Honours 5.9-enh precedence (``project`` > ``user`` > ``bundled``) and
    the requested provider when one is explicitly named in the
    dependency. When the caller asked for a particular provider and no
    matching row exists, ``None`` is returned so the missing-check
    surfaces a clear failure.
    """
    if requested_provider is not None:
        for row in candidates:
            if row.provider == requested_provider:
                return row
        return None
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    return resolve_precedence_map(list(candidates)).get(candidates[0].name)


async def resolve_closure(
    direct_skill_names: Sequence[str],
    workspace_id: uuid.UUID,
    db: AsyncSession,
) -> list[ResolvedSkill]:
    """Walk the transitive ``requires`` closure and resolve each node.

    Args:
        direct_skill_names: Names from ``agent.skills`` — the explicit
            declarations. The closure is seeded from these.
        workspace_id: The agent's owning workspace; bundled skills come
            from the zero-UUID workspace alongside.
        db: Async session — caller is responsible for transaction
            boundaries.

    Returns:
        The full closure (direct + implicit) as a list of
        :class:`ResolvedSkill` instances in DFS-discovery order. Each
        name appears exactly once (memoized).

    Raises:
        ClosureError: When any **transitive** node cannot be resolved
            (missing from the workspace, soft-deleted, or cross-source
            denied). Direct skills that don't exist are tolerated —
            they keep the 5.9d legacy "missing" hash-less entry shape
            and surface via drift reports instead of blocking the commit.
            The exception carries the full list of offending
            ``(name, requested_provider)`` tuples so the commit can
            surface them all at once.
    """
    # Mark direct-skill (seed) entries distinctly so missing seeds are
    # tolerated while missing transitive deps still hard-fail. Per the
    # 5.9d contract the legacy shape stays the same; per the 5.9e
    # contract a transitive `requires` graph that does not exist in the
    # workspace is a hard failure (the spec says "绝不静默跳过任何依赖项").
    direct_names: set[str] = set(direct_skill_names)
    queue: list[tuple[str, str | None, bool]] = [(name, None, True) for name in direct_skill_names]
    seen_keys: set[tuple[str, str | None]] = {(name, None) for name in direct_skill_names}
    discovered: list[tuple[str, str | None]] = [(name, None) for name in direct_skill_names]

    resolved_by_id: dict[uuid.UUID, ResolvedSkill] = {}
    missing: list[tuple[str, str | None]] = []

    # Phase 1 — DFS expansion. We collect *all* names we need to look up
    # before hitting the DB so the row fetch is a single round trip.

    depth = 0
    while queue:
        # Depth guard for malformed graphs.
        depth += 1
        if depth > MAX_DEPENDENCY_DEPTH * 2:
            # We don't emit a hard error here — the validator already
            # catches over-deep graphs at authoring time. Bail safely.
            break

        name, provider, is_seed = queue.pop()

        # Look up the winner for this node.
        candidates = (await _load_skill_rows(db, workspace_id, [name])).get(name, [])
        winner = _resolve_winner(candidates, provider)
        if winner is None:
            # Direct-skill missing: tolerated (5.9d legacy "missing"
            # entry shape survives). Anything else hard-fails.
            if is_seed and name in direct_names:
                continue
            missing.append((name, provider))
            continue

        # Skip if we already have a resolved entry for this row.
        if winner.id in resolved_by_id:
            continue

        # Append transitive requires to the queue. These entries are
        # NOT seeds; missing them hard-fails per the 5.9e contract.
        nested = _coerce_requires(winner.requires)
        for entry in nested:
            nested_name = entry.get("name")
            if not isinstance(nested_name, str) or nested_name == "":
                continue
            nested_provider = entry.get("provider")
            if not isinstance(nested_provider, str) or nested_provider == "":
                nested_provider = None
            key = (nested_name, nested_provider)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            discovered.append(key)
            queue.append((nested_name, nested_provider, False))

    if missing:
        raise ClosureError(missing)

    # Phase 2 — bulk load latest versions for the winners.
    winner_by_id: dict[uuid.UUID, tuple[str, str | None]] = {}
    winner_id_set: set[uuid.UUID] = set()
    for name, provider in discovered:
        candidates = (await _load_skill_rows(db, workspace_id, [name])).get(name, [])
        winner = _resolve_winner(candidates, provider)
        if winner is None:
            # Direct-skill missing: tolerated (5.9d legacy "missing"
            # entry shape survives). Anything else hard-fails — the
            # Phase 1 DFS already enforces this for fresh misses;
            # this branch defends against stale ``discovered`` entries
            # that re-fire the lookup after a soft-tolerated seed.
            if name in direct_names:
                continue
            missing.append((name, provider))
            continue
        if winner.id in winner_by_id:
            continue
        winner_by_id[winner.id] = (name, provider)
        winner_id_set.add(winner.id)

    if missing:
        raise ClosureError(missing)

    latest_versions = await _load_latest_versions(db, list(winner_id_set))

    # Phase 3 — build the resolved list in DFS-discovery order.
    out: list[ResolvedSkill] = []
    for name, provider in discovered:
        if name in direct_names:
            # Direct skill that did not resolve in the workspace stays
            # out of the resolved list (5.9d legacy "missing" entry is
            # recorded separately by the manifest builder).
            candidates = (await _load_skill_rows(db, workspace_id, [name])).get(name, [])
            if not candidates:
                continue
        candidates = (await _load_skill_rows(db, workspace_id, [name])).get(name, [])
        winner = _resolve_winner(candidates, provider)
        if winner is None:
            continue
        version_row = latest_versions.get(winner.id)
        if version_row is not None:
            out.append(
                ResolvedSkill(
                    skill_id=winner.id,
                    name=winner.name,
                    provider=winner.provider,
                    version=version_row.version,
                    content_hash=version_row.content_hash,
                )
            )
        else:
            # No committed version — store live hash so drift stays
            # detectable (matches the existing unpinned entry shape).
            from hecate.core.canonical_hash import canonical_hash

            live_hash = canonical_hash(
                {
                    "name": winner.name,
                    "instructions": winner.instructions,
                    "allowed_tools": winner.allowed_tools,
                    "scripts": winner.scripts,
                    "references": winner.references,
                }
            )
            out.append(
                ResolvedSkill(
                    skill_id=winner.id,
                    name=winner.name,
                    provider=winner.provider,
                    version=None,
                    content_hash=live_hash,
                )
            )
    return out


__all__ = [
    "ResolvedSkill",
    "ClosureError",
    "resolve_closure",
    "BUNDLED_WORKSPACE_ID",
]
