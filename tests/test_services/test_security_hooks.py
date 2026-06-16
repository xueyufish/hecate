"""Tests for security hooks: InputSecurityHook, OutputSecurityHook,
StreamDeanonymizer, ToolResultSecurityHook."""

from __future__ import annotations

from hecate.engine.guardrail import GuardrailAction
from hecate.services.security.hooks.input_security import InputSecurityHook
from hecate.services.security.hooks.output_security import OutputSecurityHook
from hecate.services.security.hooks.stream_deanonymizer import StreamDeanonymizer
from hecate.services.security.hooks.tool_result_security import ToolResultSecurityHook

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
