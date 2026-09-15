"""Candidate content safety scanner.

Two layers: deterministic built-in patterns (prompt-injection markers and
credential shapes — always on) plus an optional injected DLPScanner for
policy-driven detection. Trajectory-poisoning defence: a candidate that
carries injection instructions or secrets never reaches the review queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+|any\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?(previous|prior|system)\s+(instructions|prompts?|rules)",
    r"reveal\s+(your\s+)?(system\s+)?prompt",
    r"you\s+are\s+now\s+(a|an|no longer)",
    r"print\s+your\s+(system\s+)?instructions",
)

_SECRET_PATTERNS = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\bsk-[A-Za-z0-9]{20,}\b",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"AIza[0-9A-Za-z_\-]{35}",
)

_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (kind, re.compile(pattern, re.IGNORECASE if kind == "injection" else 0))
    for kind, patterns in (("injection", _INJECTION_PATTERNS), ("secret", _SECRET_PATTERNS))
    for pattern in patterns
]


@dataclass
class ScanVerdict:
    """Result of scanning candidate content."""

    status: str  # clean | blocked
    findings: list[dict] = field(default_factory=list)


class CandidateContentScanner:
    """Scans candidate skill text before it may enter the review queue."""

    def __init__(self, dlp_scanner: Any | None = None) -> None:
        self._dlp_scanner = dlp_scanner

    async def scan(self, text: str) -> ScanVerdict:
        """Run built-in patterns and optional DLP over the candidate text."""
        findings: list[dict] = []

        for kind, pattern in _COMPILED:
            match = pattern.search(text)
            if match:
                findings.append({"kind": kind, "sample": _redact_sample(match.group(0))})

        if self._dlp_scanner is not None:
            try:
                result = self._dlp_scanner.scan(text, "ingress")
                if getattr(result, "action", None) is not None and str(result.action).lower().endswith("block"):
                    findings.append({"kind": "dlp_block", "sample": ""})
            except Exception:  # pragma: no cover - DLP misconfig must not hang the gate
                findings.append({"kind": "dlp_error", "sample": ""})

        return ScanVerdict(status="blocked" if findings else "clean", findings=findings)


def _redact_sample(sample: str) -> str:
    """Keep a short, non-replayable snippet for audit display."""
    trimmed = sample[:60]
    return trimmed[:40] + "…" if len(trimmed) > 40 else trimmed
