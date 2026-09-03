"""IMSessionRouter — IM-channel-aware session and conversation lookup.

Replaces the in-memory ``hecate.channel.gateway.session.SessionRouter`` for IM
channels. The router is the cross-channel bridge: the same Hecate user
bound to multiple IM channels (e.g., Feishu + Slack) shares one
conversation thread across both channels (design.md D4).

The deterministic SHA-256 mapping ensures that two inbound messages from
the same user, on the same channel, in the same workspace, always resolve
to the same conversation UUID — without requiring a state lookup before
the lookup itself.

Reference: deer-flow ``ChannelStore`` pattern (``backend/app/channels/store.py``).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.conversation import ConversationModel
from hecate.models.session import SessionModel

logger = logging.getLogger(__name__)


def derive_conversation_id(
    workspace_id: Any,
    user_id: Any,
    channel_type: str | None = None,
    im_app_id: str | None = None,
) -> uuid.UUID:
    """Derive a deterministic conversation UUID for an IM channel session.

    Same ``(workspace, user)`` always returns the same UUID, which is what
    enables cross-channel session sharing for the same Hecate user
    without an explicit state lookup. ``channel_type`` and ``im_app_id``
    are accepted for backward-compatibility with earlier callers but no
    longer participate in the hash.

    Args:
        workspace_id: workspace scoping the conversation.
        user_id: resolved Hecate user from ``IMIdentityBindingModel``.
        channel_type: ignored (kept for API compatibility).
        im_app_id: ignored (kept for API compatibility).
    """
    material = f"{workspace_id}|{user_id}"
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return uuid.UUID(bytes=digest[:16])


class IMSessionRouter:
    """Resolves (or creates) a Conversation for an IM-bound user."""

    def __init__(self, default_agent_id: uuid.UUID | None = None) -> None:
        """Args:
        default_agent_id: fallback ``agent_id`` used when creating a new
            Conversation for a brand-new IM user. In production, the
            agent is selected per workspace policy — passing a default is
            a sensible MVP simplification.
        """
        self._default_agent_id = default_agent_id or uuid.UUID("00000000-0000-0000-0000-000000000000")

    async def resolve_or_create(
        self,
        *,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        channel_type: str,
        im_app_id: str | None,
        chat_id: str | None,
    ) -> tuple[ConversationModel, SessionModel]:
        """Return the active Conversation and Session for the user.

        Looks up an existing Conversation by the deterministic UUID; if not
        found, creates one with ``source_channel=channel_type`` and
        ``im_chat_id=chat_id``. A new Session is always created (matches
        the existing ``SessionModel`` semantics).
        """
        conv_id = derive_conversation_id(workspace_id, user_id, channel_type, im_app_id)
        stmt = select(ConversationModel).where(
            ConversationModel.id == conv_id,
            ConversationModel.deleted == False,  # noqa: E712
        )
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()
        if conversation is None:
            conversation = ConversationModel(
                id=conv_id,
                workspace_id=workspace_id,
                agent_id=self._default_agent_id,
                source_channel=channel_type,
                im_chat_id=chat_id,
                title=None,
            )
            session.add(conversation)
            await session.flush()
            logger.info(
                "Created IM conversation workspace=%s user=%s channel=%s id=%s",
                workspace_id,
                user_id,
                channel_type,
                conv_id,
            )
        else:
            # Refresh im_chat_id if it changed (the user may have moved chats).
            if chat_id is not None and conversation.im_chat_id != chat_id:
                conversation.im_chat_id = chat_id
                await session.flush()
        # Always create a new Session for this turn (matches existing semantics).
        sess = SessionModel(
            conversation_id=conversation.id,
            agent_id=conversation.agent_id,
            workspace_id=workspace_id,
            source_channel=channel_type,
            status="active",
        )
        session.add(sess)
        await session.flush()
        return conversation, sess
