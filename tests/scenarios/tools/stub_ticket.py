"""Deterministic stub enterprise ticket service for the scenario pack.

Stands in for the "write to a test ticket system" enterprise tool required by
step1's acceptance sample (read material -> summarize -> review -> approve ->
write result). Records every invocation with its arguments and context so
scenarios can assert business outcomes and side-effect counts deterministically.

Injection modes let scenarios exercise the success, business-rejection, and
fault paths without any real external system.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class StubTicketMode(StrEnum):
    """Deterministic behavior injected into the stub ticket service."""

    OK = "ok"
    BUSINESS_REJECTED = "business_rejected"
    FAULT = "fault"


@dataclass
class TicketCall:
    """One recorded invocation of the stub ticket tool."""

    tool_name: str
    arguments: dict[str, Any]
    context: dict[str, Any]
    ticket_id: str | None
    outcome: str


@dataclass
class StubTicketService:
    """In-memory stand-in for a real ticketing backend.

    The scenario fixtures bind this service to the ``create_test_ticket`` tool
    name at the ``ToolRegistry`` boundary; the policy, approval, and registry
    code paths above it all run for real.
    """

    tool_name: str = "create_test_ticket"
    mode: StubTicketMode = StubTicketMode.OK
    _calls: list[TicketCall] = field(default_factory=list)
    _counter: itertools.count = field(default_factory=itertools.count)

    @property
    def calls(self) -> list[TicketCall]:
        return self._calls

    @property
    def side_effect_count(self) -> int:
        """Number of invocations that reached the (simulated) backend write."""
        return len(self._calls)

    def reset(self, mode: StubTicketMode = StubTicketMode.OK) -> None:
        self.mode = mode
        self._calls.clear()

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
        if name != self.tool_name:
            raise ValueError(f"StubTicketService only handles '{self.tool_name}', got '{name}'")
        args = args or {}
        context = context or {}
        if self.mode is StubTicketMode.FAULT:
            raise ConnectionError("stub ticket backend unreachable")
        if self.mode is StubTicketMode.BUSINESS_REJECTED:
            self._calls.append(
                TicketCall(
                    tool_name=name,
                    arguments=args,
                    context=context,
                    ticket_id=None,
                    outcome="business_rejected",
                )
            )
            raise ValueError("stub ticket rejected: duplicate summary for this period")
        ticket_id = f"TKT-{next(self._counter):04d}"
        self._calls.append(
            TicketCall(tool_name=name, arguments=args, context=context, ticket_id=ticket_id, outcome="created")
        )
        return {"ticket_id": ticket_id, "status": "open", "title": args.get("title", "")}
