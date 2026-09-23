"""Task Memory service — 4.21 + 4.23 surfaces on top of the builtin provider.

Three concerns live in this module, behind the ``REFLECTION_ENABLED``
master switch:

1. **Episode write path** (Group 3). ``add_episode`` /
   ``record_tool_event`` / ``close_episode`` / ``append_turn_tool_events``
   all consume ``episodes`` rows. Active-episode lookup keys on
   ``(workspace_id, agent_id, session_id, closed_at IS NULL)`` —
   matches the unit isolation rule in the ``task-memory`` capability
   spec.

2. **Task memory retrieval** (Group 4 stub; the real implementation lands
   alongside the ReflectionEngine). ``search_reflections`` /
   ``search_cross_thread`` are wired through the protocol today so the
   builtin provider's capability check is honest; both return empty
   results until the reflection engine is implemented in a follow-up
   change.

3. **Lifecycle hook fan-out** (Group 3/4 boundary). ``end_episode`` and
   ``escalate_failure_recall`` are the provider-side bridges for the
   new ``CAP_END_EPISODE`` / ``CAP_ESCALATE_FAILURE`` capabilities. They
   return no-ops here; downstream work replaces the bodies.

All public methods are best-effort: a failure logs ``warning`` and
returns a structured ``EpisodeWriteResult(ok=False, error=...)`` /
empty list rather than raising. The chat path MUST NOT fail because of
a reflection-layer glitch.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from hecate.core.composition.memory_provider import EpisodeWriteResult
from hecate.core.database import async_session_factory
from hecate.models.task_memory import (
    EpisodeModel,
)

logger = logging.getLogger(__name__)


class TaskMemoryService:
    """Service-layer facade for Task Memory (4.21) and Cross-thread (4.23).

    All methods are coroutines and short-circuit (return no-op / empty)
    if the runtime flag is off — the caller (``BuiltinMemoryProvider``
    methods) checks ``settings.REFLECTION_ENABLED`` first, so reaching
    here implies the flag is on.
    """

    # ──────────────── 4.21 Episode lifecycle ────────────────

    @staticmethod
    async def add_episode(
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        task_type: str = "",
        situation: str | None = None,
        intent: str | None = None,
    ) -> EpisodeWriteResult:
        """Insert a new episode row and return its id.

        Mirrors the ``add_episode`` Protocol method. The caller is the
        ``BuiltinMemoryProvider.add_episode`` adapter which already
        checked the gate; if you call this directly without the gate,
        downstream ReflectionEngine scans can pull it.
        """
        new_id = uuid.uuid4()
        try:
            async with async_session_factory() as db:
                ep = EpisodeModel(
                    id=new_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    actor_id=actor_id,
                    session_id=session_id,
                    task_type=task_type or "",
                    situation=situation,
                    intent=intent,
                    actions=[],
                    outcomes=[],
                )
                db.add(ep)
                await db.commit()
        except Exception as e:
            logger.warning("TaskMemoryService.add_episode failed: %s", e)
            return EpisodeWriteResult(ok=False, error=str(e))
        return EpisodeWriteResult(ok=True, episode_id=new_id, closed=False)

    @staticmethod
    async def record_tool_event(
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | str | None,
        tool_name: str,
        args: dict[str, Any],
        result_ref: str | None = None,
        ts: Any = None,
    ) -> EpisodeWriteResult:
        """Append a ``TOOL`` event to the active episode for this session.

        No active episode → silent skip (the ``add_episode`` call is the
        caller's responsibility; we don't auto-create here because
        that's a runtime-policy decision the protocol layer owns).

        If ``session_id`` is a string (the context processor passes the
        chain ``session_id``), we coerce to UUID best-effort; on parse
        failure the call short-circuits with ``error='invalid_session_id'``
        rather than crashing.
        """
        sid = _coerce_session_id(session_id)
        if sid is None:
            return EpisodeWriteResult(ok=False, error="invalid_session_id")
        try:
            async with async_session_factory() as db:
                ep = await _find_active_episode(db, workspace_id, agent_id, sid)
                if ep is None:
                    return EpisodeWriteResult(ok=False, error="no_active_episode")
                event = {
                    "event": "TOOL",
                    "tool_name": tool_name,
                    "args": args,
                    "result_ref": result_ref,
                    "ts": ts.isoformat() if hasattr(ts, "isoformat") else None,
                }
                actions = list(ep.actions or [])
                actions.append(event)
                ep.actions = actions
                await db.commit()
                return EpisodeWriteResult(ok=True, episode_id=ep.id, closed=False)
        except Exception as e:
            logger.warning("TaskMemoryService.record_tool_event failed: %s", e)
            return EpisodeWriteResult(ok=False, error=str(e))

    @staticmethod
    async def close_episode(
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        episode_id: uuid.UUID,
    ) -> EpisodeWriteResult:
        """Mark an episode closed and stamp ``closed_at``.

        Idempotent: re-closing an already-closed episode is a no-op
        (returns ``closed=True`` with the same id) rather than an error,
        because the runtime may invoke this defensively after a crash
        recovery.
        """
        try:
            async with async_session_factory() as db:
                stmt = select(EpisodeModel).where(
                    EpisodeModel.id == episode_id,
                    EpisodeModel.workspace_id == workspace_id,
                    EpisodeModel.agent_id == agent_id,
                    ~EpisodeModel.deleted,
                )
                ep = (await db.execute(stmt)).scalar_one_or_none()
                if ep is None:
                    return EpisodeWriteResult(ok=False, error="not_found")
                from datetime import UTC, datetime

                if ep.closed_at is None:
                    ep.closed_at = datetime.now(UTC)
                ep_id = ep.id
                await db.commit()
                return EpisodeWriteResult(ok=True, episode_id=ep_id, closed=True)
        except Exception as e:
            logger.warning("TaskMemoryService.close_episode failed: %s", e)
            return EpisodeWriteResult(ok=False, error=str(e))

    @staticmethod
    async def append_turn_tool_events(
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | str | None,
        messages: list[dict[str, Any]],
    ) -> None:
        """Sync-turn sub-action hook used by ``BuiltinMemoryProvider.sync_turn``.

        Walks ``messages`` looking for assistant entries with
        ``tool_calls`` and tool results with matching ``tool_call_id``,
        batching them into ``record_tool_event`` writes. Best-effort:
        individual event failures are logged but don't raise — see
        ``task-memory`` capability spec.
        """
        for msg in messages or []:
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                call_id = tc.get("id") or ""
                tool_name = (tc.get("function") or {}).get("name") or tc.get("name") or ""
                args = (tc.get("function") or {}).get("arguments") or tc.get("args") or {}
                if isinstance(args, str):
                    try:
                        import json

                        args = json.loads(args)
                    except Exception:
                        args = {"_raw": args}
                elif not isinstance(args, dict):
                    args = {"_value": args}
                result_ref = _match_tool_result(messages, call_id)
                await TaskMemoryService.record_tool_event(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    tool_name=tool_name,
                    args=args,
                    result_ref=result_ref,
                )

    # ──────────────── 4.21 lifecycle hooks (provider bridge) ────────────────

    @staticmethod
    async def end_episode(
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        episode_id: uuid.UUID,
    ) -> None:
        """``CAP_END_EPISODE`` hook — re-export of ``close_episode``.

        ReflectionEngine fan-out lives in a downstream change. Until
        then this is a faithful pass-through.
        """
        await TaskMemoryService.close_episode(
            workspace_id=workspace_id,
            agent_id=agent_id,
            episode_id=episode_id,
        )

    @staticmethod
    async def escalate_failure_recall(
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        task_type: str,
        confidence: float,
    ) -> list[Any]:
        """``CAP_ESCALATE_FAILURE`` hook — placeholder.

        Returns the reflections matching ``task_type`` for the failure
        retry context. The ReflectionEngine's failure-retry integration
        is owned by a downstream task; this stub keeps the capability
        honest without doing the matching yet.
        """
        try:
            return await TaskMemoryService.search_reflections(
                query="",
                workspace_id=workspace_id,
                agent_id=agent_id,
                top_k=5,
                task_type=task_type,
            )
        except Exception as e:
            logger.warning("TaskMemoryService.escalate_failure_recall failed: %s", e)
            return []

    # ──────────────── 4.21 tier-4 retrieval (stub for Group 4) ────────────────

    @staticmethod
    async def search_reflections(
        *,
        query: str,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        top_k: int = 5,
        task_type: str | None = None,
    ) -> list[Any]:
        """Stub for the ReflectionEngine output surface.

        Group 4 wires the real implementation. Today's behavior: return
        ``[]`` so the capability is honest but the consumer is told
        "no reflections yet" via an empty result rather than a crash.
        """
        return []

    @staticmethod
    async def search_cross_thread(
        *,
        query: str,
        workspace_id: uuid.UUID,
        team_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        top_k: int = 5,
        tags: list[str] | None = None,
    ) -> list[Any]:
        """Stub for the cross-thread retrieval surface (4.23).

        Today's behavior: empty list. Group 4 (or a follow-up cross-thread
        change) wires the four-layer namespace query against the
        extended ``memories`` / ``knowledge_memories`` tables.
        """
        return []


# ──────────────── internal helpers ────────────────


async def _find_active_episode(
    db: Any,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
) -> EpisodeModel | None:
    stmt = (
        select(EpisodeModel)
        .where(
            EpisodeModel.workspace_id == workspace_id,
            EpisodeModel.agent_id == agent_id,
            EpisodeModel.session_id == session_id,
            EpisodeModel.closed_at.is_(None),
            ~EpisodeModel.deleted,
        )
        .order_by(EpisodeModel.created_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def _coerce_session_id(session_id: Any) -> uuid.UUID | None:
    """Coerce the chain-level ``session_id`` to a UUID; tolerant of strings."""
    if session_id is None:
        return None
    if isinstance(session_id, uuid.UUID):
        return session_id
    if isinstance(session_id, str):
        try:
            return uuid.UUID(session_id)
        except Exception:
            return None
    return None


def _match_tool_result(messages: list[dict[str, Any]], call_id: str) -> str | None:
    """First tool result matching ``call_id`` (truncated to 1 KB)."""
    for m in messages:
        if m.get("role") != "tool":
            continue
        if m.get("tool_call_id") != call_id:
            continue
        content = m.get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
        if isinstance(content, str):
            return content[:1024]
        return str(content)[:1024]
    return None
