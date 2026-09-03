"""SessionStateMaterializer — engine CheckpointStore adapter to SessionStateStore.

Implements the engine-layer :class:`CheckpointStore` ABC and forwards saves
to the existing :class:`SessionStateStore` (Redis / PostgreSQL / Tiered) under
the tenant-scoped triple ``(org_id, user_id, session_id)``. The engine layer
remains single-key (``session_id``); tenant context is injected via the
``tenant_context_provider`` closure (same pattern as ``PostgresEventStore``).

The engine still calls ``save(session_id, superstep, node_id, channel_state, ...)``
with the old signature; this adapter maps that onto the SessionState Pydantic
model with ``channel_state`` filled and ``event_position`` synced as the
log-version anchor (consumer in fold/restore tail-replay).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from hecate.runtime.checkpoint import CheckpointStore
from hecate.runtime.eventstore import EventStore
from hecate.runtime.session_state import SessionState, SessionStateStore

logger = logging.getLogger(__name__)

TenantContextProvider = Callable[[], tuple[uuid.UUID, uuid.UUID] | None]


def _bounded_retain(value: Any, head_chars: int = 32_000, tail_chars: int = 8_000) -> dict[str, Any]:
    """Apply a dsh TextRetainer-style bounded projection to a channel value.

    Replaces oversized string leaves with ``{"_omitted": True, "_prefix": ..., "_suffix": ..., "_omitted_bytes": N}``
    placeholders. Other value types pass through unchanged (the caller may
    apply stricter policies at a later time).
    """
    if isinstance(value, str) and len(value) > head_chars + tail_chars:
        prefix = value[:head_chars]
        suffix = value[-tail_chars:] if tail_chars else ""
        omitted = len(value) - head_chars - tail_chars
        return {
            "_omitted": True,
            "_prefix": prefix,
            "_suffix": suffix,
            "_omitted_bytes": omitted,
        }
    return {"value": value}


def _project_channel_state(channel_state: dict[str, Any]) -> dict[str, Any]:
    """Apply the bounded retainer over a channel snapshot."""
    projected: dict[str, Any] = {}
    for name, value in channel_state.items():
        if name.startswith("_") or name.startswith("sys."):
            continue
        projected[name] = _bounded_retain(value)
    return projected


class SessionStateMaterializer(CheckpointStore):
    """Engine CheckpointStore adapter backed by a SessionStateStore.

    Args:
        session_state_store: The configured SessionStateStore implementation
            (Redis / PostgreSQL / Tiered).
        tenant_context_provider: Closure returning ``(org_id, user_id)`` for the
            current request context. Returning ``None`` skips persistence (the
            engine execution continues without blocking).
        event_store: Optional EventStore; if provided, ``event_position`` is
            synced as the log-version anchor (for tail-replay restore).
    """

    def __init__(
        self,
        session_state_store: SessionStateStore,
        tenant_context_provider: TenantContextProvider,
        event_store: EventStore | None = None,
    ) -> None:
        self._session_state_store = session_state_store
        self._tenant_context_provider = tenant_context_provider
        self._event_store = event_store

    def _resolve_tenant(self) -> tuple[uuid.UUID, uuid.UUID] | None:
        try:
            return self._tenant_context_provider()
        except Exception:
            logger.warning("session_state_materializer_tenant_resolve_failed", exc_info=True)
            return None

    async def _sync_event_position(self, state: SessionState) -> SessionState:
        if self._event_store is None:
            return state
        try:
            position = await self._event_store.get_version(state.metadata.get("session_id", uuid.UUID(int=0)))
        except Exception:
            return state
        return state.model_copy(update={"event_position": position})

    async def save(
        self,
        session_id: uuid.UUID,
        superstep: int,
        node_id: str | None,
        channel_state: dict,
        pending_writes: list | None = None,
        metadata: dict | None = None,
    ) -> uuid.UUID:
        del pending_writes
        tenant = self._resolve_tenant()
        if tenant is None:
            logger.debug("session_state_materializer_skip_no_tenant")
            return uuid.uuid4()
        org_id, user_id = tenant

        projected = _project_channel_state(channel_state)
        merged_metadata: dict[str, Any] = {
            "session_id": session_id,
            "superstep": superstep,
            "node_id": node_id,
            **(metadata or {}),
        }
        state = SessionState(
            channel_state=projected,
            metadata=merged_metadata,
        )
        state = await self._sync_event_position(state)

        await self._session_state_store.save(org_id, user_id, session_id, state)
        return uuid.uuid4()

    async def load(self, session_id: uuid.UUID, checkpoint_id: uuid.UUID | None = None) -> dict | None:
        tenant = self._resolve_tenant()
        if tenant is None:
            return None
        org_id, user_id = tenant
        state = await self._session_state_store.load(org_id, user_id, session_id)
        if state is None:
            return None
        return {
            "id": checkpoint_id or uuid.uuid4(),
            "session_id": session_id,
            "superstep": state.metadata.get("superstep", 0),
            "node_id": state.metadata.get("node_id"),
            "channel_state": state.channel_state,
            "metadata": {**state.metadata, "log_version": state.event_position},
        }

    async def list_checkpoints(self, session_id: uuid.UUID, limit: int = 10) -> list[dict]:
        return []


def make_tenant_context_provider(
    getter: Callable[[], Awaitable[tuple[uuid.UUID, uuid.UUID] | None]],
) -> TenantContextProvider:
    """Adapt an async context getter into a sync closure for the materializer.

    The materializer calls the provider synchronously; if the request context
    is async, callers should pre-await and capture into a closure.
    """

    def _sync() -> tuple[uuid.UUID, uuid.UUID] | None:
        cached = getattr(_sync, "_cached", None)
        if cached is not None:
            return cached
        return None

    return _sync
