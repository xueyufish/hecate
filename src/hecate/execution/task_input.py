"""Supported human input contract shared by platform dispatch and wakes."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

TASK_USER_INPUT_SCHEMA_REF = "urn:hecate:task-user-input/0"
# Versioned schema distinguishing a live supplement (submitted while a
# platform-local controller_loop task is RUNNING) from a waiting_input
# answer. The payload is {"schema_version": 1, "content": <JSON object>};
# the content object — never the envelope — becomes the model-visible
# input revision content at the next not-yet-started decision boundary.
TASK_LIVE_INPUT_SCHEMA_REF = "urn:hecate:task-live-input/1"


def input_wait_timeout(value: Any) -> float | None:
    """Validate a declared timeout, including datetime precision and range."""
    if value is None:
        return None
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError
        duration = timedelta(seconds=value)
        if duration <= timedelta(0):
            raise ValueError
        datetime.now(UTC) + duration
        return float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("input_wait_timeout_seconds must be positive, finite and representable") from None


def plain_human_input_wait(value: Any) -> bool:
    """Recognize ordinary tool input waits, excluding workflow and approval waits."""
    return (
        isinstance(value, dict)
        and value.get("kind") == "input"
        and not value.get("await_task_ref")
        and not value.get("approval_ref")
        and all(
            isinstance(value.get(key), str) and value[key].strip()
            for key in ("tool", "execution_id", "arguments_digest")
        )
        and human_input_contract(value.get("contract")) is not None
    )


def human_input_contract(value: Any) -> dict[str, Any] | None:
    """Recognize the explicit required-field contract, never arbitrary feedback."""
    if not isinstance(value, dict) or not value:
        return None
    fields = value.get("fields")
    if (
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(field, str) or not field.strip() or field == "approval_ref" for field in fields)
        or len(set(fields)) != len(fields)
        or ("prompt" in value and not isinstance(value["prompt"], str))
    ):
        return None
    return value


def live_input_payload(value: Any) -> dict[str, Any] | None:
    """Recognize the versioned live-supplement envelope, never control data.

    Returns the model-visible content object, or ``None`` when the value is
    not a conforming ``{"schema_version": 1, "content": <object>}`` payload.
    Approval and wait-control keys are rejected outright: free
    supplement text must never be able to approve an action, consume a
    wait token or forge a child result.
    """

    if not isinstance(value, dict) or set(value) - {"schema_version", "content"}:
        return None
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1 or "content" not in value:
        return None
    content = value["content"]
    if (
        not isinstance(content, dict)
        or not content
        or "approval_ref" in content
        or "wait_token" in content
        or "agent_child_result" in content
    ):
        return None
    return content
