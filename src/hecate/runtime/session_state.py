"""Session state store — unified persistence for per-session execution state.

Provides the ``SessionState`` data model (Pydantic v2 frozen) aggregating
channel state, agent state, event position, and metadata at a checkpoint
boundary, and the ``SessionStateStore`` abstract base class defining the
save/load/list contract.

Design context: see ``docs/design/adr/020-async-execution-distributed-state.md``
(ADR-020) which establishes ``SessionStateStore`` as a prerequisite for
``Horizontal Scaling`` (feature 13.4) and ``Distributed Session State Store``
(feature 13.4a).

Extension point: ``MemoryProvider`` for long-term, cross-session memory
(user profile, semantic facts, episodic memory — openJiuwen L0-L3 pattern)
is intentionally OUT OF SCOPE for this module. ``SessionStateStore`` handles
only short-term per-session state; long-term memory will be a separate
abstraction and implementation delivered as an independent feature.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SessionSummary(BaseModel):
    """Summary information about a persisted session.

    Returned by :meth:`SessionStateStore.list_recent` for cheap listing
    without loading the full state.

    Attributes:
        session_id: Unique identifier of the session within its (org, user).
        org_id: Organization (tenant) the session belongs to.
        user_id: User the session belongs to.
        updated_at: UTC timestamp of the most recent save.
        superstep: Last superstep counter recorded in the session metadata,
            or None if the session has not yet recorded a superstep.
    """

    model_config = ConfigDict(frozen=True)

    session_id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    updated_at: datetime
    superstep: int | None = None


class SessionState(BaseModel):
    """Aggregated per-session execution state at a checkpoint boundary.

    Combines the four state dimensions needed to resume a session on any
    replica: channel values (runtime state), agent state (services state),
    event position (replay cursor), and metadata (checkpoint info).

    Frozen (immutable) so concurrent superstep snapshots cannot mutate each
    other. Use ``model_copy(update=...)`` to produce a modified copy.

    Attributes:
        channel_state: Full snapshot of all PregelRuntime channel values
            (subset of the existing ``CheckpointStore.channel_state``).
        agent_state: Agent working state snapshot — covers the existing
            ``AgentState`` fields (``summary``, ``context``,
            ``permission_context``, ``tool_context``, ``task_context``,
            ``environment_root``, ``metadata``).
        event_position: Current EventStore consumption position; monotonically
            increasing per session; used for event replay when restoring.
        metadata: Checkpoint metadata (e.g., ``superstep``, ``started_at``,
            ``interrupted``, ``interrupt_value``).
    """

    model_config = ConfigDict(frozen=True)

    channel_state: dict[str, Any] = Field(default_factory=dict)
    agent_state: dict[str, Any] = Field(default_factory=dict)
    event_position: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionNotFoundError(ValueError):
    """Raised when a session lookup fails.

    Optional exception for implementations that prefer exception-based error
    handling over the default ``None`` return from ``SessionStateStore.load``.
    The message includes the (org_id, user_id, session_id) triple for
    diagnostics.
    """

    def __init__(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        self.org_id = org_id
        self.user_id = user_id
        self.session_id = session_id
        super().__init__(f"Session not found: org_id={org_id}, user_id={user_id}, session_id={session_id}")


class SessionStateConflictError(Exception):
    """Raised when concurrent writers contend on the same session key.

    Signal that ``acquire_session_lock`` retry budget is exhausted. The
    message includes the ``(org_id, user_id, session_id)`` triple for
    diagnostics. Callers SHOULD fail-fast the requesting chat turn rather
    than silently falling back to a legacy store (which would split state).
    """

    def __init__(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> None:
        self.org_id = org_id
        self.user_id = user_id
        self.session_id = session_id
        super().__init__(f"Session state lock contention for org={org_id} user={user_id} session={session_id}")


# Extension point (reserved): ``MemoryProvider`` for long-term cross-session memory
# (user profiles, semantic facts, episodic memory — openJiuwen L0-L3 pattern).
# ``SessionStateStore`` intentionally handles only short-term per-session state.
# Long-term memory will be delivered as a separate abstraction in a future change.


class SessionStateStore(ABC):
    """Abstract interface for persisting per-session execution state.

    All implementations must use ``SessionState.model_dump_json()`` for
    serialization and ``SessionState.model_validate_json()`` for
    deserialization. Pickle, repr(), and custom binary formats are NOT
    permitted to keep state portable across Python versions and to allow
    inspection/debugging of stored state in any JSON-aware tool.

    Multi-tenant isolation is enforced at the type level via the
    ``(org_id, user_id, session_id)`` triple on every method — implementations
    MUST NOT accept a single ``session_id`` parameter without the org/user
    dimensions.
    """

    @abstractmethod
    async def save(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None:
        """Persist ``state`` under the given tenant-scoped key.

        Implementations MAY overwrite an existing entry for the same key.
        Implementations MUST use ``state.model_dump_json()`` for serialization.
        """

    @abstractmethod
    async def load(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None:
        """Load the state for the given key, or return ``None`` if not found.

        Implementations MUST return ``None`` (not raise) for unknown sessions
        by default. Implementations MAY raise ``SessionNotFoundError`` instead
        when callers explicitly want exception-based error handling.
        """

    @abstractmethod
    async def list_recent(self, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]:
        """List up to ``limit`` summaries for the given ``(org_id, user_id)``,
        ordered by ``updated_at`` descending (most recent first).
        """

    @asynccontextmanager
    async def acquire_session_lock(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        *,
        timeout_ms: int = 30000,
    ) -> AsyncGenerator[None, None]:
        """Acquire an exclusive lock on the session key.

        Default implementation is a no-op (single-process stores rely on
        asyncio's cooperative scheduling). Distributed implementations
        (Redis/PostgreSQL/Tiered) override this to provide mutual exclusion
        across replicas.

        Lock acquisition failure after the implementation's retry budget is
        exhausted SHALL raise :class:`SessionStateConflictError`.

        Args:
            org_id: Tenant-scoped key component.
            user_id: User-scoped key component.
            session_id: Session-scoped key component.
            timeout_ms: Advisory lock TTL in milliseconds. Implementations
                MAY interpret this as the per-attempt ceiling or the total
                budget across retries.

        Yields:
            None — the caller's ``async with`` body executes the critical
            section.
        """
        yield  # default: no-op lock


class InMemorySessionStateStore(SessionStateStore):
    """Single-process in-memory implementation of :class:`SessionStateStore`.

    Intended for unit tests and single-replica development. State is lost
    when the process exits. Production deployments must use a distributed
    implementation (e.g., Redis-backed or PostgreSQL-backed).

    Storage layout: ``_storage[org_id][user_id][session_id] = (json_str, updated_at)``.
    """

    def __init__(self) -> None:
        self._storage: dict[uuid.UUID, dict[uuid.UUID, dict[uuid.UUID, tuple[str, datetime]]]] = {}

    async def save(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID, state: SessionState) -> None:
        json_str = state.model_dump_json()
        updated_at = datetime.now(UTC)
        self._storage.setdefault(org_id, {}).setdefault(user_id, {})[session_id] = (json_str, updated_at)

    async def load(self, org_id: uuid.UUID, user_id: uuid.UUID, session_id: uuid.UUID) -> SessionState | None:
        org_bucket = self._storage.get(org_id)
        if org_bucket is None:
            return None
        user_bucket = org_bucket.get(user_id)
        if user_bucket is None:
            return None
        entry = user_bucket.get(session_id)
        if entry is None:
            return None
        json_str, _updated_at = entry
        return SessionState.model_validate_json(json_str)

    async def list_recent(self, org_id: uuid.UUID, user_id: uuid.UUID, limit: int = 10) -> list[SessionSummary]:
        org_bucket = self._storage.get(org_id)
        if org_bucket is None:
            return []
        user_bucket = org_bucket.get(user_id)
        if user_bucket is None:
            return []

        rows: list[tuple[datetime, uuid.UUID, int | None]] = []
        for session_id, (_json_str, updated_at) in user_bucket.items():
            superstep = None
            try:
                state = SessionState.model_validate_json(_json_str)
                metadata = state.metadata or {}
                raw = metadata.get("superstep")
                if isinstance(raw, int):
                    superstep = raw
            except Exception:  # noqa: BLE001
                superstep = None
            rows.append((updated_at, session_id, superstep))

        rows.sort(key=lambda r: r[0], reverse=True)
        return [
            SessionSummary(
                session_id=session_id,
                org_id=org_id,
                user_id=user_id,
                updated_at=updated_at,
                superstep=superstep,
            )
            for updated_at, session_id, superstep in rows[:limit]
        ]
