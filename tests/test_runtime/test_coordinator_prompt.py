"""Snapshots that pin the dynamic-orchestration prompt rubric verbatim.

These tests are deliberately simple: they read the public
``BENEFIT_BASED_DELEGATION_RUBRIC`` constant and assert its content is
byte-for-byte unchanged. Drift here is treated as a behavior change
because the rubric text is what constrains the planner LLM, and
silently editing it would change the planner's behavior without
updating tests.
"""

from __future__ import annotations

from hecate.runtime.dynamic_types import BENEFIT_BASED_DELEGATION_RUBRIC

EXPECTED_RUBRIC: str = """\
Benefit-based delegation rubric:
- Default to direct execution. Subagent delegation is an optimization, \
not a default response to complexity.
- Permit `task()` dispatch only when at least one of the following \
benefits clearly exceeds the aggregate cost:
  * Parallel latency win (subagents fan out and finish before a \
sequential chain would).
  * Specialist capability (a subagent has tools or knowledge a direct \
agent does not).
  * Context isolation (a subagent keeps intermediate tool noise out of \
the lead's message stream).
- HARD VETOES against parallel dispatch:
  * Output dependency: one task's result feeds into another dispatch in the \
same batch — those must be sequenced.
  * Overlapping mutable state: two parallel tasks would write to the \
same channel or share unshareable resources.
- Costs that must be weighed against the benefit:
  * Startup cost (LLM invocation overhead).
  * Duplicate-discovery cost (each subagent re-discovers context).
  * Synthesis cost (the lead must fold multiple outputs back together).
  * State-conflict cost (parallel writes need arbitration).
  * Side-effect cost (external actions are hard to roll back).
- Use the fewest useful subagents. A bounded sequential chain in one \
subagent is fine when specialist or context-isolation benefit clearly \
wins.
- Parallel scopes MUST be independent and non-overlapping.
- Re-evaluate each subsequent batch. Within-batch parallel benefit is \
retained, not new benefit is created.
- Concurrency is clamped by max_concurrent (range 1-4, default 3) and \
total dispatch is capped by max_total_tasks (range 1-50, default 6).
"""


def test_rubric_constant_matches_snapshot() -> None:
    """The public rubric constant must match the snapshot byte-for-byte."""
    assert BENEFIT_BASED_DELEGATION_RUBRIC == EXPECTED_RUBRIC


def test_rubric_constant_is_non_empty() -> None:
    """Defensive: an empty rubric would silently disable delegation guidance."""
    assert len(BENEFIT_BASED_DELEGATION_RUBRIC.strip()) > 0


def test_rubric_mentions_required_keywords() -> None:
    """Spot-check the substantive terms the planner depends on."""
    rubric = BENEFIT_BASED_DELEGATION_RUBRIC
    for keyword in (
        "Default to direct execution",
        "HARD VETOES",
        "Output dependency",
        "Overlapping mutable state",
        "max_concurrent",
        "max_total_tasks",
    ):
        assert keyword in rubric, f"rubric missing keyword: {keyword!r}"
