"""Tests for encryption helper (Group 9) and security hook factory (Group 10)."""

from __future__ import annotations

import uuid

from hecate.runtime.eventstore import EventType, InMemoryEventStore
from hecate.runtime.guardrail import NoOpPostLLMHook, NoOpPostToolHook, NoOpPreLLMHook, NoOpPreToolHook
from hecate.services.security.hooks import (
    InputSecurityHook,
    OutputSecurityHook,
    ToolResultSecurityHook,
    create_security_hooks,
)

# -- Group 10: Security Hook Factory tests --


class TestCreateSecurityHooks:
    def test_none_config_returns_noop_hooks(self) -> None:
        hooks = create_security_hooks(None)
        assert isinstance(hooks.pre_llm_hook, NoOpPreLLMHook)
        assert isinstance(hooks.post_llm_hook, NoOpPostLLMHook)
        assert isinstance(hooks.pre_tool_hook, NoOpPreToolHook)
        assert isinstance(hooks.post_tool_hook, NoOpPostToolHook)

    def test_empty_config_returns_noop_hooks(self) -> None:
        hooks = create_security_hooks({})
        assert isinstance(hooks.pre_llm_hook, NoOpPreLLMHook)
        assert isinstance(hooks.post_llm_hook, NoOpPostLLMHook)

    def test_full_config_returns_real_hooks(self) -> None:
        config = {
            "input_security": {"enabled": True},
            "output_security": {"enabled": True},
            "data_security": {"enabled": True, "mask_tool_results": True},
        }
        hooks = create_security_hooks(config)
        assert isinstance(hooks.pre_llm_hook, InputSecurityHook)
        assert isinstance(hooks.post_llm_hook, OutputSecurityHook)
        assert isinstance(hooks.pre_tool_hook, NoOpPreToolHook)
        assert isinstance(hooks.post_llm_hook, OutputSecurityHook)

    def test_dlp_scanner_threads_into_output_and_tool_result_hooks(self) -> None:
        """9.10 wiring: the DLP scanner passed to the factory reaches the
        output-security and tool-result hooks, so the documented 3-point
        scanning actually runs in the middleware chain.

        A regression here silently reverts to the pre-wiring state where
        the scanner stayed None and the DLP leg never executed.
        """
        from unittest.mock import MagicMock

        config = {
            "output_security": {"enabled": True},
            "data_security": {"enabled": True},
        }
        scanner = MagicMock(name="dlp_scanner")
        hooks = create_security_hooks(config, dlp_scanner=scanner)

        assert isinstance(hooks.post_llm_hook, OutputSecurityHook)
        assert isinstance(hooks.post_tool_hook, ToolResultSecurityHook)
        assert hooks.post_llm_hook._dlp_scanner is scanner  # noqa: SLF001 — wiring assertion
        assert hooks.post_tool_hook._dlp_scanner is scanner  # noqa: SLF001 — wiring assertion

    def test_no_dlp_scanner_leaves_hooks_none(self) -> None:
        """Backward-compat: omitting the scanner keeps the hooks' DLP leg
        disabled (the pre-wiring behavior for callers that don't pass one)."""
        config = {
            "output_security": {"enabled": True},
            "data_security": {"enabled": True},
        }
        hooks = create_security_hooks(config)
        assert hooks.post_llm_hook._dlp_scanner is None  # noqa: SLF001
        assert hooks.post_tool_hook._dlp_scanner is None  # noqa: SLF001

    def test_disabled_section_returns_noop(self) -> None:
        config = {
            "input_security": {"enabled": False},
            "output_security": {"enabled": False},
            "data_security": {"enabled": False},
        }
        hooks = create_security_hooks(config)
        assert isinstance(hooks.pre_llm_hook, NoOpPreLLMHook)
        assert isinstance(hooks.post_llm_hook, NoOpPostLLMHook)
        assert isinstance(hooks.post_tool_hook, NoOpPostToolHook)

    def test_partial_config_returns_mixed(self) -> None:
        config = {
            "input_security": {"enabled": True},
        }
        hooks = create_security_hooks(config)
        assert isinstance(hooks.pre_llm_hook, InputSecurityHook)
        assert isinstance(hooks.post_llm_hook, NoOpPostLLMHook)

    def test_security_hook_set_is_named_tuple(self) -> None:
        hooks = create_security_hooks(None)
        assert hasattr(hooks, "_fields")
        assert "pre_llm_hook" in hooks._fields
        assert "post_llm_hook" in hooks._fields
        assert "pre_tool_hook" in hooks._fields
        assert "post_tool_hook" in hooks._fields

    def test_config_with_custom_thresholds(self) -> None:
        config = {
            "output_security": {
                "enabled": True,
                "toxicity_threshold": 0.9,
                "deanonymize": False,
            },
        }
        hooks = create_security_hooks(config)
        assert isinstance(hooks.post_llm_hook, OutputSecurityHook)


# -- Group 12: PII_DETECTED EventType test --


class TestPIIDetectedEventType:
    def test_pii_detected_event_type_exists(self) -> None:
        assert EventType.PII_DETECTED == "PII_DETECTED"

    def test_pii_detected_is_string(self) -> None:
        assert isinstance(EventType.PII_DETECTED, str)


# -- Group 12: Audit logging tests --


class TestInputSecurityHookAudit:
    async def test_audit_disabled_no_event(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        hook = InputSecurityHook(
            pii_entities=["email"],
            audit_pii_events=False,
            event_store=store,
            session_id=sid,
        )
        await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "Email user@example.com"}],
            model="gpt-4o",
            tools=None,
        )
        # No events emitted when audit disabled
        events = await store.get_events(sid)
        assert len(events) == 0

    async def test_audit_enabled_emits_event_without_original_pii(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        hook = InputSecurityHook(
            pii_entities=["email"],
            audit_pii_events=True,
            event_store=store,
            session_id=sid,
            superstep=1,
        )
        await hook.on_pre_llm_call(
            messages=[{"role": "user", "content": "Email user@example.com"}],
            model="gpt-4o",
            tools=None,
        )
        # Allow event loop to process the scheduled task
        import asyncio

        await asyncio.sleep(0.05)
        events = await store.get_events(sid)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == EventType.PII_DETECTED
        assert event.payload["source"] == "input"
        assert "email" in event.payload["pii_types"]
        assert event.payload["placeholder_count"] == 1
        # MUST NOT contain original PII values
        assert "user@example.com" not in str(event.payload)


class TestOutputSecurityHookAudit:
    async def test_audit_emits_event_on_deanonymize(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        hook = OutputSecurityHook(
            deanonymize=True,
            audit_pii_events=True,
            event_store=store,
            session_id=sid,
            superstep=2,
        )
        await hook.on_post_llm_call(
            response={"content": "Contact [EMAIL_1] for help"},
            messages=[],
        )
        import asyncio

        await asyncio.sleep(0.05)
        events = await store.get_events(sid)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == EventType.PII_DETECTED
        assert event.payload["source"] == "output"
        assert event.payload["placeholder_count"] == 1
        # MUST NOT contain original PII values
        assert "user@example.com" not in str(event.payload)


class TestToolResultSecurityHookAudit:
    async def test_audit_emits_event_on_mask(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        hook = ToolResultSecurityHook(
            mask_tool_results=True,
            audit_pii_events=True,
            event_store=store,
            session_id=sid,
            superstep=3,
        )
        await hook.on_post_tool_call(
            "search",
            "Found email user@example.com in database",
            None,
        )
        import asyncio

        await asyncio.sleep(0.05)
        events = await store.get_events(sid)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == EventType.PII_DETECTED
        assert event.payload["source"] == "tool_result"
        assert event.payload["tool_name"] == "search"
        assert event.payload["placeholder_count"] >= 1
        # MUST NOT contain original PII values
        assert "user@example.com" not in str(event.payload)

    async def test_audit_disabled_no_event(self) -> None:
        store = InMemoryEventStore()
        sid = uuid.uuid4()
        hook = ToolResultSecurityHook(
            mask_tool_results=True,
            audit_pii_events=False,
            event_store=store,
            session_id=sid,
        )
        await hook.on_post_tool_call(
            "search",
            "Found email user@example.com",
            None,
        )
        events = await store.get_events(sid)
        assert len(events) == 0
