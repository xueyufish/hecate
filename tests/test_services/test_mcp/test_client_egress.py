"""Tests for HecateMCPClient egress filter integration.

Mocks the underlying ``mcp.Client`` so we exercise the egress filter
chain without needing a real MCP server.
"""

from __future__ import annotations

from typing import Any

from hecate.ops.dlp.policy import (
    DLPPolicyResolver,
    DLPPolicyRule,
    PolicyScope,
)
from hecate.ops.dlp.recognizer import DLPRecognizerRegistry
from hecate.ops.dlp.result import DLPAction, DLPFinding
from hecate.ops.dlp.scanner import DLPScanner
from hecate.runtime.security.egress import (
    DLPEgressFilter,
    EgressAction,
    EgressResult,
)
from hecate.tools.mcp.client import HecateMCPClient


class _StubTextContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _StubCallResult:
    def __init__(self, content: list[_StubTextContent] | None) -> None:
        self.content = content


class _StubRecognizer:
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


def _scanner_with_recognizer(
    recognizer: _StubRecognizer,
    entity_type: str,
    action: DLPAction,
) -> DLPScanner:
    registry = DLPRecognizerRegistry()
    registry.register(recognizer)
    policy = DLPPolicyResolver(
        [
            DLPPolicyRule(
                entity_type=entity_type,
                direction="tool_output",
                action=action,
                scope=PolicyScope.DEFAULT,
            )
        ]
    )
    return DLPScanner(registry, policy)


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


def _make_client(
    egress_filters: list[Any] | None = None,
    audit_sink: Any = None,
    server_url: str | None = None,
) -> HecateMCPClient:
    client = HecateMCPClient(
        server_url=server_url,
        egress_filters=egress_filters,
        audit_sink=audit_sink,
    )
    client._connected = True
    client._client = _StubSession()
    return client


class _StubSession:
    def __init__(self) -> None:
        self._text: str | None = None
        self._contents: list[_StubTextContent] | None = None

    def set_text(self, text: str) -> None:
        self._text = text
        self._contents = [_StubTextContent(text)]

    def set_contents(self, contents: list[_StubTextContent]) -> None:
        self._contents = contents

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> _StubCallResult:
        return _StubCallResult(self._contents)


class TestMCPNoFilters:
    async def test_no_filters_returns_text_unchanged(self) -> None:
        client = _make_client()
        client._client.set_text("raw response")
        result = await client.call_tool("my_tool", {})
        assert result == "raw response"

    async def test_no_filters_returns_list_for_multiple_texts(self) -> None:
        client = _make_client()
        client._client.set_contents([_StubTextContent("first"), _StubTextContent("second")])
        result = await client.call_tool("my_tool", {})
        assert result == ["first", "second"]

    async def test_no_filters_returns_none_for_no_text_content(self) -> None:
        client = _make_client()
        client._client.set_contents([])
        result = await client.call_tool("my_tool", {})
        assert result is None


class TestMCPWithMASKFilter:
    async def test_email_in_response_is_masked(self) -> None:
        recognizer = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 13)],
        )
        scanner = _scanner_with_recognizer(recognizer, "EMAIL", DLPAction.MASK)
        egress = DLPEgressFilter(scanner)
        client = _make_client(egress_filters=[egress])
        client._client.set_text("see u@e.com here")
        result = await client.call_tool("my_tool", {})
        assert result is not None
        assert "[EMAIL]" in result
        assert "u@e.com" not in result

    async def test_no_email_passes_through(self) -> None:
        recognizer = _StubRecognizer("r", ["EMAIL"], [])
        scanner = _scanner_with_recognizer(recognizer, "EMAIL", DLPAction.MASK)
        egress = DLPEgressFilter(scanner)
        client = _make_client(egress_filters=[egress])
        client._client.set_text("no email here")
        result = await client.call_tool("my_tool", {})
        assert result == "no email here"


class TestMCPWithBLOCKFilter:
    async def test_aws_key_in_response_blocks(self) -> None:
        recognizer = _StubRecognizer(
            "r",
            ["AWS_KEY"],
            [_finding("AWS_KEY", "AKIA", 0, 4)],
        )
        scanner = _scanner_with_recognizer(recognizer, "AWS_KEY", DLPAction.BLOCK)
        egress = DLPEgressFilter(scanner)
        client = _make_client(egress_filters=[egress])
        client._client.set_text("AKIA secret stuff")
        result = await client.call_tool("my_tool", {})
        assert result is None

    async def test_audit_sink_called_on_block(self) -> None:
        recognizer = _StubRecognizer(
            "r",
            ["AWS_KEY"],
            [_finding("AWS_KEY", "AKIA", 0, 4)],
        )
        scanner = _scanner_with_recognizer(recognizer, "AWS_KEY", DLPAction.BLOCK)
        egress = DLPEgressFilter(scanner)

        sink_calls: list[dict[str, Any]] = []

        def sink(**kwargs: Any) -> None:
            sink_calls.append(kwargs)

        client = _make_client(egress_filters=[egress], audit_sink=sink, server_url="mcp://x")
        client._client.set_text("AKIA here")
        result = await client.call_tool("my_tool", {"k": "v"})
        assert result is None
        assert len(sink_calls) >= 1
        assert sink_calls[0]["entity_type"] == "AWS_KEY"
        assert sink_calls[0]["context"]["server"] == "mcp://x"
        assert sink_calls[0]["context"]["tool"] == "my_tool"


class TestMCPWithAUDITFilter:
    async def test_audit_passes_through(self) -> None:
        recognizer = _StubRecognizer(
            "r",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 13)],
        )
        scanner = _scanner_with_recognizer(recognizer, "EMAIL", DLPAction.AUDIT)
        egress = DLPEgressFilter(scanner)

        sink_calls: list[dict[str, Any]] = []

        def sink(**kwargs: Any) -> None:
            sink_calls.append(kwargs)

        client = _make_client(egress_filters=[egress], audit_sink=sink)
        client._client.set_text("see u@e.com here")
        result = await client.call_tool("my_tool", {})
        assert result == "see u@e.com here"
        assert len(sink_calls) >= 1


class TestMCPFilterChain:
    async def test_multiple_filters_run_in_order(self) -> None:
        # First filter would mask EMAIL; second filter would block AWS_KEY.
        # Text contains both: final result is the masked text (first filter
        # rewrites, second filter sees the rewritten text and finds AWS_KEY
        # — which isn't in it, so no block).
        email_rec = _StubRecognizer(
            "e",
            ["EMAIL"],
            [_finding("EMAIL", "u@e.com", 4, 13)],
        )
        email_scanner = _scanner_with_recognizer(email_rec, "EMAIL", DLPAction.MASK)
        aws_rec = _StubRecognizer("a", ["AWS_KEY"], [])
        aws_scanner = _scanner_with_recognizer(aws_rec, "AWS_KEY", DLPAction.BLOCK)
        egress_chain = [
            DLPEgressFilter(email_scanner),
            DLPEgressFilter(aws_scanner),
        ]
        client = _make_client(egress_filters=egress_chain)
        client._client.set_text("see u@e.com here")
        result = await client.call_tool("my_tool", {})
        assert result is not None
        assert "[EMAIL]" in result
        assert "u@e.com" not in result

    async def test_filter_chain_short_circuits_on_block(self) -> None:
        aws_rec = _StubRecognizer(
            "a",
            ["AWS_KEY"],
            [_finding("AWS_KEY", "AKIA", 0, 4)],
        )
        aws_scanner = _scanner_with_recognizer(aws_rec, "AWS_KEY", DLPAction.BLOCK)
        after_block_called = False

        class _PostBlockFilter:
            async def filter(
                self,
                content: str | bytes | object,
                context: dict | None = None,
            ) -> EgressResult:
                nonlocal after_block_called
                after_block_called = True
                return EgressResult(action=EgressAction.ALLOW, content=content)

        egress_chain: list[Any] = [
            DLPEgressFilter(aws_scanner),
            _PostBlockFilter(),
        ]
        client = _make_client(egress_filters=egress_chain)
        client._client.set_text("AKIA secret")
        result = await client.call_tool("my_tool", {})
        assert result is None
        assert not after_block_called


class TestMCPConstruction:
    def test_default_construction(self) -> None:
        client = HecateMCPClient()
        assert client.connected is False
        assert client._server_url is None
        assert client._egress_filters == []
        assert client._audit_sink is None

    def test_construction_with_filters_and_audit(self) -> None:
        sentinel = object()
        client = HecateMCPClient(
            server_url="mcp://example",
            egress_filters=[sentinel],
            audit_sink=lambda **_: None,
        )
        assert client._server_url == "mcp://example"
        assert client._egress_filters == [sentinel]
        assert client._audit_sink is not None
