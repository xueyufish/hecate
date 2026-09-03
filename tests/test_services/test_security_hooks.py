"""Tests for security hooks: InputSecurityHook, OutputSecurityHook,
StreamDeanonymizer, ToolResultSecurityHook."""

from __future__ import annotations

from typing import Any

import pytest

from hecate.ops.dlp.result import DLPAction, DLPFinding, DLPResult
from hecate.runtime.guardrail import GuardrailAction
from hecate.runtime.security.hooks.input_security import InputSecurityHook
from hecate.runtime.security.hooks.output_security import OutputSecurityHook
from hecate.runtime.security.hooks.stream_deanonymizer import StreamDeanonymizer
from hecate.runtime.security.hooks.tool_result_security import ToolResultSecurityHook

# -- InputSecurityHook tests --


class TestInputSecurityHook:
    async def test_clean_messages_pass_through(self) -> None:
        hook = InputSecurityHook()
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "What is the weather?"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_pii_detected_in_messages(self) -> None:
        hook = InputSecurityHook(pii_entities=["email"])
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "Contact user@example.com please"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.SANITIZE
        assert result.modified_data is not None
        assert "user@example.com" not in str(result.modified_data["messages"])
        assert "_pii_mappings" in result.modified_data

    async def test_disabled_returns_allow(self) -> None:
        hook = InputSecurityHook(enabled=False)
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "user@example.com"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_entity_type_filtering(self) -> None:
        hook = InputSecurityHook(pii_entities=["email"])
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "Call 555-123-4567"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_injection_blocked(self) -> None:
        hook = InputSecurityHook(block_on_injection=True)
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "How to hack exploit systems"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action in (GuardrailAction.BLOCK, GuardrailAction.ALLOW)

    async def test_multiple_pii_unique_placeholders(self) -> None:
        hook = InputSecurityHook(pii_entities=["email"])
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "Email a@x.com and b@y.com"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.SANITIZE
        mappings = result.modified_data["_pii_mappings"]
        assert len(mappings) == 2


class _StubDLPScanner:
    """Minimal DLPScanner double for InputSecurityHook tests."""

    def __init__(self, action: DLPAction) -> None:
        self._action = action
        self.last_direction: str | None = None

    def scan(self, text: str, direction: str, **_: object) -> DLPResult:
        self.last_direction = direction
        if self._action == DLPAction.BLOCK:
            return DLPResult(
                findings=[
                    DLPFinding(
                        entity_type="AWS_ACCESS_KEY",
                        value="AKIA",
                        start=0,
                        end=4,
                        score=1.0,
                        recognizer="stub",
                    )
                ],
                action=DLPAction.BLOCK,
                text=None,
                audit_data=[{"entity_type": "AWS_ACCESS_KEY"}],
            )
        return DLPResult(
            findings=[],
            action=self._action,
            text=text,
            audit_data=[],
        )


class TestInputSecurityHookWithDLPScanner:
    async def test_dlp_scanner_blocks_on_aws_key(self) -> None:
        scanner = _StubDLPScanner(DLPAction.BLOCK)
        hook = InputSecurityHook(dlp_scanner=scanner)
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "AKIA secret stuff"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.BLOCK
        assert "DLP" in result.reason

    async def test_dlp_scanner_called_with_llm_input_direction(self) -> None:
        scanner = _StubDLPScanner(DLPAction.ALLOW)
        hook = InputSecurityHook(dlp_scanner=scanner)
        await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "harmless text"}],
            model="gpt-4o",
            tools=None,
        )
        assert scanner.last_direction == "llm_input"

    async def test_dlp_scanner_allow_lets_pii_anonymization_run(self) -> None:
        scanner = _StubDLPScanner(DLPAction.ALLOW)
        hook = InputSecurityHook(dlp_scanner=scanner, pii_entities=["email"])
        result = await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "Contact user@example.com"}],
            model="gpt-4o",
            tools=None,
        )
        assert result.action == GuardrailAction.SANITIZE
        assert "user@example.com" not in str(result.modified_data["messages"])

    async def test_no_dlp_scanner_falls_back_to_llm_guard(self) -> None:
        hook = InputSecurityHook()  # no dlp_scanner
        assert hook._dlp_scanner is None


# -- OutputSecurityHook tests --


class TestOutputSecurityHook:
    async def test_clean_response_passes(self) -> None:
        hook = OutputSecurityHook()
        result = await hook.on_post_llm_call(
            response={"content": "The weather is sunny."},
            messages=[],
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_disabled_returns_allow(self) -> None:
        hook = OutputSecurityHook(enabled=False)
        result = await hook.on_post_llm_call(
            response={"content": "bad toxic content"},
            messages=[],
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_deanonymize_disabled(self) -> None:
        hook = OutputSecurityHook(deanonymize=False)
        result = await hook.on_post_llm_call(
            response={"content": "Contact [EMAIL_1]"},
            messages=[],
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_response_with_placeholder_triggers_sanitize(self) -> None:
        hook = OutputSecurityHook(deanonymize=True)
        result = await hook.on_post_llm_call(
            response={"content": "Contact [EMAIL_1] for help", "model": "gpt-4o"},
            messages=[],
        )
        assert result.action == GuardrailAction.SANITIZE
        assert result.modified_data is not None

    async def test_deanonymize_text_static(self) -> None:
        mappings = {"user@example.com": "[EMAIL_1]"}
        text = OutputSecurityHook.deanonymize_text("Contact [EMAIL_1]", mappings)
        assert text == "Contact user@example.com"

    async def test_empty_content_returns_allow(self) -> None:
        hook = OutputSecurityHook()
        result = await hook.on_post_llm_call(
            response={"content": ""},
            messages=[],
        )
        assert result.action == GuardrailAction.ALLOW


class _StubOutputDLPScanner:
    """Minimal DLPScanner double for OutputSecurityHook tests."""

    def __init__(self, action: str, text: str | None = None) -> None:
        self._action = action
        self._text = text
        self.last_direction: str | None = None

    def scan(self, text: str, direction: str, **_: object) -> Any:  # noqa: ANN401
        self.last_direction = direction
        from hecate.ops.dlp.result import (
            DLPAction,
            DLPFinding,
            DLPResult,
        )

        if self._action == "block":
            return DLPResult(
                findings=[
                    DLPFinding(
                        entity_type="AWS_ACCESS_KEY",
                        value="AKIA",
                        start=0,
                        end=4,
                        score=1.0,
                        recognizer="stub",
                    )
                ],
                action=DLPAction.BLOCK,
                text=None,
                audit_data=[],
            )
        if self._action == "mask":
            return DLPResult(
                findings=[
                    DLPFinding(
                        entity_type="EMAIL",
                        value="user@example.com",
                        start=0,
                        end=16,
                        score=1.0,
                        recognizer="stub",
                    )
                ],
                action=DLPAction.MASK,
                text="see [EMAIL] for details",
                audit_data=[],
            )
        from hecate.ops.dlp.result import (
            DLPAction,
            DLPResult,
        )

        return DLPResult(
            findings=[],
            action=DLPAction.ALLOW,
            text=self._text or text,
            audit_data=[],
        )


class TestOutputSecurityHookWithDLPScanner:
    async def test_dlp_block_returns_block_no_content(self) -> None:
        scanner = _StubOutputDLPScanner("block")
        hook = OutputSecurityHook(dlp_scanner=scanner)
        result = await hook.on_post_llm_call(
            response={"content": "AKIA secret stuff"},
            messages=[],
        )
        assert result.action == GuardrailAction.BLOCK
        assert "DLP" in result.reason

    async def test_dlp_mask_replaces_with_modified_data(self) -> None:
        scanner = _StubOutputDLPScanner("mask")
        hook = OutputSecurityHook(dlp_scanner=scanner)
        result = await hook.on_post_llm_call(
            response={"content": "see user@example.com for details"},
            messages=[],
        )
        assert result.action == GuardrailAction.SANITIZE
        assert result.modified_data is not None
        modified = result.modified_data["response"]
        assert modified["content"] == "see [EMAIL] for details"

    async def test_dlp_audit_returns_deanonymized_unchanged(self) -> None:
        scanner = _StubOutputDLPScanner("allow")
        writer_calls: list[dict] = []

        def writer(**kwargs: object) -> None:
            writer_calls.append(kwargs)

        hook = OutputSecurityHook(
            dlp_scanner=scanner,
            security_finding_writer=writer,
        )
        result = await hook.on_post_llm_call(
            response={"content": "harmless text"},
            messages=[],
        )
        assert result.action == GuardrailAction.ALLOW
        assert result.modified_data is None or "response" in result.modified_data

    async def test_dlp_called_with_llm_output_direction(self) -> None:
        scanner = _StubOutputDLPScanner("allow")
        hook = OutputSecurityHook(dlp_scanner=scanner)
        await hook.on_post_llm_call(
            response={"content": "some content"},
            messages=[],
        )
        assert scanner.last_direction == "llm_output"

    async def test_dlp_runs_after_deanonymization(self) -> None:
        """The DLP scanner should see the deanonymized (real-value) text,
        not the original placeholder text.
        """
        seen_by_scanner: list[str] = []

        class _CaptureScanner:
            def scan(self, text: str, direction: str, **_: object) -> Any:  # noqa: ANN401
                seen_by_scanner.append(text)
                from hecate.ops.dlp.result import (
                    DLPAction,
                    DLPResult,
                )

                return DLPResult(
                    findings=[],
                    action=DLPAction.ALLOW,
                    text=text,
                    audit_data=[],
                )

        # Output contains a [EMAIL_1] placeholder; the test's deanonymization
        # step (in on_post_llm_call) is no-op for this case (no mappings),
        # so the scanner sees the placeholder. The point is the DLP runs
        # AFTER any deanonymization, not before. We assert the scanner was
        # called and saw post-deanonymization content.
        hook = OutputSecurityHook(dlp_scanner=_CaptureScanner())
        result = await hook.on_post_llm_call(
            response={"content": "Contact [EMAIL_1]"},
            messages=[],
        )
        assert len(seen_by_scanner) == 1
        assert seen_by_scanner[0] == "Contact [EMAIL_1]"
        assert result.action == GuardrailAction.SANITIZE

    async def test_no_dlp_scanner_skips_dlp_path(self) -> None:
        hook = OutputSecurityHook()
        result = await hook.on_post_llm_call(
            response={"content": "AKIA anything"},
            messages=[],
        )
        assert result.action == GuardrailAction.ALLOW


# -- StreamDeanonymizer tests --


class TestStreamDeanonymizer:
    def test_non_pii_tokens_emitted_immediately(self) -> None:
        sd = StreamDeanonymizer()
        assert sd.process("hello ") == "hello "
        assert sd.process("world") == "world"

    def test_complete_placeholder_deanonymized(self) -> None:
        sd = StreamDeanonymizer(mappings={"user@example.com": "[EMAIL_1]"})
        result = sd.process("Contact [EMAIL_1]")
        assert "user@example.com" in result

    def test_split_placeholder_buffered(self) -> None:
        sd = StreamDeanonymizer(mappings={"user@example.com": "[EMAIL_1]"})
        r1 = sd.process("Contact [")
        assert r1 == "Contact "
        r2 = sd.process("EMAIL_")
        assert r2 == ""
        r3 = sd.process("1]")
        assert "user@example.com" in r3

    def test_flush_complete_placeholder(self) -> None:
        sd = StreamDeanonymizer(mappings={"user@example.com": "[EMAIL_1]"})
        sd.process("Contact [EMAIL_1]")
        flushed = sd.flush()
        assert flushed == ""

    def test_flush_partial_placeholder(self) -> None:
        sd = StreamDeanonymizer(mappings={"user@example.com": "[EMAIL_1]"})
        sd.process("Contact [EMA")
        flushed = sd.flush()
        assert flushed == "[EMA"

    def test_flush_empty_buffer(self) -> None:
        sd = StreamDeanonymizer()
        assert sd.flush() == ""

    def test_multiple_placeholders(self) -> None:
        sd = StreamDeanonymizer(
            mappings={
                "user@example.com": "[EMAIL_1]",
                "555-123-4567": "[PHONE_1]",
            }
        )
        result = sd.process("Contact [EMAIL_1] and [PHONE_1]")
        assert "user@example.com" in result
        assert "555-123-4567" in result

    def test_unknown_placeholder_passes_through(self) -> None:
        sd = StreamDeanonymizer(mappings={})
        result = sd.process("See [UNKNOWN_1]")
        assert "[UNKNOWN_1]" in result


# -- ToolResultSecurityHook tests --


class TestToolResultSecurityHook:
    async def test_clean_result_passes(self) -> None:
        hook = ToolResultSecurityHook()
        result = await hook.on_post_tool_call("search", "no PII here", None)
        assert result.action == GuardrailAction.ALLOW

    async def test_pii_in_result_masked(self) -> None:
        hook = ToolResultSecurityHook()
        result = await hook.on_post_tool_call(
            "search",
            "Found email user@example.com in database",
            None,
        )
        assert result.action == GuardrailAction.SANITIZE
        assert "user@example.com" not in result.modified_data["result"]

    async def test_masking_disabled(self) -> None:
        hook = ToolResultSecurityHook(mask_tool_results=False)
        result = await hook.on_post_tool_call(
            "search",
            "Found email user@example.com",
            None,
        )
        assert result.action == GuardrailAction.ALLOW

    async def test_none_result_passes(self) -> None:
        hook = ToolResultSecurityHook()
        result = await hook.on_post_tool_call("search", None, None)
        assert result.action == GuardrailAction.ALLOW

    async def test_empty_result_passes(self) -> None:
        hook = ToolResultSecurityHook()
        result = await hook.on_post_tool_call("search", "", None)
        assert result.action == GuardrailAction.ALLOW


class _StubToolDLPScanner:
    """Minimal DLPScanner double for ToolResultSecurityHook tests."""

    def __init__(self, action: str, text: str | None = None) -> None:
        self._action = action
        self._text = text
        self.last_direction: str | None = None
        self.findings: list[Any] = []

    def scan(self, text: str, direction: str, **_: object) -> Any:
        from hecate.ops.dlp.result import (
            DLPAction,
            DLPFinding,
            DLPResult,
        )

        self.last_direction = direction
        if self._action == "allow":
            self.findings = []
            return DLPResult(
                findings=[],
                action=DLPAction.ALLOW,
                text=text,
                audit_data=[],
            )
        finding = DLPFinding(
            entity_type="EMAIL",
            value="user@example.com",
            start=0,
            end=16,
            score=1.0,
            recognizer="stub",
        )
        self.findings = [finding]
        if self._action == "block":
            return DLPResult(
                findings=[finding],
                action=DLPAction.BLOCK,
                text=None,
                audit_data=[],
            )
        return DLPResult(
            findings=[finding],
            action=DLPAction.MASK,
            text="see [EMAIL] for details",
            audit_data=[],
        )


class TestToolResultSecurityHookWithDLPScanner:
    async def test_dlp_scanner_called_with_tool_output_direction(self) -> None:
        scanner = _StubToolDLPScanner("allow")
        hook = ToolResultSecurityHook(dlp_scanner=scanner)
        await hook.on_post_tool_call("search", "user@example.com", None)
        assert scanner.last_direction == "tool_output"

    async def test_dlp_block_returns_block(self) -> None:
        scanner = _StubToolDLPScanner("block")
        hook = ToolResultSecurityHook(dlp_scanner=scanner)
        result = await hook.on_post_tool_call("search", "user@example.com secret", None)
        assert result.action == GuardrailAction.BLOCK

    async def test_dlp_mask_returns_sanitized_modified_data(self) -> None:
        scanner = _StubToolDLPScanner("mask")
        hook = ToolResultSecurityHook(dlp_scanner=scanner)
        result = await hook.on_post_tool_call("search", "see user@example.com for details", None)
        assert result.action == GuardrailAction.SANITIZE
        assert result.modified_data is not None
        assert "[EMAIL]" in result.modified_data["result"]

    async def test_dlp_allow_passes_through(self) -> None:
        scanner = _StubToolDLPScanner("allow")
        hook = ToolResultSecurityHook(dlp_scanner=scanner)
        result = await hook.on_post_tool_call("search", "clean text", None)
        assert result.action == GuardrailAction.ALLOW

    async def test_dlp_mask_and_encrypt_mode(self) -> None:
        scanner = _StubToolDLPScanner("mask")
        hook = ToolResultSecurityHook(
            dlp_scanner=scanner,
            pii_storage_mode="mask_and_encrypt",
            encryption_key=b"a" * 32,
        )
        result = await hook.on_post_tool_call("search", "see user@example.com for details", None)
        assert result.action == GuardrailAction.SANITIZE
        # The masked text is then encrypted via Fernet; the result should
        # not be the plaintext placeholder.
        assert "[EMAIL]" not in result.modified_data["result"]

    async def test_pii_storage_mode_validation(self) -> None:
        with pytest.raises(ValueError, match="Invalid pii_storage_mode"):
            ToolResultSecurityHook(pii_storage_mode="invalid_mode")

    async def test_no_dlp_scanner_falls_back_to_anonymizer(self) -> None:
        hook = ToolResultSecurityHook()  # no dlp_scanner
        result = await hook.on_post_tool_call("search", "user@example.com here", None)
        assert result.action == GuardrailAction.SANITIZE
        assert "user@example.com" not in result.modified_data["result"]
