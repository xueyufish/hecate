"""Workspace-level routing for the chat engine path (step5d, G3).

Resolution order for one request:

1. The ``chat_tool_loop_engine_enabled`` feature flag with workspace
   targeting: when the flag row is ``active`` + enabled and carries a
   non-empty ``tenant_allowlist``, the allowlist is the authoritative
   rollout surface (workspace in → engine, out → direct).
2. Otherwise the global ``CHAT_TOOL_LOOP_ENGINE_ENABLED`` setting.

Session affinity rides on the session row's metadata: the first tool
loop of a session records the resolved path, and continuations
(``session_resume``, follow-up turns) reuse that record instead of
re-resolving, so an in-flight conversation never switches path mid-way.
Every override change is written to the audit log as a rollout record.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.config import settings

logger = logging.getLogger(__name__)

CHAT_ENGINE_FLAG_KEY = "chat_tool_loop_engine_enabled"
CHAT_ENGINE_ROLLOUT_ACTION = "CHAT_ENGINE_ROLLOUT_CHANGED"
SESSION_PATH_METADATA_KEY = "chat_execution_path"

PATH_ENGINE = "engine"
PATH_DIRECT = "direct"


async def resolve_chat_engine_path(
    db: AsyncSession,
    workspace_id: uuid.UUID | None,
    *,
    session_path: str | None = None,
) -> str:
    """Resolve the effective chat execution path for one request.

    ``session_path`` (when recorded on the session) wins: an existing
    conversation keeps its established path regardless of flag changes.
    Missing workspace context falls through to the global setting.
    """
    if session_path in (PATH_ENGINE, PATH_DIRECT):
        return session_path
    if db is not None and workspace_id is not None:
        override = await _workspace_override(db, workspace_id)
        if override is not None:
            return PATH_ENGINE if override else PATH_DIRECT
    return PATH_ENGINE if settings.CHAT_TOOL_LOOP_ENGINE_ENABLED else PATH_DIRECT


async def _workspace_override(db: AsyncSession, workspace_id: uuid.UUID) -> bool | None:
    """Read the workspace-level override from the feature-flag row.

    Returns ``None`` when no workspace targeting exists, so the caller
    falls through to the global setting.
    """
    from hecate.models.feature_flag import FeatureFlagModel

    row = await db.execute(select(FeatureFlagModel).where(FeatureFlagModel.key == CHAT_ENGINE_FLAG_KEY))
    flag = row.scalar_one_or_none()
    if flag is None or flag.status != "active" or not flag.enabled:
        return None
    rules = flag.targeting_rules or {}
    allowlist = rules.get("tenant_allowlist")
    if allowlist:
        return str(workspace_id) in allowlist
    return True


async def read_session_path(db: AsyncSession, session_id: uuid.UUID | str | None) -> str | None:
    """Read the session's recorded execution path (None when unrecorded)."""
    if db is None or session_id is None:
        return None
    from hecate.models.session import SessionModel

    sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    session = await db.get(SessionModel, sid)
    if session is None or session.deleted:
        return None
    recorded = (session.metadata_ or {}).get(SESSION_PATH_METADATA_KEY)
    return recorded if recorded in (PATH_ENGINE, PATH_DIRECT) else None


async def record_session_path(db: AsyncSession, session_id: uuid.UUID | str | None, path: str) -> bool:
    """Record the resolved path on the session (first tool loop only).

    Best-effort: sessions without a row (raw chat requests without a
    session id kept by the client) simply do not record, and the next
    request re-resolves. Never raises.
    """
    if db is None or session_id is None or path not in (PATH_ENGINE, PATH_DIRECT):
        return False
    from hecate.models.session import SessionModel

    try:
        sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
        session = await db.get(SessionModel, sid)
        if session is None or session.deleted:
            return False
        metadata = dict(session.metadata_ or {})
        if metadata.get(SESSION_PATH_METADATA_KEY) in (PATH_ENGINE, PATH_DIRECT):
            return False
        metadata[SESSION_PATH_METADATA_KEY] = path
        session.metadata_ = metadata
        await db.flush()
        return True
    except Exception:  # noqa: BLE001 — affinity recording must not break execution
        logger.warning("Session path recording failed for session %s", session_id, exc_info=True)
        return False


async def record_rollout_change(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    old_value: Any,
    new_value: Any,
) -> None:
    """Write the rollout audit record for an override change.

    Best-effort by contract: the flag change itself must not fail
    because the audit write did, but a missing record is logged.
    """
    from hecate.execution.registry_support import registry_audit

    try:
        await registry_audit(
            db,
            workspace_id=workspace_id,
            actor=actor_id,
            action=CHAT_ENGINE_ROLLOUT_ACTION,
            resource_type="feature_flag",
            resource_id=None,
            detail={"old_value": str(old_value), "new_value": str(new_value)},
        )
    except Exception:  # noqa: BLE001
        logger.warning("Rollout record for %s failed (workspace %s)", CHAT_ENGINE_FLAG_KEY, workspace_id, exc_info=True)
