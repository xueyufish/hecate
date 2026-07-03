"""ChannelCapabilities — declarative capability model for channels.

Each channel adapter declares what features it supports via a frozen
dataclass. The Gateway checks capabilities before attempting operations.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ChannelCapabilities:
    """Declarative capability model for channel adapters.

    Attributes:
        streaming: Whether the channel supports streaming responses.
        interactive_buttons: Whether the channel supports interactive buttons.
        file_upload: Whether the channel supports file uploads.
        markdown: Whether the channel supports Markdown formatting.
        rich_cards: Whether the channel supports rich card messages.
        max_message_length: Maximum message length in characters (None = unlimited).
    """

    streaming: bool = False
    interactive_buttons: bool = False
    file_upload: bool = False
    markdown: bool = False
    rich_cards: bool = False
    max_message_length: int | None = None
