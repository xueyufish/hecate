"""Tests for DLP core data types: DLPAction, DLPFinding, DLPResult.

Covers spec §dlp-scanner ADDED Requirements for the data type layer:
* Four DLPAction members with string equality.
* overall_action() returns the most restrictive action (BLOCK > MASK
  > AUDIT > ALLOW).
* DLPFinding field accessibility.
* DLPResult state combinations (empty→ALLOW, BLOCK→text=None,
  MASK→text masked, AUDIT→text+audit_data).
"""

from __future__ import annotations

import dataclasses

import pytest

from hecate.ops.dlp.result import DLPAction, DLPFinding, DLPResult


class TestDLPAction:
    def test_member_count(self) -> None:
        # 9.1a/9.2 added SANITIZE (output-guardrail-only action) to the original four.
        assert len(DLPAction) == 5

    def test_string_equality(self) -> None:
        assert DLPAction.ALLOW == "allow"  # type: ignore[comparison-overlap]
        assert DLPAction.BLOCK == "block"  # type: ignore[comparison-overlap]
        assert DLPAction.MASK == "mask"  # type: ignore[comparison-overlap]
        assert DLPAction.AUDIT == "audit"  # type: ignore[comparison-overlap]
        assert DLPAction.SANITIZE == "sanitize"  # type: ignore[comparison-overlap]

    def test_member_values(self) -> None:
        assert DLPAction.ALLOW.value == "allow"
        assert DLPAction.BLOCK.value == "block"
        assert DLPAction.MASK.value == "mask"
        assert DLPAction.AUDIT.value == "audit"
        assert DLPAction.SANITIZE.value == "sanitize"

    def test_severity_ordering(self) -> None:
        assert DLPAction.ALLOW.severity < DLPAction.AUDIT.severity
        assert DLPAction.AUDIT.severity < DLPAction.SANITIZE.severity
        assert DLPAction.SANITIZE.severity < DLPAction.MASK.severity
        assert DLPAction.MASK.severity < DLPAction.BLOCK.severity

    def test_overall_action_returns_most_restrictive(self) -> None:
        result = DLPAction.overall_action([DLPAction.MASK, DLPAction.BLOCK, DLPAction.AUDIT, DLPAction.ALLOW])
        assert result == DLPAction.BLOCK

    def test_overall_action_with_single_member(self) -> None:
        assert DLPAction.overall_action([DLPAction.MASK]) == DLPAction.MASK
        assert DLPAction.overall_action([DLPAction.ALLOW]) == DLPAction.ALLOW

    def test_overall_action_empty_returns_allow(self) -> None:
        assert DLPAction.overall_action([]) == DLPAction.ALLOW

    def test_overall_action_with_duplicates(self) -> None:
        result = DLPAction.overall_action([DLPAction.AUDIT, DLPAction.AUDIT, DLPAction.MASK])
        assert result == DLPAction.MASK


class TestDLPFinding:
    def test_fields_accessible(self) -> None:
        finding = DLPFinding(
            entity_type="EMAIL",
            value="john@example.com",
            start=10,
            end=26,
            score=0.95,
            recognizer="regex_pii",
        )
        assert finding.entity_type == "EMAIL"
        assert finding.value == "john@example.com"
        assert finding.start == 10
        assert finding.end == 26
        assert finding.score == 0.95
        assert finding.recognizer == "regex_pii"

    def test_frozen_dataclass_immutable(self) -> None:
        finding = DLPFinding(
            entity_type="SSN",
            value="123-45-6789",
            start=0,
            end=11,
            score=1.0,
            recognizer="regex",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.score = 0.5


class TestDLPResult:
    def test_defaults_empty_allow(self) -> None:
        result = DLPResult()
        assert result.findings == []
        assert result.action == DLPAction.ALLOW
        assert result.text is None
        assert result.audit_data == []

    def test_empty_findings_allow(self) -> None:
        result = DLPResult(
            findings=[],
            action=DLPAction.ALLOW,
            text="clean text",
            audit_data=[],
        )
        assert result.action == DLPAction.ALLOW
        assert result.text == "clean text"

    def test_block_action_withholds_text(self) -> None:
        finding = DLPFinding(
            entity_type="AWS_ACCESS_KEY",
            value="AKIA...",
            start=0,
            end=20,
            score=1.0,
            recognizer="secrets",
        )
        result = DLPResult(
            findings=[finding],
            action=DLPAction.BLOCK,
            text=None,
            audit_data=[{"entity_type": "AWS_ACCESS_KEY"}],
        )
        assert result.action == DLPAction.BLOCK
        assert result.text is None
        assert len(result.findings) == 1
        assert len(result.audit_data) == 1

    def test_mask_action_has_text(self) -> None:
        finding = DLPFinding(
            entity_type="EMAIL",
            value="user@example.com",
            start=10,
            end=26,
            score=0.95,
            recognizer="regex",
        )
        result = DLPResult(
            findings=[finding],
            action=DLPAction.MASK,
            text="Contact [EMAIL] for details",
            audit_data=[],
        )
        assert result.action == DLPAction.MASK
        assert result.text is not None
        assert "[EMAIL]" in result.text
        assert "user@example.com" not in result.text

    def test_audit_action_preserves_text(self) -> None:
        finding = DLPFinding(
            entity_type="EMAIL",
            value="user@example.com",
            start=0,
            end=16,
            score=0.95,
            recognizer="regex",
        )
        original = "user@example.com"
        result = DLPResult(
            findings=[finding],
            action=DLPAction.AUDIT,
            text=original,
            audit_data=[{"entity_type": "EMAIL", "value": "user@example.com"}],
        )
        assert result.action == DLPAction.AUDIT
        assert result.text == original
        assert len(result.audit_data) == 1
