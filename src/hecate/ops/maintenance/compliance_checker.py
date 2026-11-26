"""Compliance checker agent for scanning architecture violations.

Runs code style checks via ruff and security configuration audits,
producing a report of violations and recommendations without applying fixes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_RUFF_LINE_RE = re.compile(r"^(.+?):(\d+):(\d+):\s+([A-Z]\d+)\s*(?:\[\*\])?\s*(.*)$")


@dataclass
class Violation:
    """A single compliance violation."""

    rule_id: str
    severity: str  # "error" | "warning"
    message: str
    file_path: str | None = None
    fix_suggestion: str | None = None


@dataclass
class ComplianceReport:
    """Aggregated compliance report."""

    error_count: int = 0
    warning_count: int = 0
    violations: list[Violation] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))


_ERROR_CODES = {"E", "F", "B", "S", "N", "UP"}
_WARNING_CODES = {"W", "I", "SIM"}


class ComplianceCheckerAgent:
    """Scans for code style and security configuration violations."""

    async def check_code_style(self, project_root: Path | None = None) -> list[Violation]:
        """Run ruff check and parse output into violations."""
        root = project_root or Path.cwd()
        violations: list[Violation] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "ruff",
                "check",
                "src/hecate/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(root),
            )
            stdout, _ = await proc.communicate()
            for line in stdout.decode().strip().splitlines():
                m = _RUFF_LINE_RE.match(line.strip())
                if not m:
                    continue
                file_path, _line, _col, code, msg = m.groups()
                prefix = code[0]
                severity = "error" if prefix in _ERROR_CODES else "warning"
                violations.append(
                    Violation(
                        rule_id=code,
                        severity=severity,
                        message=msg.strip(),
                        file_path=file_path,
                    )
                )
        except FileNotFoundError:
            logger.warning("ruff not found on PATH — skipping code style check")
        return violations

    async def check_security_config(self) -> list[Violation]:
        """Check security-related environment configuration."""
        violations: list[Violation] = []

        if not os.environ.get("LLM_GUARD_ENABLED"):
            violations.append(
                Violation(
                    rule_id="SEC001",
                    severity="warning",
                    message="LLM_GUARD_ENABLED not set — prompt scanning disabled",
                    fix_suggestion="Set LLM_GUARD_ENABLED=true in .env",
                )
            )

        if not os.environ.get("RATE_LIMIT_RPM"):
            violations.append(
                Violation(
                    rule_id="SEC002",
                    severity="warning",
                    message="RATE_LIMIT_RPM not set — no rate limiting configured",
                    fix_suggestion="Set RATE_LIMIT_RPM=60 in .env",
                )
            )

        if not os.environ.get("HECATE_API_KEYS"):
            violations.append(
                Violation(
                    rule_id="SEC003",
                    severity="error",
                    message="HECATE_API_KEYS not set — API is unauthenticated",
                    fix_suggestion="Set HECATE_API_KEYS=key1,key2 in .env",
                )
            )

        return violations

    async def generate_compliance_report(self, project_root: Path | None = None) -> ComplianceReport:
        """Run all compliance checks and build a report."""
        style_violations = await self.check_code_style(project_root)
        security_violations = await self.check_security_config()
        all_violations = style_violations + security_violations

        report = ComplianceReport(
            error_count=sum(1 for v in all_violations if v.severity == "error"),
            warning_count=sum(1 for v in all_violations if v.severity == "warning"),
            violations=all_violations,
        )
        logger.info(
            "Compliance report: %d errors, %d warnings",
            report.error_count,
            report.warning_count,
        )
        return report

    async def run(self, project_root: Path | None = None) -> ComplianceReport:
        """Convenience method to run all checks and return a report."""
        return await self.generate_compliance_report(project_root)
