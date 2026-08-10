"""Tests for StreamingDLPWrapper.

Covers spec §dlp-streaming requirements:
* Buffer accumulation; ``process_chunk`` returns nothing until the
  threshold is hit.
* Threshold triggered scan emits the prefix, retains an overlap.
* BLOCK stops the stream immediately; subsequent calls return ``None``.
* MASK keeps streaming and replaces matched spans with placeholders.
* AUDIT/ALLOW continue streaming with the original text.
* ``finalize()`` drains the remaining buffer.
* Streaming artifacts (short buffer relative to overlap) are recorded
  in :attr:`corrections`.
"""

from __future__ import annotations

import pytest

from hecate.services.security.dlp.policy import (
    DLPPolicyResolver,
    DLPPolicyRule,
    PolicyScope,
)
from hecate.services.security.dlp.recognizer import (
    DLPRecognizer,
    DLPRecognizerRegistry,
)
from hecate.services.security.dlp.result import DLPAction, DLPFinding
from hecate.services.security.dlp.scanner import DLPScanner
from hecate.services.security.dlp.streaming import StreamingDLPWrapper


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


def _wrapper(
    scanner: DLPScanner,
    *,
    threshold: int = 300,
    overlap: int = 10,
) -> StreamingDLPWrapper:
    return StreamingDLPWrapper(
        scanner,
        threshold=threshold,
        overlap=overlap,
    )


class TestStreamingBufferAccumulation:
    def test_empty_stream_returns_empty(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({"EMAIL": DLPAction.MASK}))
        stream = _wrapper(scanner)
        assert stream.process_chunk("") == ""

    def test_chunk_below_threshold_returns_empty(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({"EMAIL": DLPAction.MASK}))
        stream = _wrapper(scanner)
        assert stream.process_chunk("short text") == ""

    def test_reaching_threshold_triggers_emit(self) -> None:
        rec = _StubRecognizer("r", ["EMAIL"], [])
        scanner = DLPScanner(_registry(rec), _policy_for({}))
        stream = _wrapper(scanner)
        assert stream.process_chunk("a" * 300) == "a" * 290


class TestStreamingMask:
    def test_mask_replaces_email_with_placeholder(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 11)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.MASK}))
        stream = _wrapper(scanner, threshold=20, overlap=2)
        emit = stream.process_chunk("see u@e.com today " + "x" * 20)
        assert emit is not None
        assert "[EMAIL]" in emit
        assert "u@e.com" not in emit

    def test_mask_continues_streaming(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 11)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.MASK}))
        stream = _wrapper(scanner)
        first = stream.process_chunk("a" * 300)
        assert first is not None
        assert not stream.is_blocked
        second = stream.process_chunk("b" * 300)
        assert second is not None
        assert not stream.is_blocked


class TestStreamingBlock:
    def test_block_returns_none_and_marks_blocked(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["AWS_KEY"],
            [_finding("AWS_KEY", "AKIA", 0, 4)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"AWS_KEY": DLPAction.BLOCK}))
        stream = _wrapper(scanner, threshold=10, overlap=2)
        emit = stream.process_chunk("AKIA-secret-data-here")
        assert emit is None
        assert stream.is_blocked

    def test_block_subsequent_calls_return_none(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["AWS_KEY"],
            [_finding("AWS_KEY", "AKIA", 0, 4)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"AWS_KEY": DLPAction.BLOCK}))
        stream = _wrapper(scanner, threshold=10, overlap=2)
        stream.process_chunk("AKIA-secret-data-here")
        assert stream.process_chunk("more data") is None


class TestStreamingAudit:
    def test_audit_keeps_original_text(self) -> None:
        rec = _StubRecognizer("r", ["EMAIL"], [_finding("EMAIL", "u@e.com", 4, 11)])
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.AUDIT}))
        stream = _wrapper(scanner, threshold=20, overlap=2)
        emit = stream.process_chunk("see u@e.com today " + "x" * 20)
        assert emit is not None
        assert "u@e.com" in emit


class TestStreamingOverlapRetention:
    def test_overlap_characters_retained_in_buffer(self) -> None:
        rec = _StubRecognizer("r", [], [])
        scanner = DLPScanner(_registry(rec), _policy_for({}))
        stream = _wrapper(scanner, threshold=20, overlap=5)
        stream.process_chunk("a" * 20)
        assert stream.buffer_length == 5

    def test_overlap_zero_releases_full_text(self) -> None:
        rec = _StubRecognizer("r", [], [])
        scanner = DLPScanner(_registry(rec), _policy_for({}))
        stream = _wrapper(scanner, threshold=20, overlap=0)
        emit = stream.process_chunk("a" * 20)
        assert emit == "a" * 20
        assert stream.buffer_length == 0


class TestStreamingFinalize:
    def test_finalize_drains_buffer(self) -> None:
        rec = _StubRecognizer("r", [], [])
        scanner = DLPScanner(_registry(rec), _policy_for({}))
        stream = _wrapper(scanner, threshold=100, overlap=5)
        stream.process_chunk("short")
        assert stream.process_chunk("") == ""
        result = stream.finalize()
        assert result == "short"
        assert stream.buffer_length == 0

    def test_finalize_empty_buffer_returns_empty(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({}))
        stream = _wrapper(scanner)
        assert stream.finalize() == ""

    def test_finalize_idempotent(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({}))
        stream = _wrapper(scanner)
        stream.finalize()
        assert stream.finalize() == ""

    def test_process_after_finalize_returns_none(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({}))
        stream = _wrapper(scanner)
        stream.finalize()
        assert stream.process_chunk("data") is None

    def test_finalize_after_block_returns_none(self) -> None:
        rec = _StubRecognizer("r", ["AWS_KEY"], [_finding("AWS_KEY", "AKIA", 0, 4)])
        scanner = DLPScanner(_registry(rec), _policy_for({"AWS_KEY": DLPAction.BLOCK}))
        stream = _wrapper(scanner, threshold=10, overlap=2)
        stream.process_chunk("AKIA secret")
        assert stream.finalize() is None


class TestStreamingSafeSplit:
    def test_does_not_split_inside_placeholder(self) -> None:
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 0, 7)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.MASK}))
        stream = _wrapper(scanner, threshold=10, overlap=4)
        emit = stream.process_chunk("u@e.com")
        assert emit is not None
        assert "[EMAIL]" not in emit or emit.endswith("[EMAIL]") is False
        assert stream.buffer_length >= 4

    def test_safe_split_records_correction_when_placeholder_straddles(self) -> None:
        """When a masked placeholder straddles the natural split point,
        the wrapper extends the emit to include the whole placeholder
        and records a correction note.
        """
        rec = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 8, 15)],
        )
        scanner = DLPScanner(_registry(rec), _policy_for({"EMAIL": DLPAction.MASK}))
        stream = _wrapper(scanner, threshold=15, overlap=5)
        emit = stream.process_chunk("contact u@e.com now")
        assert emit is not None
        assert "[EMAIL]" in emit
        assert stream.corrections != []


class TestStreamingConstructorValidation:
    def test_negative_threshold_raises(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({}))
        with pytest.raises(ValueError, match="threshold must be positive"):
            StreamingDLPWrapper(scanner, threshold=0)

    def test_negative_overlap_raises(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({}))
        with pytest.raises(ValueError, match="overlap must be non-negative"):
            StreamingDLPWrapper(scanner, threshold=10, overlap=-1)

    def test_overlap_at_or_above_threshold_raises(self) -> None:
        scanner = DLPScanner(_registry(), _policy_for({}))
        with pytest.raises(ValueError, match="overlap must be smaller than threshold"):
            StreamingDLPWrapper(scanner, threshold=10, overlap=10)


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
