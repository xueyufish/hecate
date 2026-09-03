"""Integration tests for the outbound DLP engine (Tasks 18.4-18.6).

These tests exercise the full DLP pipeline end-to-end using mocked
infrastructure (no real PostgreSQL, no real MCP server). They verify
the contracts between components that unit tests can't cover:

* 18.4 — Default rules + ToolResultSecurityHook → BLOCK on secrets
* 18.5 — MCP client egress chain → MASK on EMAIL
* 18.6 — Multi-tenant policy resolver → is_locked override
"""

from __future__ import annotations

import uuid
from typing import Any

from hecate.ops.dlp.defaults import DEFAULT_RULES
from hecate.ops.dlp.policy import (
    DLPPolicyResolver,
    DLPPolicyRule,
    PolicyScope,
)
from hecate.ops.dlp.recognizer import DLPRecognizerRegistry
from hecate.ops.dlp.recognizers.regex import RegexRecognizer
from hecate.ops.dlp.result import DLPAction
from hecate.ops.dlp.scanner import DLPScanner
from hecate.runtime.guardrail import GuardrailAction
from hecate.runtime.security.egress import DLPEgressFilter
from hecate.runtime.security.hooks.tool_result_security import (
    ToolResultSecurityHook,
)
from hecate.tools.mcp.client import HecateMCPClient


class _StubTextContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubCallResult:
    def __init__(self, content: list[_StubTextContent] | None) -> None:
        self.content = content


class _StubSession:
    def __init__(self, text: str) -> None:
        self._text = text

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> _StubCallResult:
        return _StubCallResult([_StubTextContent(self._text)])


def _build_default_scanner() -> DLPScanner:
    """Build a scanner with DEFAULT-scope rules from DEFAULT_RULES.

    Uses DEFAULT scope so rules apply regardless of org/workspace/agent
    context (the hooks don't pass scope IDs to the scanner yet).
    """
    registry = DLPRecognizerRegistry()
    registry.register(RegexRecognizer())
    rules = [
        DLPPolicyRule(
            entity_type=s.entity_type,
            direction=s.direction,
            action=s.action,
            scope=PolicyScope.DEFAULT,
            is_locked=s.is_locked,
        )
        for s in DEFAULT_RULES
    ]
    return DLPScanner(registry, DLPPolicyResolver(rules))


# ------------------------------------------------------------------
# 18.4 — Agent with default guardrail config: secrets in tool result → BLOCK
# ------------------------------------------------------------------


class TestIntegrationDefaultRulesBlockSecrets:
    async def test_ssn_in_tool_result_is_masked(self) -> None:
        scanner = _build_default_scanner()
        hook = ToolResultSecurityHook(dlp_scanner=scanner)

        result = await hook.on_post_tool_call(
            "lookup",
            "SSN: 123-45-6789 found in record",
            None,
        )

        assert result.action == GuardrailAction.SANITIZE
        assert "123-45-6789" not in result.modified_data["result"]
        assert "[SSN]" in result.modified_data["result"]

    async def test_credit_card_in_tool_result_is_masked(self) -> None:
        scanner = _build_default_scanner()
        hook = ToolResultSecurityHook(dlp_scanner=scanner)

        result = await hook.on_post_tool_call(
            "payment_lookup",
            "Card 4111-1111-1111-1111 on file",
            None,
        )

        assert result.action == GuardrailAction.SANITIZE
        assert "4111" not in result.modified_data["result"]

    async def test_email_in_tool_result_passes_through(self) -> None:
        """EMAIL has no tool_output rule in DEFAULT_RULES — only llm_output.
        Per fail-open default (design.md §D5), unmapped entity/direction
        pairs return ALLOW.
        """
        scanner = _build_default_scanner()
        hook = ToolResultSecurityHook(dlp_scanner=scanner)

        result = await hook.on_post_tool_call(
            "search",
            "Contact user@example.com for details",
            None,
        )

        assert result.action == GuardrailAction.ALLOW

    async def test_clean_text_passes_through(self) -> None:
        scanner = _build_default_scanner()
        hook = ToolResultSecurityHook(dlp_scanner=scanner)

        result = await hook.on_post_tool_call(
            "search",
            "The weather is sunny today",
            None,
        )

        assert result.action == GuardrailAction.ALLOW


# ------------------------------------------------------------------
# 18.5 — MCP tool call: response scanned via egress filter
# ------------------------------------------------------------------


class TestIntegrationMCPEgressMask:
    async def test_ssn_in_mcp_response_is_masked(self) -> None:
        """SSN is MASK for tool_output in DEFAULT_RULES. The MCP egress
        filter scans with direction='tool_output' and should replace
        the SSN with a [SSN] placeholder.
        """
        scanner = _build_default_scanner()
        egress = DLPEgressFilter(scanner, direction="tool_output")

        client = HecateMCPClient(egress_filters=[egress])
        client._connected = True
        client._client = _StubSession("Found SSN 123-45-6789 in data")

        result = await client.call_tool("lookup", {})
        assert result is not None
        assert "123-45-6789" not in result
        assert "[SSN]" in result

    async def test_clean_mcp_response_passes_through(self) -> None:
        scanner = _build_default_scanner()
        egress = DLPEgressFilter(scanner, direction="tool_output")

        client = HecateMCPClient(egress_filters=[egress])
        client._connected = True
        client._client = _StubSession("The temperature is 72 degrees")

        result = await client.call_tool("weather", {})
        assert result == "The temperature is 72 degrees"

    async def test_no_filters_backward_compatible(self) -> None:
        """Without egress filters, call_tool returns raw text."""
        client = HecateMCPClient()
        client._connected = True
        client._client = _StubSession("SSN 123-45-6789 raw response")

        result = await client.call_tool("raw", {})
        assert result == "SSN 123-45-6789 raw response"


# ------------------------------------------------------------------
# 18.6 — Multi-tenant: org BLOCK locked vs workspace ALLOW → BLOCK wins
# ------------------------------------------------------------------


class TestIntegrationMultiTenantIsLocked:
    def test_org_locked_block_overrides_workspace_allow(self) -> None:
        org_id = uuid.uuid4()
        workspace_id = uuid.uuid4()

        resolver = DLPPolicyResolver(
            [
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.ALLOW,
                    scope=PolicyScope.WORKSPACE,
                    scope_id=workspace_id,
                    is_locked=False,
                ),
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.BLOCK,
                    scope=PolicyScope.ORG,
                    scope_id=org_id,
                    is_locked=True,
                ),
            ]
        )

        result = resolver.resolve(
            "EMAIL",
            "llm_output",
            workspace_id=workspace_id,
            org_id=org_id,
        )
        assert result == DLPAction.BLOCK

    def test_org_unlocked_block_does_not_override_workspace_allow(self) -> None:
        org_id = uuid.uuid4()
        workspace_id = uuid.uuid4()

        resolver = DLPPolicyResolver(
            [
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.ALLOW,
                    scope=PolicyScope.WORKSPACE,
                    scope_id=workspace_id,
                ),
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.BLOCK,
                    scope=PolicyScope.ORG,
                    scope_id=org_id,
                ),
            ]
        )

        result = resolver.resolve(
            "EMAIL",
            "llm_output",
            workspace_id=workspace_id,
            org_id=org_id,
        )
        assert result == DLPAction.ALLOW

    def test_agent_scope_overrides_org_when_not_locked(self) -> None:
        org_id = uuid.uuid4()
        agent_id = uuid.uuid4()

        resolver = DLPPolicyResolver(
            [
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.AUDIT,
                    scope=PolicyScope.AGENT,
                    scope_id=agent_id,
                ),
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.BLOCK,
                    scope=PolicyScope.ORG,
                    scope_id=org_id,
                ),
            ]
        )

        result = resolver.resolve(
            "EMAIL",
            "llm_output",
            agent_id=agent_id,
            org_id=org_id,
        )
        assert result == DLPAction.AUDIT

    def test_org_locked_with_agent_rule_still_wins(self) -> None:
        org_id = uuid.uuid4()
        agent_id = uuid.uuid4()

        resolver = DLPPolicyResolver(
            [
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.ALLOW,
                    scope=PolicyScope.AGENT,
                    scope_id=agent_id,
                ),
                DLPPolicyRule(
                    entity_type="EMAIL",
                    direction="llm_output",
                    action=DLPAction.BLOCK,
                    scope=PolicyScope.ORG,
                    scope_id=org_id,
                    is_locked=True,
                ),
            ]
        )

        result = resolver.resolve(
            "EMAIL",
            "llm_output",
            agent_id=agent_id,
            org_id=org_id,
        )
        assert result == DLPAction.BLOCK

    def test_default_rules_block_secrets_across_all_scopes(self) -> None:
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()

        rules = []
        for org_id in [org_a, org_b]:
            for s in DEFAULT_RULES:
                if s.entity_type == "AWS_ACCESS_KEY":
                    rules.append(
                        DLPPolicyRule(
                            entity_type=s.entity_type,
                            direction=s.direction,
                            action=s.action,
                            scope=PolicyScope.ORG,
                            scope_id=org_id,
                            is_locked=s.is_locked,
                        )
                    )

        resolver = DLPPolicyResolver(rules)

        for org_id in [org_a, org_b]:
            result = resolver.resolve("AWS_ACCESS_KEY", "llm_output", org_id=org_id)
            assert result == DLPAction.BLOCK
