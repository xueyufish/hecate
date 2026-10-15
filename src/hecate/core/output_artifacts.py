"""Extract explicit output references without trusting client tool history."""

from __future__ import annotations

import json
from typing import Any


def declared_output_artifacts(state: dict[str, Any], *, input_message_count: int = 0) -> Any:
    """Collect graph declarations or declarations in newly produced tool results.

    These are candidates, never authorization: the entry service must resolve
    each reference's workspace ownership before publishing it. Supplied tool
    history is excluded so a client cannot declare an output by replaying text.
    """
    if "declared_artifacts" in state:
        return state["declared_artifacts"]
    declarations: list[Any] = []
    messages = state.get("messages")
    if not isinstance(messages, list):
        return None
    for message in messages[input_message_count:]:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                continue
        if isinstance(content, dict) and "declared_artifacts" in content:
            declared = content["declared_artifacts"]
            declarations.extend(declared if isinstance(declared, list) else [declared])
    return declarations or None
