"""Tests for EgressFilter ABC and DLPEgressFilter."""

from __future__ import annotations

import pytest

from hecate.ops.dlp.policy import (
    DLPPolicyResolver,
    DLPPolicyRule,
    PolicyScope,
)
from hecate.ops.dlp.recognizer import (
    DLPRecognizer,
    DLPRecognizerRegistry,
)
from hecate.ops.dlp.result import DLPAction, DLPFinding
from hecate.ops.dlp.scanner import DLPScanner
from hecate.runtime.security.egress import (
    DLPEgressFilter,
    EgressAction,
    EgressFilter,
    EgressResult,
)


class _StubRecognizer(DLPRecognizer):
    def __init__(
        self,
        name: str,
        entities: list[str],
        findings: list[DLPFinding],
    ) -> None:
        self.name = name
        self.supported_entities = entities
        self._findings = findings

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        if entities is None:
            return list(self._findings)
        return [f for f in self._findings if f.entity_type in entities]


def _scanner(policy: DLPPolicyResolver, *recognizers: DLPRecognizer) -> DLPScanner:
    registry = DLPRecognizerRegistry()
    for recognizer in recognizers:
        registry.register(recognizer)
    return DLPScanner(registry, policy)


def _policy(*rules: DLPPolicyRule) -> DLPPolicyResolver:
    return DLPPolicyResolver(list(rules))


def _finding(
    entity_type: str,
    value: str,
    start: int,
    end: int,
) -> DLPFinding:
    return DLPFinding(
        entity_type=entity_type,
        value=value,
        start=start,
        end=end,
        score=1.0,
        recognizer="stub",
    )


class TestEgressFilterABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            EgressFilter()  # type: ignore[abstract]

    def test_subclass_works(self) -> None:
        class _NullFilter(EgressFilter):
            async def filter(
                self,
                content: str | bytes | object,
                context: dict | None = None,
            ) -> EgressResult:
                return EgressResult(
                    action=EgressAction.ALLOW,
                    content=content,
                )

        f = _NullFilter()
        assert isinstance(f, EgressFilter)


class TestEgressResultDataclass:
    def test_defaults(self) -> None:
        result = EgressResult(action=EgressAction.ALLOW, content="hello")
        assert result.findings == []
        assert result.audit_data == []

    def test_holds_findings(self) -> None:
        finding = _finding("EMAIL", "u@e.com", 0, 7)
        result = EgressResult(
            action=EgressAction.MODIFIED,
            content="[EMAIL]",
            findings=[finding],
            audit_data=[{"entity_type": "EMAIL"}],
        )
        assert result.findings == [finding]
        assert result.audit_data == [{"entity_type": "EMAIL"}]

    def test_block_content_is_none(self) -> None:
        result = EgressResult(action=EgressAction.BLOCK, content=None)
        assert result.content is None


class TestEgressActionEnum:
    def test_three_members(self) -> None:
        assert len(EgressAction) == 3

    def test_string_values(self) -> None:
        assert EgressAction.ALLOW.value == "allow"
        assert EgressAction.BLOCK.value == "block"
        assert EgressAction.MODIFIED.value == "modified"


class TestDLPEgressFilterTextScanning:
    async def test_allow_passes_text_through(self) -> None:
        policy = _policy(
            DLPPolicyRule(
                entity_type="EMAIL",
                direction="tool_output",
                action=DLPAction.AUDIT,
                scope=PolicyScope.DEFAULT,
            )
        )
        scanner = _scanner(policy)
        f = DLPEgressFilter(scanner)
        result = await f.filter("see user@example.com here")
        assert result.action == EgressAction.ALLOW
        assert result.content == "see user@example.com here"

    async def test_mask_returns_modified_action(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 13)],
        )
        policy = _policy(
            DLPPolicyRule(
                entity_type="EMAIL",
                direction="tool_output",
                action=DLPAction.MASK,
                scope=PolicyScope.DEFAULT,
            )
        )
        scanner = _scanner(policy, rec)
        f = DLPEgressFilter(scanner)
        result = await f.filter("see u@e.com here")
        assert result.action == EgressAction.MODIFIED
        assert result.content is not None
        assert "[EMAIL]" in result.content
        assert "u@e.com" not in result.content

    async def test_block_returns_none_content(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["AWS_KEY"],
            [_finding("AWS_KEY", "AKIA", 0, 4)],
        )
        policy = _policy(
            DLPPolicyRule(
                entity_type="AWS_KEY",
                direction="tool_output",
                action=DLPAction.BLOCK,
                scope=PolicyScope.DEFAULT,
            )
        )
        scanner = _scanner(policy, rec)
        f = DLPEgressFilter(scanner)
        result = await f.filter("AKIA secret stuff")
        assert result.action == EgressAction.BLOCK
        assert result.content is None

    async def test_audit_passes_through_with_audit_data(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 13)],
        )
        policy = _policy(
            DLPPolicyRule(
                entity_type="EMAIL",
                direction="tool_output",
                action=DLPAction.AUDIT,
                scope=PolicyScope.DEFAULT,
            )
        )
        scanner = _scanner(policy, rec)
        f = DLPEgressFilter(scanner)
        result = await f.filter("see u@e.com here")
        assert result.action == EgressAction.ALLOW
        assert result.content == "see u@e.com here"
        assert len(result.audit_data) >= 1
        assert result.findings[0].entity_type == "EMAIL"


class TestDLPEgressFilterNonText:
    async def test_bytes_pass_through_with_audit(self) -> None:
        policy = _policy()
        scanner = _scanner(policy)
        f = DLPEgressFilter(scanner)
        data = b"\x00\x01\x02binary"
        result = await f.filter(data)
        assert result.action == EgressAction.ALLOW
        assert result.content is data
        assert any(record.get("reason") == "non_text_content" for record in result.audit_data)

    async def test_dict_pass_through_with_audit(self) -> None:
        policy = _policy()
        scanner = _scanner(policy)
        f = DLPEgressFilter(scanner)
        data = {"key": "value", "nested": [1, 2, 3]}
        result = await f.filter(data)
        assert result.action == EgressAction.ALLOW
        assert result.content is data
        assert any(record.get("reason") == "non_text_content" for record in result.audit_data)


class TestDLPEgressFilterConfiguration:
    def test_scanner_property(self) -> None:
        scanner = _scanner(_policy())
        f = DLPEgressFilter(scanner)
        assert f.scanner is scanner

    def test_default_direction(self) -> None:
        scanner = _scanner(_policy())
        f = DLPEgressFilter(scanner)
        assert f._direction == "tool_output"
