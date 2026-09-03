"""Tests for DLPScanner.

Covers spec §dlp-scanner three-layer orchestration:
* Empty findings → ALLOW with original text.
* Single finding × each action (MASK, BLOCK, AUDIT) → correct text.
* Multiple findings → most-restrictive action wins (BLOCK > MASK > AUDIT > ALLOW).
* Per-finding masking — only MASK-action findings are replaced.
* Audit data populated regardless of action.
"""

from __future__ import annotations

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


def _registry(*recognizers: DLPRecognizer) -> DLPRecognizerRegistry:
    registry = DLPRecognizerRegistry()
    for recognizer in recognizers:
        registry.register(recognizer)
    return registry


def _policy(*rules: DLPPolicyRule) -> DLPPolicyResolver:
    return DLPPolicyResolver(list(rules))


def _policy_for(mapping: dict[str, DLPAction], direction: str = "llm_output") -> DLPPolicyResolver:
    return DLPPolicyResolver(
        [
            DLPPolicyRule(
                entity_type=entity_type,
                direction=direction,
                action=action,
                scope=PolicyScope.DEFAULT,
            )
            for entity_type, action in mapping.items()
        ]
    )


def _finding(
    entity_type: str,
    value: str,
    start: int,
    end: int,
    recognizer: str = "stub",
    score: float = 1.0,
) -> DLPFinding:
    return DLPFinding(
        entity_type=entity_type,
        value=value,
        start=start,
        end=end,
        score=score,
        recognizer=recognizer,
    )


class TestDLPScannerEmptyAndAllow:
    def test_no_findings_returns_allow(self) -> None:
        scanner = DLPScanner(_registry(), _policy())
        result = scanner.scan("clean text", "llm_output")
        assert result.action == DLPAction.ALLOW
        assert result.text == "clean text"
        assert result.findings == []
        assert result.audit_data == []

    def test_empty_text_returns_allow(self) -> None:
        scanner = DLPScanner(_registry(), _policy())
        result = scanner.scan("", "llm_output")
        assert result.action == DLPAction.ALLOW
        assert result.text == ""


class TestDLPScannerMask:
    def test_single_mask_replaces_with_placeholder(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "user@example.com", 4, 20)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.MASK}))
        result = scanner.scan("see user@example.com today", "llm_output")
        assert result.action == DLPAction.MASK
        assert result.text == "see [EMAIL] today"
        assert "user@example.com" not in result.text

    def test_mask_with_no_mask_format_uses_entity_type(self) -> None:
        rec = _StubRecognizer("r", ["SSN"], [_finding("SSN", "123-45-6789", 0, 11)])
        scanner = DLPScanner(_registry(rec), _policy_for({"SSN": DLPAction.MASK}))
        result = scanner.scan("123-45-6789 here", "llm_output")
        assert result.text == "[SSN] here"

    def test_multiple_mask_replacements(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [
                _finding("EMAIL", "a@b.com", 0, 7),
                _finding("EMAIL", "c@d.com", 12, 19),
            ],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.MASK}))
        result = scanner.scan("a@b.com and c@d.com", "llm_output")
        assert result.text == "[EMAIL] and [EMAIL]"

    def test_mask_action_with_no_resolved_mask_returns_alone(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "user@example.com", 0, 16)],
        )
        # Policy says ALLOW for EMAIL — the per-finding action is ALLOW.
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.ALLOW}))
        result = scanner.scan("user@example.com", "llm_output")
        assert result.action == DLPAction.ALLOW
        assert result.text == "user@example.com"


class TestDLPScannerBlock:
    def test_single_block_withholds_text(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["AWS_ACCESS_KEY"],
            [_finding("AWS_ACCESS_KEY", "AKIAEXAMPLE", 0, 11)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"AWS_ACCESS_KEY": DLPAction.BLOCK}))
        result = scanner.scan("AKIAEXAMPLE here", "llm_output")
        assert result.action == DLPAction.BLOCK
        assert result.text is None
        assert len(result.findings) == 1
        assert len(result.audit_data) == 1

    def test_block_wins_over_mask(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL", "AWS_ACCESS_KEY"],
            [
                _finding("EMAIL", "user@example.com", 0, 16),
                _finding("AWS_ACCESS_KEY", "AKIA", 21, 25),
            ],
        )
        scanner = DLPScanner(
            _registry(rec),
            _policy_for({"EMAIL": DLPAction.MASK, "AWS_ACCESS_KEY": DLPAction.BLOCK}),
        )
        result = scanner.scan("user@example.com AKIA", "llm_output")
        assert result.action == DLPAction.BLOCK
        assert result.text is None


class TestDLPScannerAudit:
    def test_audit_keeps_original_text(self) -> None:
        rec = _StubRecognizer("r", ["EMAIL"], [_finding("EMAIL", "u@e.com", 0, 7)])
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.AUDIT}))
        result = scanner.scan("u@e.com here", "llm_output")
        assert result.action == DLPAction.AUDIT
        assert result.text == "u@e.com here"
        assert len(result.audit_data) == 1

    def test_audit_data_includes_action(self) -> None:
        rec = _StubRecognizer("r", ["EMAIL"], [_finding("EMAIL", "u@e.com", 0, 7, recognizer="r")])
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.AUDIT}))
        result = scanner.scan("u@e.com here", "llm_output")
        audit_record = result.audit_data[0]
        assert audit_record["entity_type"] == "EMAIL"
        assert audit_record["value"] == "u@e.com"
        assert audit_record["action"] == "audit"
        assert audit_record["recognizer"] == "r"


class TestDLPScannerSeverityRanking:
    def test_block_wins_over_mask(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL", "AWS_KEY"],
            [
                _finding("EMAIL", "u@e.com", 0, 7),
                _finding("AWS_KEY", "AKIA", 12, 16),
            ],
        )
        scanner = DLPScanner(
            _registry(rec),
            _policy_for({"EMAIL": DLPAction.MASK, "AWS_KEY": DLPAction.BLOCK}),
        )
        result = scanner.scan("u@e.com AKIA", "llm_output")
        assert result.action == DLPAction.BLOCK

    def test_mask_wins_over_audit(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL", "PHONE"],
            [
                _finding("EMAIL", "u@e.com", 0, 7),
                _finding("PHONE", "555-1234", 12, 20),
            ],
        )
        scanner = DLPScanner(
            _registry(rec),
            _policy_for({"EMAIL": DLPAction.MASK, "PHONE": DLPAction.AUDIT}),
        )
        result = scanner.scan("u@e.com 555-1234", "llm_output")
        assert result.action == DLPAction.MASK
        assert result.text is not None
        assert "[EMAIL]" in result.text
        assert "555-1234" in result.text

    def test_audit_wins_over_allow(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL", "PHONE"],
            [
                _finding("EMAIL", "u@e.com", 0, 7),
                _finding("PHONE", "555-1234", 12, 20),
            ],
        )
        scanner = DLPScanner(
            _registry(rec),
            _policy_for({"EMAIL": DLPAction.AUDIT, "PHONE": DLPAction.ALLOW}),
        )
        result = scanner.scan("u@e.com 555-1234", "llm_output")
        assert result.action == DLPAction.AUDIT
        assert result.text == "u@e.com 555-1234"

    def test_all_allow_returns_allow(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL", "PHONE"],
            [
                _finding("EMAIL", "u@e.com", 0, 7),
                _finding("PHONE", "555-1234", 12, 20),
            ],
        )
        scanner = DLPScanner(
            _registry(rec),
            _policy_for({"EMAIL": DLPAction.ALLOW, "PHONE": DLPAction.ALLOW}),
        )
        result = scanner.scan("u@e.com 555-1234", "llm_output")
        assert result.action == DLPAction.ALLOW
        assert result.text == "u@e.com 555-1234"


class TestDLPScannerPerFindingActions:
    def test_only_mask_findings_get_masked(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL", "PHONE"],
            [
                _finding("EMAIL", "u@e.com", 0, 7),
                _finding("PHONE", "555-1234", 12, 20),
            ],
        )
        scanner = DLPScanner(
            _registry(rec),
            _policy_for({"EMAIL": DLPAction.MASK, "PHONE": DLPAction.ALLOW}),
        )
        result = scanner.scan("u@e.com 555-1234", "llm_output")
        assert result.text is not None
        assert "[EMAIL]" in result.text
        assert "555-1234" in result.text


class TestDLPScannerScopeWiring:
    def test_scan_passes_scope_to_policy(self) -> None:
        rec = _StubRecognizer("r", ["EMAIL"], [_finding("EMAIL", "u@e.com", 0, 7)])
        scanner = DLPScanner(
            _registry(rec),
            _policy(
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.BLOCK,
                    scope=PolicyScope.AGENT,
                    scope_id="a1",
                ),
            ),
        )
        result = scanner.scan(
            "u@e.com",
            "llm_output",
            agent_id="a1",
            workspace_id="w1",
        )
        assert result.action == DLPAction.BLOCK

    def test_registry_property_returns_registry(self) -> None:
        registry = _registry()
        scanner = DLPScanner(registry, _policy())
        assert scanner.registry is registry

    def test_policy_property_returns_policy(self) -> None:
        policy = _policy()
        scanner = DLPScanner(_registry(), policy)
        assert scanner.policy is policy
