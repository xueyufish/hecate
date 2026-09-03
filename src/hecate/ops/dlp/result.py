"""Core DLP data types — action enum, finding record, scan result.

Defines the three primitive types every DLP subsystem exchanges:

* :class:`DLPAction` — the enforcement decision an Operator returns.
* :class:`DLPFinding` — a single detection emitted by a Recognizer.
* :class:`DLPResult` — the aggregated output of a full :class:`DLPScanner`
  scan, including the resolved action and any audit metadata.

Severity ordering follows design.md §D6: ``BLOCK > MASK > AUDIT > ALLOW``.
The aggregator :meth:`DLPAction.overall_action` returns the most
restrictive action from an iterable, matching spec §dlp-scanner
"Most restrictive wins".

``SANITIZE`` is an output-guardrail-only action (9.1a / 9.2): it sits
between AUDIT and MASK in restrictiveness per the injection-detection
spec ordering ``BLOCK > MASK > SANITIZE > AUDIT > ALLOW``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class DLPAction(StrEnum):
    """Enforcement decision for a DLP scan.

    Members are ordered by restrictiveness for introspection but the
    comparison must go through :meth:`severity` — Python's enum
    declaration order is not part of the public contract.
    """

    ALLOW = "allow"
    AUDIT = "audit"
    SANITIZE = "sanitize"
    MASK = "mask"
    BLOCK = "block"

    @property
    def severity(self) -> int:
        """Return a numeric rank where higher means more restrictive.

        ``ALLOW=0 < AUDIT=1 < SANITIZE=2 < MASK=3 < BLOCK=4``.
        """
        return _SEVERITY[self]

    @classmethod
    def overall_action(cls, actions: Iterable[DLPAction]) -> DLPAction:
        """Return the most restrictive action from ``actions``.

        Empty input is treated as ``ALLOW`` (fail-open default; see
        design.md §D5). Ordering: BLOCK > MASK > SANITIZE > AUDIT > ALLOW.
        """
        materialized = set(actions)
        if not materialized:
            return cls.ALLOW
        return max(materialized, key=lambda action: action.severity)


_SEVERITY: dict[DLPAction, int] = {
    DLPAction.ALLOW: 0,
    DLPAction.AUDIT: 1,
    DLPAction.SANITIZE: 2,
    DLPAction.MASK: 3,
    DLPAction.BLOCK: 4,
}


@dataclass(frozen=True)
class DLPFinding:
    """A single sensitive-entity detection produced by a Recognizer.

    Attributes:
        entity_type: Canonical entity name (e.g. ``"EMAIL"``, ``"AWS_ACCESS_KEY"``).
        value: The matched substring from the scanned text.
        start: Inclusive character offset of the match in the original text.
        end: Exclusive character offset of the match in the original text.
        score: Confidence score in ``[0.0, 1.0]``. ``1.0`` for deterministic
            regex matches; lower for ML-based recognizers (e.g. Presidio).
        recognizer: Name of the recognizer that emitted this finding. Used
            for deduplication and audit reporting.
    """

    entity_type: str
    value: str
    start: int
    end: int
    score: float
    recognizer: str


@dataclass
class DLPResult:
    """Aggregated output of a :class:`DLPScanner` scan.

    Attributes:
        findings: All detections collected across the registered
            recognizers after deduplication and entity-type filtering.
        action: The resolved enforcement decision. When ``BLOCK``,
            ``text`` is ``None`` (content withheld). When ``MASK``,
            ``text`` contains the masked version. When ``AUDIT`` or
            ``ALLOW``, ``text`` is the original content.
        text: The (possibly masked) text returned to the caller.
            ``None`` iff ``action == BLOCK``.
        audit_data: Per-finding metadata records suitable for writing
            to ``SecurityFindingModel``. Populated regardless of action
            so audit-mode findings are recorded.
    """

    findings: list[DLPFinding] = field(default_factory=list)
    action: DLPAction = DLPAction.ALLOW
    text: str | None = None
    audit_data: list[dict[str, object]] = field(default_factory=list)
