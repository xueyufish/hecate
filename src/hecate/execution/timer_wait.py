"""Contracts for platform controller timer waits (persistent timed waiting).

A controller task may explicitly opt in (``controller.timer_wait``) to
honoring structured wait declarations returned by its readonly polling
tools: the tool result carries a versioned declaration asking the platform
to park the task and wake it when the declared duration has passed. Three
facts gate every declaration, and all three are re-checkable from durable
records — model natural language and undeclared tools can never trigger a
timed wait:

1. the task declared the opt-in (validated here, fail-fast at dispatch);
2. the result envelope matches the declared version exactly (no free-form
   fields, no arbitrary durations);
3. the host confirms the declaring action is ledger-recorded as readonly.

The parked wait binds Task, Run, accepted decision (via the deterministic
execution id) and the real action receipt: the absolute deadline is
anchored to the action's first durable receipt time, so a crash between
the receipt and the park commit re-derives the same deadline — recovery
never restarts the clock. The wake path (``timer_wait_wake``) consumes the
expired wait and requeues the task; timer passage is never business
progress: no input revision is recorded, and no-progress protection, turn
budgets and deadlines keep their accumulated values.

Supported scope: platform-local controller-loop tasks only. Runner,
Workflow generic timed waits, local Plan and Child Run waits are out of
scope. Unsupported hosts reject the opt-in before accepting execution.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

TIMER_WAIT_ENVELOPE_STATUS = "timer_wait_required"
TIMER_WAIT_DECLARATION_VERSION = 1
TIMER_WAIT_CONTRACT_KIND = "timer"
TIMER_WAIT_DEFAULT_MAX_WAITS = 16

_TIMER_WAIT_POLICY_KEYS = frozenset({"max_waits"})
_TIMER_WAIT_DECLARATION_KEYS = frozenset({"version", "wake_in_seconds", "reason", "subject"})


def timer_wait_policy(value: Any) -> dict[str, int] | None:
    """Validate the task-level opt-in; ``None`` keeps prior behavior.

    The declaration is a dict with an optional ``max_waits`` bound (the
    number of timer waits a task may honor; each park persists its count).
    Any other shape is a submission error, not a silent disable.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("controller.timer_wait must be a declaration object")
    unknown = set(value) - _TIMER_WAIT_POLICY_KEYS
    if unknown:
        raise ValueError(f"controller.timer_wait has unsupported fields: {sorted(unknown)}")
    max_waits = value.get("max_waits", TIMER_WAIT_DEFAULT_MAX_WAITS)
    if isinstance(max_waits, bool) or not isinstance(max_waits, int) or max_waits < 1:
        raise ValueError("controller.timer_wait.max_waits must be a positive integer")
    return {"max_waits": max_waits}


def timer_wait_declaration(value: Any) -> dict[str, Any] | None:
    """Recognize the explicit versioned wait envelope; anything else is data.

    Only ``{"status": "timer_wait_required", "wait": {version, wake_in_seconds,
    [reason], [subject]}}`` qualifies, with a positive finite representable
    duration. Unknown fields inside the versioned declaration are a contract
    violation (a future version must bump ``version``), and the envelope
    must arrive as a structured tool result — never as model text.
    """

    if not isinstance(value, dict) or value.get("status") != TIMER_WAIT_ENVELOPE_STATUS:
        return None
    declaration = value.get("wait")
    if not isinstance(declaration, dict) or set(declaration) - _TIMER_WAIT_DECLARATION_KEYS:
        return None
    if type(declaration.get("version")) is not int or declaration["version"] != TIMER_WAIT_DECLARATION_VERSION:
        return None
    seconds = declaration.get("wake_in_seconds")
    try:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds):
            raise ValueError
        duration = timedelta(seconds=seconds)
        if duration <= timedelta(0):
            raise ValueError
        datetime.now(UTC) + duration
    except (OverflowError, TypeError, ValueError):
        return None
    for key in ("reason", "subject"):
        raw = declaration.get(key)
        if raw is not None and (not isinstance(raw, str) or not raw.strip() or len(raw) > 200):
            return None
    return {"wake_seconds": float(seconds), "declaration": dict(declaration)}


def timer_wait_contract(value: Any) -> dict[str, Any] | None:
    """Recognize the persisted wait contract this package's wake path owns.

    The parked contract names the declaring action (tool, execution id,
    arguments digest), the honored declaration, and the declared duration
    the deadline was derived from. ``None`` means the wait is not a timer
    wait (or its contract is corrupt — callers must converge such records
    to reconciliation instead of guessing a wake).
    """

    if not isinstance(value, dict) or value.get("kind") != TIMER_WAIT_CONTRACT_KIND:
        return None
    required = ("tool", "execution_id", "arguments_digest", "wake_seconds", "declaration")
    if any(not value.get(key) for key in required):
        return None
    if not all(isinstance(value[key], str) for key in ("tool", "execution_id", "arguments_digest")):
        return None
    seconds = value.get("wake_seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        return None
    parsed = timer_wait_declaration({"status": TIMER_WAIT_ENVELOPE_STATUS, "wait": value["declaration"]})
    if parsed is None or parsed["wake_seconds"] != seconds:
        return None
    return value


def timer_deadline_for(anchor: datetime, wake_seconds: float, task_deadline: datetime | None) -> str:
    """Derive the absolute deadline from the receipt anchor; clamp to deadline.

    ``anchor`` is the declaring action's durable receipt time — the same
    value every recovery re-derives from, so the clock never restarts.
    """

    expires_at = anchor + timedelta(seconds=wake_seconds)
    if task_deadline is not None:
        expires_at = min(expires_at, task_deadline)
    return expires_at.isoformat()
