"""Supported human input contract shared by platform dispatch and wakes."""

from __future__ import annotations

from typing import Any

TASK_USER_INPUT_SCHEMA_REF = "urn:hecate:task-user-input/0"


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
