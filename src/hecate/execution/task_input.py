"""Supported human input contract shared by platform dispatch and wakes."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

TASK_USER_INPUT_SCHEMA_REF = "urn:hecate:task-user-input/0"


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
