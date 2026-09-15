"""Candidate template safety scanner for the prompt optimization pipeline.

Same two-layer defence as the skill-evolution scanner: deterministic
prompt-injection and secret-shape patterns (always on) plus an optional
injected DLP scanner for policy-driven detection. A candidate template
learned from rollout trajectories could carry injection instructions or
secrets from a polluted dataset — blocked candidates never reach the
review queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    """Result of scanning a candidate template."""

    status: str  # clean | blocked
    findings: list[dict] = field(default_factory=list)


class PromptCandidateScanner:
    """Scans candidate prompt templates before they may enter review."""

    def __init__(self, dlp_scanner: object | None = None) -> None:
        self._dlp_scanner = dlp_scanner

    async def scan(self, template: str) -> ScanVerdict:
        """Return a verdict; ``blocked`` candidates carry finding entries."""
        findings: list[dict] = []
        for kind, pattern in _COMPILED:
            match = pattern.search(template)
            if match:
                findings.append({"kind": kind, "excerpt": match.group(0)[:80]})
        if self._dlp_scanner is not None:
            try:
                dlp_findings = await self._dlp_scanner.analyze(template)  # noqa: B009 — duck-typed optional scanner
                for finding in dlp_findings or []:
                    findings.append({"kind": "dlp", "detail": str(finding)[:200]})
            except Exception:  # noqa: BLE001 — scanner failure must not block clean candidates silently
                findings.append({"kind": "scanner_error", "detail": "optional DLP scan failed"})
        return ScanVerdict(status="blocked" if findings else "clean", findings=findings)
