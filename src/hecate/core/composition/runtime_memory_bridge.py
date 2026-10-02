"""Bridge between the platform and the ``hecate_runtime`` kernel (step5b).

The kernel never imports platform composition; the platform installs its
services into the kernel here:

- ``build_runtime_config()`` maps platform ``Settings`` onto the kernel's
  ``RuntimeConfig`` (flag parity is one-to-one by field).
- ``install_runtime_kernel_services()`` installs the kernel's memory
  provider source (delegating lazily to the platform resolver so provider
  switches stay dynamic), the DB-backed open-episode lookup, and the
  DB-backed flush-policy resolver.

Called once from the application lifespan (``compose_application``).
"""

from __future__ import annotations

import uuid

from hecate_runtime.config import RuntimeConfig, configure_kernel
from hecate_runtime.memory import (
    install_episode_lookup,
    install_memory_policy_resolver,
    install_memory_provider_source,
)


def build_runtime_config() -> RuntimeConfig:
    """Map platform settings onto the kernel configuration, field for field."""
    from hecate.core.config import settings

    return RuntimeConfig(
        reflection_enabled=settings.REFLECTION_ENABLED,
        memory_flush_enabled=settings.MEMORY_FLUSH_ENABLED,
        memory_pressure_nudge_enabled=settings.MEMORY_PRESSURE_NUDGE_ENABLED,
        memory_prefetch_enabled=settings.MEMORY_PREFETCH_ENABLED,
        memory_prefetch_max_entries=settings.MEMORY_PREFETCH_MAX_ENTRIES,
        memory_prefetch_max_tokens=settings.MEMORY_PREFETCH_MAX_TOKENS,
        llm_guard_enabled=settings.LLM_GUARD_ENABLED,
        fernet_key=settings.FERNET_KEY,
    )


def install_runtime_kernel_services() -> None:
    """Install kernel configuration and memory services from the platform."""
    configure_kernel(build_runtime_config())
    install_memory_provider_source(_resolve_memory_provider)
    install_episode_lookup(_find_open_episode)
    install_memory_policy_resolver(_memory_flush_policy_enabled)


def _resolve_memory_provider() -> object | None:
    from hecate.core.composition.memory_provider import resolve_memory_provider

    return resolve_memory_provider()


async def _find_open_episode(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
) -> uuid.UUID | None:
    """Kernel adapter over the EpisodeModel lookup the hook used inline."""
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
    except Exception:  # pragma: no cover — best-effort, matches hook contract
        return None


async def _memory_flush_policy_enabled(
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> bool:
    """Kernel adapter over the DB-backed memory policy lookup."""
    from hecate.core.composition.memory_policy import resolve_policy
    from hecate.core.database import async_session_factory

    async with async_session_factory() as db:
        policy = await resolve_policy(db, workspace_id, agent_id)
        return bool(policy.flush_enabled)
