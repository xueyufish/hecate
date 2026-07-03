"""Channel types — CanonicalMessage and related data structures.

Defines the universal message format that all channel adapters use to
communicate with the Gateway.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime


@dataclasses.dataclass(frozen=True)
class Attachment:
    """An attachment included in a message.

    Attributes:
        type: MIME type (e.g., "image/png", "application/pdf").
        url: URL where the attachment can be fetched.
        name: Original filename.
        size: Size in bytes (None if unknown).
    """

    type: str
    url: str
    name: str = ""
    size: int | None = None


@dataclasses.dataclass(frozen=True)
class MessageContent:
    """Content of a canonical message.

    Attributes:
        text: Plain text content (None if message is attachment-only).
        attachments: Tuple of attachments.
    """

    text: str | None = None
    attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(frozen=True)
class CanonicalMessage:
    """Universal message format for all channel adapters.

    Every channel adapter converts platform-specific messages into this
    format before passing them to the Gateway. The Gateway and agent
    runtime only see CanonicalMessage — never platform-specific formats.

    Attributes:
        id: Unique message identifier.
        channel_id: Identifier of the source channel (e.g., "feishu", "slack").
        user_id: Platform-specific user identifier.
        session_id: Session identifier for conversation continuity (None for new).
        content: Message content (text + attachments).
        metadata: Platform-specific passthrough data.
        timestamp: When the message was created.
    """

    id: uuid.UUID
    channel_id: str
    user_id: str
    session_id: str | None
    content: MessageContent
    metadata: dict[str, object] = dataclasses.field(default_factory=dict)
    timestamp: datetime = dataclasses.field(default_factory=lambda: datetime.now(UTC))
