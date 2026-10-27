"""Trace read-access queries for the trace REST surface.

Private query module for ``/api/traces``: workspace authorization of trace
roots through live Session/Agent row association, and safe resolution of
the OTel span tree before any content is loaded.

Authorization model:

- A root trace row is visible only through *live* row associations into
  the caller's workspace: a session link requires the Session row to be
  live in the workspace and its own linked Agent to be live in the same
  workspace; a trace-level agent link requires that Agent row to be live
  in the workspace. Every association present on the row must be valid —
  one legitimate link never masks a foreign, dead, or contradictory one.
  Rows with no association at all are orphans and stay invisible.
- Child spans normally carry no session/agent attributes (the OTel
  producer fills them on the request-scoped spans). They inherit the
  root's authorization only through a verified tree structure: children
  link to parents via the producer's ``metadata.otel.span_id`` marker
  (``parent_id`` stores the zero-padded UUID of the parent's OTel span
  id — never assumed to be a row primary key; a row-PK link is accepted
  as a secondary form). Ambiguous roots, dangling references, cycles,
  disconnected rows sharing the trace_id, and spans whose own
  associations contradict the caller's workspace make a detail unsafely
  attributable — the API refuses the whole trace with a uniform 404
  instead of guessing or mixing content.
"""

from __future__ import annotations

import re
import uuid
from collections import deque
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, String, and_, cast, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.session import SessionModel
from hecate.models.trace import TraceModel

TRACE_NOT_FOUND = "Trace not found"


def authorized_trace_condition(workspace_id: uuid.UUID) -> ColumnElement[bool]:
    """Row-level visibility predicate for trace association.

    Mirrors the Session/Agent authorization used by the read boundary:
    the session link is only trustworthy when the session's own agent
    binding is live in the same workspace, and a trace-level agent link
    must resolve on its own. Every association present on the row has to
    be valid; an orphan (neither link) matches nothing.
    """

    def _live_session_ids() -> Any:
        return (
            select(SessionModel.id)
            .join(AgentModel, AgentModel.id == SessionModel.agent_id)
            .where(
                SessionModel.workspace_id == workspace_id,
                ~SessionModel.deleted,
                AgentModel.workspace_id == workspace_id,
                ~AgentModel.deleted,
            )
        )

    def _live_agent_ids() -> Any:
        return select(AgentModel.id).where(
            AgentModel.workspace_id == workspace_id,
            ~AgentModel.deleted,
        )

    session_ok = TraceModel.session_id.in_(_live_session_ids())
    agent_ok = TraceModel.agent_id.in_(_live_agent_ids())
    return or_(
        and_(session_ok, or_(TraceModel.agent_id.is_(None), agent_ok)),
        and_(TraceModel.session_id.is_(None), agent_ok),
    )


class _ProbeRow:
    """Minimal per-row facts needed to authorize before loading content."""

    __slots__ = ("id", "parent_id", "session_id", "agent_id", "span_id_hex")

    def __init__(
        self,
        row_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        session_id: uuid.UUID | None,
        agent_id: uuid.UUID | None,
        span_id_hex: str | None,
    ) -> None:
        self.id = row_id
        self.parent_id = parent_id
        self.session_id = session_id
        self.agent_id = agent_id
        self.span_id_hex = span_id_hex

    @property
    def has_association(self) -> bool:
        return self.session_id is not None or self.agent_id is not None


class AuthorizedTrace:
    """The verified shape of one trace, ready for a content load."""

    __slots__ = ("trace_id", "workspace_id", "root_id", "span_ids", "rows")

    def __init__(
        self, trace_id: uuid.UUID, workspace_id: uuid.UUID, root: _ProbeRow, spans: Sequence[_ProbeRow]
    ) -> None:
        self.trace_id = trace_id
        self.workspace_id = workspace_id
        self.root_id = root.id
        self.span_ids = tuple(row.id for row in spans)
        self.rows = (root, *spans)


class TraceReadService:
    """Workspace-scoped trace read queries behind the REST boundary."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_traces(
        self,
        workspace_id: uuid.UUID,
        *,
        session_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[TraceModel]:
        """List authorized root traces, paginated after authorization.

        Client-supplied filters only narrow the authorized population —
        they never grant access to rows outside the workspace scope.
        """
        query = (
            select(TraceModel)
            .where(
                TraceModel.parent_id.is_(None),
                ~TraceModel.deleted,
                authorized_trace_condition(workspace_id),
            )
            .order_by(TraceModel.start_time.desc(), TraceModel.id.desc())
            .limit(limit)
            .offset(offset)
            .execution_options(populate_existing=True)
        )
        if session_id is not None:
            query = query.where(TraceModel.session_id == session_id)
        if agent_id is not None:
            query = query.where(TraceModel.agent_id == agent_id)
        if start_time is not None:
            query = query.where(TraceModel.start_time >= start_time)
        if end_time is not None:
            query = query.where(TraceModel.start_time <= end_time)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def authorize_trace(
        self,
        trace_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> AuthorizedTrace:
        """Settle visibility with a minimal probe, loading no content.

        Resolves the root, verifies the span tree structure, and checks
        that every explicitly-associated row in the tree satisfies the
        workspace authorization on its own — the root's legitimate link
        never launders a foreign or dead association deeper in the tree.

        Returns:
            The verified root and ordered span row ids.

        Raises:
            LookupError: The trace is missing, foreign, soft-deleted,
                orphaned, structurally unsafe (ambiguous root, dangling
                reference, cycle, disconnected rows), or carries an
                association that contradicts the caller's workspace —
                uniformly ``TRACE_NOT_FOUND``.
        """
        probe_rows = await self._probe_rows(trace_id)
        root = self._resolve_root(probe_rows)
        ordered = self._resolve_span_order(probe_rows, root)
        checked = [row.id for row in (root, *ordered) if row.has_association]
        if not root.has_association or not await self._rows_authorized(checked, workspace_id):
            raise LookupError(TRACE_NOT_FOUND)
        return AuthorizedTrace(trace_id, workspace_id, root, ordered)

    async def load_trace_detail(self, authorized: AuthorizedTrace) -> tuple[TraceModel, list[TraceModel]]:
        """Load full content rows for an already-authorized trace.

        The content statement checks the complete probed shape and live
        associations in its own database snapshot. A changed row or a new
        fragment refuses the entire load, including sparse children that
        depend on the root's authorization. ORM refresh alone cannot
        protect against a binding changing after the initial probe.
        """
        expected = [
            (
                row.id,
                row.parent_id.hex if row.parent_id is not None else "",
                row.session_id.hex if row.session_id is not None else "",
                row.agent_id.hex if row.agent_id is not None else "",
                f'"{row.span_id_hex}"' if row.span_id_hex is not None else "null",
            )
            for row in authorized.rows
        ]

        def uuid_text(value: Any) -> Any:
            # PostgreSQL renders UUIDs with hyphens; SQLite stores hex.
            # Empty text represents NULL without colliding with zero UUID.
            return func.coalesce(func.replace(cast(value, String), "-", ""), "")

        matching = (
            select(TraceModel.id)
            .where(
                TraceModel.trace_id == authorized.trace_id,
                ~TraceModel.deleted,
                tuple_(
                    TraceModel.id,
                    uuid_text(TraceModel.parent_id),
                    uuid_text(TraceModel.session_id),
                    uuid_text(TraceModel.agent_id),
                    # Preserve JSON scalar type: a numeric marker must
                    # never alias a valid string after PostgreSQL ->>.
                    func.coalesce(cast(TraceModel.metadata_["otel.span_id"], String), "null"),
                ).in_(expected),
                or_(
                    authorized_trace_condition(authorized.workspace_id),
                    and_(
                        TraceModel.parent_id.is_not(None),
                        TraceModel.session_id.is_(None),
                        TraceModel.agent_id.is_(None),
                    ),
                ),
            )
            .cte("authorized_trace_rows")
        )
        matched_count = select(func.count()).select_from(matching).scalar_subquery()
        live_count = (
            select(func.count(TraceModel.id))
            .where(TraceModel.trace_id == authorized.trace_id, ~TraceModel.deleted)
            .scalar_subquery()
        )
        stmt = (
            select(TraceModel)
            .join(matching, matching.c.id == TraceModel.id)
            .where(matched_count == len(expected), live_count == len(expected))
            .order_by(TraceModel.start_time, TraceModel.id)
            .execution_options(populate_existing=True)
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        if len(rows) != len(expected):
            raise LookupError(TRACE_NOT_FOUND)
        root = next(row for row in rows if row.id == authorized.root_id)
        return root, [row for row in rows if row.id != authorized.root_id]

    # --- internals ----------------------------------------------------------

    async def _probe_rows(self, trace_id: uuid.UUID) -> list[_ProbeRow]:
        """Load ownership/structure facts without trace content."""
        stmt = select(
            TraceModel.id,
            TraceModel.parent_id,
            TraceModel.session_id,
            TraceModel.agent_id,
            TraceModel.metadata_["otel.span_id"].label("span_id_hex"),
        ).where(TraceModel.trace_id == trace_id, ~TraceModel.deleted)
        rows = (await self.db.execute(stmt)).all()
        return [_ProbeRow(row.id, row.parent_id, row.session_id, row.agent_id, row.span_id_hex) for row in rows]

    @staticmethod
    def _resolve_root(rows: Sequence[_ProbeRow]) -> _ProbeRow:
        """The single parentless row, or refuse ambiguous/missing roots."""
        roots = [row for row in rows if row.parent_id is None]
        if len(roots) != 1:
            # Missing root, or several candidate roots under one trace_id:
            # no arbitrary first-row fallback — the detail is not
            # attributable.
            raise LookupError(TRACE_NOT_FOUND)
        return roots[0]

    @staticmethod
    def _resolve_span_order(rows: Sequence[_ProbeRow], root: _ProbeRow) -> list[_ProbeRow]:
        """Order the tree below the root; refuse unsafe structures.

        Children link to parents via the producer's OTel span-id marker
        (``parent_id`` is the zero-padded UUID of the parent's
        ``metadata.otel.span_id``); a direct row-PK link is accepted as a
        secondary form. Rows that cannot be reached from the verified
        root — including cycle members and disconnected fragments — make
        the whole detail unsafely attributable.
        """
        by_reference = {row.id: row for row in rows}
        for row in rows:
            marker = row.span_id_hex
            if marker is None:
                continue
            if not isinstance(marker, str) or re.fullmatch(r"[0-9a-fA-F]{16}", marker) is None or int(marker, 16) == 0:
                raise LookupError(TRACE_NOT_FOUND)
            reference = uuid.UUID(int=int(marker, 16))
            previous = by_reference.get(reference)
            if previous is not None and previous.id != row.id:
                # Duplicate OTel ids and OTel/row-PK collisions cannot
                # establish a unique parent, regardless of result order.
                raise LookupError(TRACE_NOT_FOUND)
            by_reference[reference] = row

        children: dict[uuid.UUID, list[_ProbeRow]] = {}
        for row in rows:
            if row.parent_id is None:
                continue
            parent = by_reference.get(row.parent_id)
            if parent is None or parent.id == row.id:
                # Dangling parent reference or self-loop: unreachable.
                raise LookupError(TRACE_NOT_FOUND)
            children.setdefault(parent.id, []).append(row)

        ordered: list[_ProbeRow] = []
        queue: deque[_ProbeRow] = deque([root])
        seen: set[uuid.UUID] = {root.id}
        while queue:
            current = queue.popleft()
            for child in children.get(current.id, []):
                if child.id in seen:
                    # Cycle reachable from the root — not a tree.
                    raise LookupError(TRACE_NOT_FOUND)
                seen.add(child.id)
                ordered.append(child)
                queue.append(child)
        if len(seen) != len(rows):
            # Disconnected rows sharing this trace_id must not be mixed in.
            raise LookupError(TRACE_NOT_FOUND)
        return ordered

    async def _rows_authorized(self, row_ids: list[uuid.UUID], workspace_id: uuid.UUID) -> bool:
        """Whether every listed row satisfies the association predicate."""
        if not row_ids:
            return True
        stmt = select(func.count(TraceModel.id)).where(
            TraceModel.id.in_(row_ids),
            ~TraceModel.deleted,
            authorized_trace_condition(workspace_id),
        )
        return (await self.db.execute(stmt)).scalar_one() == len(set(row_ids))
