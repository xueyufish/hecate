"""Publishing channel service (1.3.20).

CRUD and target resolution for publishing channels — the
alias-indirection layer between external surfaces and an agent's
published version. Resolution semantics:

- ``published`` bind mode tracks ``agents.published_version`` at
  invocation time, so a new publish is picked up automatically.
- ``pinned`` bind mode serves ``pinned_version`` until explicitly
  repointed.
- An explicit version override (``X-Agent-Version`` / ``?version=``)
  wins over both, for debugging.

Creation constraints: the target agent must have a published version
(a channel is meaningless before the first publish), and a ``pinned``
binding must name an existing version. ``embed`` / ``webhook`` types are
accepted but marked ``unwired`` — no invocation surface backs them in v1.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.channels import (
    WIRED_CHANNEL_TYPES,
    ChannelModel,
)

logger = logging.getLogger(__name__)


@dataclass
class ResolvedChannelTarget:
    """The concrete invocation target a channel resolves to."""

    channel: ChannelModel
    agent_id: uuid.UUID
    resolved_version: int


class ChannelPublishingService:
    """CRUD + resolution for publishing channels (1.3.20)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- CRUD ---------------------------------------------------------------

    async def create_channel(
        self,
        *,
        workspace_id: uuid.UUID,
        created_by: uuid.UUID | None,
        name: str,
        channel_type: str,
        agent_id: uuid.UUID,
        bind_mode: str = "published",
        pinned_version: int | None = None,
        config: dict | None = None,
    ) -> ChannelModel:
        """Create a channel after validating the publish/pin constraints."""
        agent = await self._get_agent(agent_id)
        if agent.published_version is None:
            raise ValueError(
                f"Agent {agent_id} has no published version — commit and publish one before creating a channel"
            )

        if bind_mode == "pinned":
            await self._assert_version_exists(agent_id, pinned_version)

        status = "active" if channel_type in WIRED_CHANNEL_TYPES else "unwired"
        if channel_type == "im" and not (config or {}).get("provider"):
            raise ValueError("im channels require config.provider (the IM adapter name)")

        channel = ChannelModel(
            workspace_id=workspace_id,
            name=name,
            type=channel_type,
            agent_id=agent.id,
            bind_mode=bind_mode,
            pinned_version=pinned_version if bind_mode == "pinned" else None,
            config=config or {},
            status=status,
            created_by=created_by,
        )
        self.db.add(channel)
        await self.db.flush()
        await self.db.refresh(channel)
        logger.info("Created %s channel '%s' for agent %s (%s)", channel_type, name, agent_id, status)
        return channel

    async def list_channels(
        self,
        workspace_id: uuid.UUID | None = None,
        agent_id: uuid.UUID | None = None,
    ) -> list[ChannelModel]:
        stmt = select(ChannelModel).where(~ChannelModel.deleted)
        if workspace_id is not None:
            stmt = stmt.where(ChannelModel.workspace_id == workspace_id)
        if agent_id is not None:
            stmt = stmt.where(ChannelModel.agent_id == agent_id)
        result = await self.db.execute(stmt.order_by(ChannelModel.created_at.desc()))
        return list(result.scalars().all())

    async def get_channel(self, channel_id: uuid.UUID) -> ChannelModel:
        channel = await self._get_channel(channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        return channel

    async def update_channel(
        self,
        channel_id: uuid.UUID,
        *,
        name: str | None = None,
        bind_mode: str | None = None,
        pinned_version: int | None = None,
        config: dict | None = None,
        status: str | None = None,
    ) -> ChannelModel:
        """Update channel metadata or repoint its binding.

        Repointing to a pinned version validates the target version
        exists; switching to ``published`` clears ``pinned_version``.
        """
        channel = await self.get_channel(channel_id)

        effective_mode = bind_mode or channel.bind_mode
        if effective_mode == "pinned":
            effective_pin = pinned_version if pinned_version is not None else channel.pinned_version
            await self._assert_version_exists(channel.agent_id, effective_pin)
            channel.pinned_version = effective_pin
        elif bind_mode == "published":
            channel.pinned_version = None

        if name is not None:
            channel.name = name
        if bind_mode is not None:
            channel.bind_mode = bind_mode
        if config is not None:
            channel.config = config
        if status is not None:
            channel.status = status

        await self.db.flush()
        await self.db.refresh(channel)
        return channel

    async def delete_channel(self, channel_id: uuid.UUID) -> None:
        from datetime import UTC, datetime

        channel = await self.get_channel(channel_id)
        channel.deleted = True
        channel.deleted_at = datetime.now(UTC)
        await self.db.flush()

    # --- resolution -----------------------------------------------------------

    async def resolve_target(
        self,
        channel_id: uuid.UUID,
        version_override: int | None = None,
    ) -> ResolvedChannelTarget:
        """Resolve a channel invocation to ``(channel, agent_id, version)``.

        The explicit override wins for debugging; otherwise ``pinned``
        channels serve their locked version and ``published`` channels
        track the agent's latest publish. Unwired/disabled channels are
        rejected — they have no invocation surface.
        """
        channel = await self.get_channel(channel_id)
        if channel.status == "unwired":
            raise ValueError(f"Channel '{channel.name}' type '{channel.type}' has no invocation surface")
        if channel.status == "disabled":
            raise ValueError(f"Channel '{channel.name}' is disabled")

        if version_override is not None:
            await self._assert_version_exists(channel.agent_id, version_override)
            resolved = version_override
        elif channel.bind_mode == "pinned":
            resolved = channel.pinned_version
        else:
            agent = await self._get_agent(channel.agent_id)
            resolved = agent.published_version
        if resolved is None:
            raise ValueError(f"Channel '{channel.name}' resolves to no version")
        return ResolvedChannelTarget(channel=channel, agent_id=channel.agent_id, resolved_version=resolved)

    async def list_pinned_channels(self, agent_id: uuid.UUID, version: int) -> list[ChannelModel]:
        """Channels pinning ``version`` of ``agent_id`` (delete-constraint input)."""
        result = await self.db.execute(
            select(ChannelModel).where(
                ChannelModel.agent_id == agent_id,
                ChannelModel.bind_mode == "pinned",
                ChannelModel.pinned_version == version,
                ~ChannelModel.deleted,
            )
        )
        return list(result.scalars().all())

    async def resolve_im_route(self, provider_name: str) -> ResolvedChannelTarget | None:
        """Resolve the im-type channel routing an inbound IM message.

        The lookup key is the IM adapter's provider name (the webhook
        path's ``{name}``), matched against the channel's
        ``config.provider``. Per-message resolution means repointing a
        binding takes effect on the next message without rebuilding
        sessions. Returns ``None`` when no active channel row exists —
        callers MUST reject the message rather than fall back to a
        default agent.
        """
        result = await self.db.execute(
            select(ChannelModel).where(
                ChannelModel.type == "im",
                ChannelModel.status == "active",
                ~ChannelModel.deleted,
            )
        )
        candidates = [c for c in result.scalars().all() if (c.config or {}).get("provider") == provider_name]
        if not candidates:
            return None
        channel = candidates[0]

        if channel.bind_mode == "pinned":
            resolved = channel.pinned_version
        else:
            agent = await self._get_agent(channel.agent_id)
            resolved = agent.published_version
        if resolved is None:
            logger.error(
                "IM channel '%s' resolves to no version for agent %s; rejecting message",
                channel.name,
                channel.agent_id,
            )
            return None
        return ResolvedChannelTarget(channel=channel, agent_id=channel.agent_id, resolved_version=resolved)

    # --- internals --------------------------------------------------------------

    async def _get_channel(self, channel_id: uuid.UUID) -> ChannelModel | None:
        result = await self.db.execute(select(ChannelModel).where(ChannelModel.id == channel_id, ~ChannelModel.deleted))
        return result.scalar_one_or_none()

    async def _get_agent(self, agent_id: uuid.UUID) -> AgentModel:
        result = await self.db.execute(select(AgentModel).where(AgentModel.id == agent_id, ~AgentModel.deleted))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        return agent

    async def _assert_version_exists(self, agent_id: uuid.UUID, version: int | None) -> None:
        if version is None:
            raise ValueError("bind_mode='pinned' requires pinned_version")
        result = await self.db.execute(
            select(AgentVersionModel).where(
                AgentVersionModel.agent_id == agent_id,
                AgentVersionModel.version == version,
                ~AgentVersionModel.deleted,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"Version {version} not found for agent {agent_id}")
