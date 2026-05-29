"""Unit tests for ComplianceCheckerAgent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from hecate.services.meta_agents.compliance_checker import (
    ComplianceCheckerAgent,
    ComplianceReport,
)


@pytest.fixture
def agent() -> ComplianceCheckerAgent:
    return ComplianceCheckerAgent()


async def test_check_code_style_parses_ruff_output(agent: ComplianceCheckerAgent) -> None:
    ruff_output = (
        "src/hecate/foo.py:10:5: F401 [*] Unused import os\nsrc/hecate/bar.py:20:1: W291 Trailing whitespace\n"
    )
    mock_proc = AsyncMock()
    mock_proc.communicate.return_value = (ruff_output.encode(), b"")
    mock_proc.returncode = 0

    with patch("hecate.services.meta_agents.compliance_checker.asyncio.create_subprocess_exec", return_value=mock_proc):
        violations = await agent.check_code_style(Path("/fake"))

    assert len(violations) == 2
    assert violations[0].rule_id == "F401"
    assert violations[0].severity == "error"
    assert violations[1].rule_id == "W291"
    assert violations[1].severity == "warning"


async def test_check_code_style_handles_missing_ruff(agent: ComplianceCheckerAgent) -> None:
    with patch(
        "hecate.services.meta_agents.compliance_checker.asyncio.create_subprocess_exec",
        side_effect=FileNotFoundError,
    ):
        violations = await agent.check_code_style()
        assert violations == []


async def test_check_security_config_missing_keys(agent: ComplianceCheckerAgent) -> None:
    with patch.dict("os.environ", {}, clear=True):
        violations = await agent.check_security_config()
    rule_ids = [v.rule_id for v in violations]
    assert "SEC001" in rule_ids
    assert "SEC002" in rule_ids
    assert "SEC003" in rule_ids


async def test_check_security_config_all_set(agent: ComplianceCheckerAgent) -> None:
    env = {"LLM_GUARD_ENABLED": "true", "RATE_LIMIT_RPM": "60", "HECATE_API_KEYS": "key1"}
    with patch.dict("os.environ", env, clear=True):
        violations = await agent.check_security_config()
        assert len(violations) == 0


async def test_generate_compliance_report(agent: ComplianceCheckerAgent) -> None:
    with patch.dict("os.environ", {"HECATE_API_KEYS": "key1"}, clear=True), \
         patch.object(agent, "check_code_style", return_value=[]):
        report = await agent.generate_compliance_report()
    assert isinstance(report, ComplianceReport)
    assert report.checked_at is not None


async def test_run_convenience(agent: ComplianceCheckerAgent) -> None:
    with patch.dict("os.environ", {"HECATE_API_KEYS": "key1"}, clear=True), \
         patch.object(agent, "check_code_style", return_value=[]):
        report = await agent.run()
    assert isinstance(report, ComplianceReport)
