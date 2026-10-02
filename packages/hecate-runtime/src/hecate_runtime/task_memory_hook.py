"""Task memory runtime hooks — 4.21 task completion / failure entry points.

The runtime doesn't have a single "node finished" event today (each
node type owns its own completion path: tool calls return synchronously,
LLM nodes return via the Pregel runtime, etc.). Rather than wire a
specific node hook, this module exposes two functions:

- ``on_task_complete(workspace_id, agent_id, session_id)`` — happy path.
  Closes the active episode for the session and stamps ``closed_at``.
- ``on_task_failure(workspace_id, agent_id, session_id)`` — failure path.
  Same close semantics; downstream the ``escalate_failure`` capability
  triggers reflection recall for the retry context.

Both functions are best-effort: any failure logs ``warning`` and
returns ``None``; the chat path MUST NOT fail because of a
reflection-layer glitch. They are no-ops when the reflection feature
flag is off, when the runtime fails to resolve the memory provider,
or when the provider doesn't declare the relevant capability.

The functions are also publicly importable so the Pregel runtime
itself, the channel adapters, or any test harness can call them once
a task boundary is observed. They intentionally do NOT auto-detect
task boundaries — boundary detection is the caller's policy decision
(the task-memory capability spec defers it to the agent runtime
because auto-detection is unreliable).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


async def on_task_complete(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    task_type: str | None = None,
    episode_id: uuid.UUID | None = None,
) -> Any:
    """Close the active episode for ``session_id`` (happy path).

    If ``episode_id`` is provided, closes that specific episode
    (``provider.close_episode``). Otherwise the ``TaskMemoryService``
    falls back to lookup-by-session to find the open episode.

    Returns the ``EpisodeWriteResult`` from the provider, or ``None``
    when the feature flag is off / the provider lacks the capability.
    """
    from hecate.core.composition.memory_provider import (
        CAP_END_EPISODE,
        provider_supports,
        resolve_memory_provider,
    )
    from hecate.core.config import settings

    if not settings.REFLECTION_ENABLED:
        return None
    provider = resolve_memory_provider()
    if provider is None or not provider_supports(provider, CAP_END_EPISODE):
        return None
    try:
        if episode_id is not None:
            await provider.close_episode(
                workspace_id=workspace_id,
                agent_id=agent_id,
                episode_id=episode_id,
            )
            return await provider.end_episode(
                workspace_id=workspace_id,
                agent_id=agent_id,
                episode_id=episode_id,
            )
        # No specific episode id — let the service layer look up the
        # active one by session.
        from hecate_memory.memory.task_memory import TaskMemoryService

        ep = await _lookup_active_episode(workspace_id, agent_id, session_id)
        if ep is None:
            return None
        return await TaskMemoryService.close_episode(
            workspace_id=workspace_id,
            agent_id=agent_id,
            episode_id=ep,
        )
    except Exception as e:  # pragma: no cover — best-effort
        logger.warning(
            "on_task_complete failed (workspace=%s agent=%s session=%s): %s",
            workspace_id,
            agent_id,
            session_id,
            e,
        )
        return None


async def on_task_failure(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    actor_id: uuid.UUID | None = None,
    task_type: str | None = None,
    episode_id: uuid.UUID | None = None,
    confidence: float = 0.0,
) -> Any:
    """Close the active episode on failure and recall matching reflections.

    Same close semantics as ``on_task_complete``; the additional
    ``escalate_failure`` call surfaces previously-approved reflections
    whose ``use_cases`` match ``task_type`` so the retry prompt can
    include them. Returns the reflection hits (may be empty) or
    ``None`` when the feature flag is off.
    """
    close_result = await on_task_complete(
        workspace_id,
        agent_id,
        session_id,
        actor_id=actor_id,
        task_type=task_type,
        episode_id=episode_id,
    )
    from hecate.core.composition.memory_provider import (
        CAP_ESCALATE_FAILURE,
        provider_supports,
        resolve_memory_provider,
    )
    from hecate.core.config import settings

    if not settings.REFLECTION_ENABLED:
        return close_result
    provider = resolve_memory_provider()
    if provider is None or not provider_supports(provider, CAP_ESCALATE_FAILURE):
        return close_result
    if not task_type:
        return close_result
    try:
        hits = await provider.escalate_failure(
            workspace_id=workspace_id,
            agent_id=agent_id,
            task_type=task_type,
            confidence=confidence,
        )
        return {"close_result": close_result, "reflection_hits": hits}
    except Exception as e:  # pragma: no cover
        logger.warning(
            "on_task_failure escalate step failed (workspace=%s agent=%s session=%s): %s",
            workspace_id,
            agent_id,
            session_id,
            e,
        )
        return close_result


async def _lookup_active_episode(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
) -> uuid.UUID | None:
    from sqlalchemy import select

    from hecate.core.database import async_session_factory
    from hecate.models.task_memory import EpisodeModel

    try:
        async with async_session_factory() as db:
            stmt = (
                select(EpisodeModel.id)
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
    except Exception as e:  # pragma: no cover
        logger.warning(
            "_lookup_active_episode failed (workspace=%s agent=%s session=%s): %s",
            workspace_id,
            agent_id,
            session_id,
            e,
        )
        return None
