"""Observable tool facts and declared compensations (step7 local slice).

Tool facts capture what the model proposed (candidate tool calls) and what
was actually dispatched, as structured records for diagnostics — no hidden
reasoning is required or recorded. Compensation records exist only for
explicitly declared and authorized compensation operations; nothing infers an
inverse operation from a tool name.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ToolCandidate(BaseModel):
    """One tool invocation the model proposed or that was dispatched."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments_digest: str


class ToolFacts(BaseModel):
    """Candidate-vs-actual tool selection facts for one decision point.

    ``candidates`` lists every tool call the model produced at a decision
    point; ``actual`` identifies the call that was really dispatched. Facts
    are diagnostics for evaluation/dashboards, never a substitute for the
    authorization decision or the execution receipt.
    """

    model_config = ConfigDict(frozen=True)

    invocation_id: str
    agent_principal: str | None = None
    session_ref: str | None = None
    candidates: tuple[ToolCandidate, ...] = Field(default=())
    actual: ToolCandidate | None = None


class CompensationDeclaration(BaseModel):
    """A declared, explicitly authorized compensation operation.

    ``tool_name`` is the compensation tool; ``compensates_tool_name`` is the
    original operation it may compensate. A compensation is itself a new
    controlled action — it never claims to undo an already-transferred
    external effect.
    """

    model_config = ConfigDict(frozen=True)

    tool_name: str
    compensates_tool_name: str
    applies_when: str
    approved: bool = False


class CompensationRegistry:
    """Declares which (tool, compensates-tool) pairs may carry
    ``compensates_action_id``. Pairs are declared by tool owners and approved
    by policy — never inferred from names.
    """

    def __init__(self) -> None:
        self._pairs: dict[tuple[str, str], CompensationDeclaration] = {}

    def declare(self, declaration: CompensationDeclaration) -> None:
        self._pairs[(declaration.tool_name, declaration.compensates_tool_name)] = declaration

    def lookup(self, tool_name: str, compensates_tool_name: str) -> CompensationDeclaration | None:
        return self._pairs.get((tool_name, compensates_tool_name))

    def approved_declaration(self, tool_name: str, compensates_tool_name: str) -> CompensationDeclaration | None:
        declaration = self.lookup(tool_name, compensates_tool_name)
        if declaration is not None and declaration.approved:
            return declaration
        return None


class CompensationRecord(BaseModel):
    """The controlled compensation action itself."""

    model_config = ConfigDict(frozen=True)

    invocation_id: str
    compensates_action_id: str
    declaration: CompensationDeclaration
    recorded_at: datetime
    receipt: str | None = None
