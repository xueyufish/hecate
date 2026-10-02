"""Egress content filters for the outbound DLP gate.

Per design.md §D3, content egressing through MCP responses, A2A
messages, webhooks, and LLM tool outputs is passed through a
filter chain. The :class:`EgressFilter` ABC defines the contract:
implementations take content + a context dict and return an
:class:`EgressResult` that the caller renders (or withholds) as
appropriate.

The concrete :class:`DLPEgressFilter` wraps a :class:`DLPScanner`
and maps :class:`DLPAction` to :class:`EgressAction`:

* ``ALLOW`` → ``EgressAction.ALLOW`` (content unchanged)
* ``MASK``  → ``EgressAction.MODIFIED`` (text rewritten with
  ``[ENTITY_TYPE]`` placeholders)
* ``AUDIT`` → ``EgressAction.ALLOW`` (content unchanged; ``audit_data``
  is populated so the security team can review the detection)
* ``BLOCK`` → ``EgressAction.BLOCK`` (content withheld; ``content=None``)

Non-text content (bytes, images, structured objects) is passed through
unchanged with an audit record. DLP never modifies bytes — that is the
job of a different filter (e.g., content-type-aware redaction).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # ops is a sibling domain: annotations only here. The scanner is
    # injected via guardrail assembly; the action enum is imported at
    # its runtime use site in ``DLPEgressFilter.filter``.
    from hecate.ops.dlp.result import DLPFinding
    from hecate.ops.dlp.scanner import DLPScanner


class EgressAction(StrEnum):
    """Outcome of an egress filter on a piece of content."""

    ALLOW = "allow"
    BLOCK = "block"
    MODIFIED = "modified"


@dataclass
class EgressResult:
    """The result of running an egress filter on a piece of content.

    Attributes:
        action: The filter's verdict — see :class:`EgressAction`.
        content: The text to render. ``None`` when ``action == BLOCK``.
            For ``ALLOW`` and ``AUDIT``, this is the original text. For
            ``MODIFIED``, this is the rewritten text.
        findings: DLP findings the filter acted on.
        audit_data: Structured records suitable for
            :class:`SecurityFindingModel`. Always populated so audit
            consumers can review the detection even when the content
            passes through unchanged.
    """

    action: EgressAction
    content: str | None
    findings: list[DLPFinding] = field(default_factory=list)
    audit_data: list[dict[str, Any]] = field(default_factory=list)


class EgressFilter(ABC):
    """Abstract base class for content egress filters."""

    @abstractmethod
    async def filter(
        self,
        content: str | bytes | Any,
        context: dict[str, Any] | None = None,
    ) -> EgressResult:
        """Inspect and possibly transform content before it egresses.

        ``content`` may be a string (the common case for LLM tool
        outputs) or a non-text payload (for tool results that return
        structured data). Implementations should pass non-text through
        with an audit record and ``EgressAction.ALLOW``.
        """


def _build_audit_records(
    findings: list[DLPFinding],
) -> list[dict[str, Any]]:
    return [
        {
            "entity_type": finding.entity_type,
            "value": finding.value,
            "start": finding.start,
            "end": finding.end,
            "score": finding.score,
            "recognizer": finding.recognizer,
        }
        for finding in findings
    ]


class DLPEgressFilter(EgressFilter):
    """Egress filter that delegates detection to a :class:`DLPScanner`."""

    def __init__(
        self,
        scanner: DLPScanner,
        *,
        direction: str = "tool_output",
        agent_id: str | None = None,
        workspace_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        self._scanner = scanner
        self._direction = direction
        self._scope = {
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "org_id": org_id,
        }

    @property
    def scanner(self) -> DLPScanner:
        return self._scanner

    async def filter(
        self,
        content: str | bytes | Any,
        context: dict[str, Any] | None = None,
    ) -> EgressResult:
        if not isinstance(content, str):
            return EgressResult(
                action=EgressAction.ALLOW,
                content=content,
                findings=[],
                audit_data=[
                    {
                        "reason": "non_text_content",
                        "type": type(content).__name__,
                    }
                ],
            )

        result = self._scanner.scan(
            content,
            self._direction,
            agent_id=self._scope["agent_id"],
            workspace_id=self._scope["workspace_id"],
            org_id=self._scope["org_id"],
        )

        from hecate.ops.dlp.result import DLPAction

        if result.action == DLPAction.BLOCK:
            return EgressResult(
                action=EgressAction.BLOCK,
                content=None,
                findings=result.findings,
                audit_data=result.audit_data,
            )

        if result.action == DLPAction.MASK:
            return EgressResult(
                action=EgressAction.MODIFIED,
                content=result.text if result.text is not None else content,
                findings=result.findings,
                audit_data=result.audit_data,
            )

        return EgressResult(
            action=EgressAction.ALLOW,
            content=content,
            findings=result.findings,
            audit_data=result.audit_data,
        )
