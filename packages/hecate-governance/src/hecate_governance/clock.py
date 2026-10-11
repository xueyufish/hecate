"""Trusted time for approval-expiry judgments (step7, task 3.5).

A binding's expiry judgment is only as good as the clock that evaluates it.
Every approval authority keeps a time anchor at issuance (wall clock — already
persisted with the binding — plus the process-local monotonic reading). A
dispatch that depends on an expiry verdict is rejected when time cannot be
trusted:

- **clock rollback** — current wall time is before the anchor wall time beyond
  tolerance (detectable across restarts via the persisted anchor);
- **restart** — the monotonic reading moved backwards (the anchor's
  monotonic continuity is lost), so elapsed-time checks are unverifiable;
- **wall/monotonic divergence** — within one process the wall clock moved by
  more than the tolerance relative to the monotonic clock.

Bindings without an expiry are not expiry-dependent and stay unaffected.
Local (standalone host) approvals use the same rule — never relaxed, never
deferred to managed-mode certification.
"""

from __future__ import annotations

import time as _time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_TOLERANCE_SECONDS = 300.0
PROCESS_EPOCH = str(uuid.uuid4())


@dataclass(frozen=True)
class TimeAnchor:
    """Issuance-time anchor: wall clock plus process-local monotonic reading."""

    wall: datetime
    monotonic: float
    epoch: str = PROCESS_EPOCH


def validate_clock(
    anchor: TimeAnchor,
    *,
    now_wall: datetime,
    now_monotonic: float,
    tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
) -> str | None:
    """Return the untrusted-time reason, or ``None`` when time is trusted."""
    if anchor.epoch != PROCESS_EPOCH:
        return "process restarted: issuance clock continuity cannot be established"
    if now_wall.tzinfo is None:
        now_wall = now_wall.replace(tzinfo=UTC)
    anchor_wall = anchor.wall if anchor.wall.tzinfo else anchor.wall.replace(tzinfo=UTC)
    if (now_wall - anchor_wall).total_seconds() < -tolerance_seconds:
        return "clock moved backwards relative to the issuance anchor"
    if now_monotonic < anchor.monotonic:
        return "process restarted: monotonic anchor lost, elapsed time unverifiable"
    wall_elapsed = (now_wall - anchor_wall).total_seconds()
    mono_elapsed = now_monotonic - anchor.monotonic
    if abs(wall_elapsed - mono_elapsed) > tolerance_seconds:
        return "wall clock diverged from the monotonic clock beyond tolerance"
    return None


class TrustedClock:
    """Process clock that issues and validates anchors."""

    def __init__(
        self,
        *,
        now_fn: Callable[[], datetime] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        tolerance_seconds: float = DEFAULT_TOLERANCE_SECONDS,
    ) -> None:
        self._now_fn = now_fn or (lambda: datetime.now(UTC))
        self._mono_fn = monotonic_fn or _time.monotonic
        self.tolerance_seconds = tolerance_seconds

    def anchor(self) -> TimeAnchor:
        return TimeAnchor(wall=self._now_fn(), monotonic=self._mono_fn())

    def trusted_now(self, anchor: TimeAnchor) -> tuple[datetime, str | None]:
        """Return ``(current wall time, untrusted reason or None)``."""
        now = self._now_fn()
        reason = validate_clock(
            anchor,
            now_wall=now,
            now_monotonic=self._mono_fn(),
            tolerance_seconds=self.tolerance_seconds,
        )
        return now, reason
