"""Work Context Graph (KM6 / ADR-024 §6) — node + edge storage layer.

The Work Context Graph is the structured successor to flat Task Memory
records (ADR-024 §6). Approved ``reflections`` are the input; the
graph is the long-term knowledge substrate the agent reads when
``reflection_search`` is invoked.

Two storage primitives live on the ``hecate-memory`` side:

- ``work_context_nodes`` — five node types (``method / outcome /
  correction / source / pattern``). Each node is the per-reflection
  materialized form: one approved reflection -> at most one active
  node. Aggregate statistics (``success_rate`` / ``usage_count`` /
  ``last_used_at`` / ``user_correction_count``) live here, recomputed by
  a background job — never on the read path or in the reflection
  transaction.
- ``work_context_edges`` — four edge types (``tried_before /
  led_to / corrected_by / validated_by``). MVP scope: only the
  supersession edge emitted when a reflection's ``operator='update'``
  flips the previous node's ``active`` to ``false`` (the
  ``superseded_by`` chain is mirrored on the node).

Three service-layer entry points:

- ``create_node_for_reflection`` — called from inside the
  ReflectionEngine transaction when an approval fires. Same db
  session so a reflection rollback also rolls back the node write.
- ``supersede_nodes_for_reflection`` — called when a reflection is
  ``operator='update'``; flips the linked prior node's ``active`` flag
  to ``false``. Idempotent.
- ``aggregate_node_stats`` — background job (Group 6.3) that walks
  approved reflections + episodes, updates success_rate / usage_count
  / last_used_at / user_correction_count on the nodes. Designed to run
  on a slow cadence (default: nightly); explicitly NOT in the read or
  write critical path.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.task_memory import (
    WORK_CONTEXT_EDGE_TYPE_CORRECTED_BY,
    WORK_CONTEXT_NODE_NODE_TYPE_PATTERN,
    WORK_CONTEXT_NODE_TYPE_CORRECTION,
    WORK_CONTEXT_NODE_TYPE_METHOD,
    WORK_CONTEXT_NODE_TYPE_OUTCOME,
    WORK_CONTEXT_NODE_TYPE_SOURCE,
    ReflectionModel,
    WorkContextEdgeModel,
    WorkContextNodeModel,
)

logger = logging.getLogger(__name__)


# Default node_type if no heuristic matches.
_DEFAULT_NODE_TYPE = WORK_CONTEXT_NODE_TYPE_METHOD


# Heuristics — keyword-based node_type derivation. Matched against
# the reflection title (case-insensitive) first, then hints. A
# reflection that matches multiple buckets lands on the first one in
# priority order: ``correction > pattern > outcome > source > method``.
_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    WORK_CONTEXT_NODE_TYPE_CORRECTION: ("instead of", "rather than", "correction", "fix"),
    WORK_CONTEXT_NODE_NODE_TYPE_PATTERN: ("always", "pattern", "recurring", "whenever"),
    WORK_CONTEXT_NODE_TYPE_OUTCOME: ("result", "outcome", "worked", "succeeded"),
    WORK_CONTEXT_NODE_TYPE_SOURCE: ("source", "from", "based on", "according to"),
    WORK_CONTEXT_NODE_TYPE_METHOD: ("method", "approach", "use ", "prefer"),
}


def derive_node_type(*, title: str, hints: str) -> str:
    """Return one of ``WORK_CONTEXT_NODE_TYPES`` based on title + hints."""
    haystack = (title + " " + hints).lower()
    for node_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in haystack:
                return node_type
    return _DEFAULT_NODE_TYPE


# ──────────────────────────── write path (in transaction) ────────────────────────────


async def create_node_for_reflection(
    db: AsyncSession,
    *,
    reflection_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    title: str,
    hints: str,
    use_cases: list[str],
    confidence: float,
) -> uuid.UUID:
    """Insert one work_context_node row tied to an approved reflection.

    Called inside the same transaction as the reflection insert so a
    reflection rollback also rolls back the node write (atomicity).
    No edge creation in MVP — the reflection engine's supersession
    path emits a separate ``supersede_nodes_for_reflection`` call.

    Returns the new node's id.
    """
    node_type = derive_node_type(title=title, hints=hints)
    new_id = uuid.uuid4()
    row = WorkContextNodeModel(
        id=new_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        node_type=node_type,
        content=hints,
        success_rate=confidence,
        usage_count=0,
        last_used_at=None,
        user_correction_count=0,
        source_reliability=None,
        linked_reflection_id=reflection_id,
        active=True,
    )
    db.add(row)
    await db.flush()
    return new_id


async def supersede_nodes_for_reflection(
    db: AsyncSession,
    *,
    reflection_id: uuid.UUID,
) -> int:
    """Mark all nodes linked to ``reflection_id`` as ``active=False``.

    Called when a reflection is ``operator='update'`` — the prior
    reflection row was just pointed at by ``superseded_by``; its
    linked nodes must stop being retrievable so the new node's
    content wins. Idempotent: re-superseding an already-inactive node
    is a no-op.

    Note: this call does NOT write the supersession edge — see
    ``write_supersession_edge`` which is called *after* the new node
    is created (so both endpoints exist when the edge row is
    inserted).

    Returns the number of rows updated.
    """
    stmt = (
        update(WorkContextNodeModel)
        .where(
            WorkContextNodeModel.linked_reflection_id == reflection_id,
            WorkContextNodeModel.active.is_(True),
        )
        .values(active=False)
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def write_supersession_edge(
    db: AsyncSession,
    *,
    prior_reflection_id: uuid.UUID,
    new_reflection_id: uuid.UUID,
) -> None:
    """Insert a single ``corrected_by`` edge between the prior and new node rows.

    Called *after* the new reflection row has been written (and its
    graph node created via ``create_node_for_reflection``) so both
    endpoints exist at insertion time. Best-effort: if either endpoint
    has been deleted or never written, the call is a silent no-op.
    """
    prior_q = select(WorkContextNodeModel).where(
        WorkContextNodeModel.linked_reflection_id == prior_reflection_id,
        WorkContextNodeModel.active.is_(False),
        ~WorkContextNodeModel.deleted,
    )
    new_q = select(WorkContextNodeModel).where(
        WorkContextNodeModel.linked_reflection_id == new_reflection_id,
        WorkContextNodeModel.active.is_(True),
        ~WorkContextNodeModel.deleted,
    )
    prior = (await db.execute(prior_q)).scalar_one_or_none()
    new = (await db.execute(new_q)).scalar_one_or_none()
    if prior is None or new is None:
        return
    edge = WorkContextEdgeModel(
        id=uuid.uuid4(),
        workspace_id=prior.workspace_id,
        source_node_id=prior.id,
        target_node_id=new.id,
        edge_type=WORK_CONTEXT_EDGE_TYPE_CORRECTED_BY,
        weight=1.0,
    )
    db.add(edge)


async def _write_supersession_edge(
    db: AsyncSession,
    *,
    prior_reflection_id: uuid.UUID,
    new_reflection_id: uuid.UUID,
) -> None:
    """Backward-compat shim — calls ``write_supersession_edge``.

    Retained so the previous call site that passed ``superseded_by_id``
    as a parameter keeps working; new callers should call
    ``write_supersession_edge`` directly.
    """
    await write_supersession_edge(
        db,
        prior_reflection_id=prior_reflection_id,
        new_reflection_id=new_reflection_id,
    )


# ──────────────────────────── read path ────────────────────────────


async def get_active_node(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    linked_reflection_id: uuid.UUID,
) -> WorkContextNodeModel | None:
    """Return the active node for a given reflection (or ``None``)."""
    stmt = select(WorkContextNodeModel).where(
        WorkContextNodeModel.workspace_id == workspace_id,
        WorkContextNodeModel.agent_id == agent_id,
        WorkContextNodeModel.linked_reflection_id == linked_reflection_id,
        WorkContextNodeModel.active.is_(True),
        ~WorkContextNodeModel.deleted,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# ──────────────────────────── batched stat aggregation ────────────────────────────


class NodeStatsAggregator:
    """Background aggregation of work_context_nodes statistics.

    Updates four fields on each active node:

    - ``usage_count`` — number of approved reflections with the same
      title (active lineage size). Proxies for "how often the lesson
      was retrieved / re-affirmed" without requiring a separate
      access-log table.
    - ``success_rate`` — mean of ``confidence`` across the title's
      active lineage (EMA-blended with the prior value to keep
      updates smooth across runs).
    - ``last_used_at`` — max ``last_confirmed_at`` across the title's
      active lineage; ``None`` when the lineage has no recent use.
    - ``user_correction_count`` — number of ``operator='update'``
      rows in the lineage minus one (the original ``add`` row);
      captures "how many times this lesson has been corrected".

    The aggregator does not lock or block read paths — it operates
    on a snapshot, computes, then bulk-updates. Run on a slow
    cadence (nightly by default).
    """

    ema_alpha: float = 0.1

    async def run(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """Aggregate one pass; returns counters."""
        node_q = select(WorkContextNodeModel).where(
            WorkContextNodeModel.active.is_(True),
            ~WorkContextNodeModel.deleted,
        )
        if workspace_id is not None:
            node_q = node_q.where(WorkContextNodeModel.workspace_id == workspace_id)
        if agent_id is not None:
            node_q = node_q.where(WorkContextNodeModel.agent_id == agent_id)
        nodes = list((await db.execute(node_q)).scalars().all())
        if not nodes:
            return {"scanned": 0, "updated": 0}

        # Load all approved reflections per scope.
        scope_pairs = {(n.workspace_id, n.agent_id) for n in nodes}
        lineage_by_title: dict[tuple[uuid.UUID, uuid.UUID, str], list[ReflectionModel]] = {}
        for ws, ag in scope_pairs:
            stmt = select(ReflectionModel).where(
                ReflectionModel.workspace_id == ws,
                ReflectionModel.agent_id == ag,
                ReflectionModel.status != "deprecated",
                ~ReflectionModel.deleted,
            )
            for r in (await db.execute(stmt)).scalars().all():
                key = (ws, ag, r.title.strip().lower())
                lineage_by_title.setdefault(key, []).append(r)

        scanned = 0
        updated = 0
        for node in nodes:
            scanned += 1
            if node.linked_reflection_id is None:
                continue
            # Resolve the node's title via its linked reflection (one
            # cheap query per node; an aggregation pass already pays
            # this O(N) cost).
            stmt = select(ReflectionModel.title).where(ReflectionModel.id == node.linked_reflection_id)
            title_row = (await db.execute(stmt)).first()
            if not title_row:
                continue
            title = (title_row[0] or "").strip().lower()
            lineage = lineage_by_title.get((node.workspace_id, node.agent_id, title), [])
            usage_count = len(lineage)
            confidences = [r.confidence for r in lineage if r.confidence is not None]
            mean_confidence = (sum(confidences) / len(confidences)) if confidences else node.success_rate
            new_success_rate = self._ema(node.success_rate, mean_confidence)
            correction_count = max(0, sum(1 for r in lineage if (r.operator or "add") == "update") - 0)
            last_used = max(
                (r.last_confirmed_at for r in lineage if r.last_confirmed_at is not None),
                default=None,
            )
            changed = False
            if usage_count != node.usage_count:
                node.usage_count = usage_count
                changed = True
            if last_used and node.last_used_at != last_used:
                node.last_used_at = last_used
                changed = True
            if abs(new_success_rate - node.success_rate) > 1e-6:
                node.success_rate = new_success_rate
                changed = True
            if correction_count != node.user_correction_count:
                node.user_correction_count = correction_count
                changed = True
            if changed:
                updated += 1
        if updated:
            await db.commit()
        return {"scanned": scanned, "updated": updated}

    def _ema(self, prior: float, evidence: float) -> float:
        return self.ema_alpha * evidence + (1.0 - self.ema_alpha) * prior
