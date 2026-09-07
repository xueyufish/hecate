"""Task phase detection for dynamic tool and context filtering (Context Engineering).

Classifies the current conversation into one of four task phases —
EXPLORE / CONVERGE / EXECUTE / VERIFY — using deterministic heuristics over
the message history (no LLM call). The detected phase is published on the
LLM span, the LLM_REQUEST event payload, and the tool-gate context (as
``task_phase``) so ``available_when`` expressions can gate tools per phase
(e.g. only expose write-tools during EXECUTE, verification tools during
VERIFY).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class TaskPhase(StrEnum):
    """Lifecycle phase of the task being executed in a conversation."""

    EXPLORE = "explore"
    CONVERGE = "converge"
    EXECUTE = "execute"
    VERIFY = "verify"


# Keyword tables are matched case-insensitively against recent message text.
_EXPLORE_KEYWORDS = (
    "explore",
    "research",
    "investigate",
    "find out",
    "what is",
    "what are",
    "how does",
    "how do",
    "look into",
    "gather",
    "compare options",
    "调查",
    "研究",
    "了解",
    "调研",
    "什么是",
    "为什么",
)
_CONVERGE_KEYWORDS = (
    "decide",
    "choose",
    "select",
    "recommend",
    "which option",
    "prefer",
    "trade-off",
    "tradeoff",
    "conclude",
    "summarize findings",
    "决定",
    "选择",
    "推荐",
    "结论",
    "权衡",
)
_EXECUTE_KEYWORDS = (
    "create",
    "write",
    "implement",
    "build",
    "run",
    "execute",
    "deploy",
    "fix",
    "update",
    "delete",
    "refactor",
    "generate",
    "apply",
    "make",
    "创建",
    "编写",
    "实现",
    "构建",
    "运行",
    "执行",
    "部署",
    "修复",
    "更新",
    "删除",
    "重构",
    "生成",
)
_VERIFY_KEYWORDS = (
    "verify",
    "check",
    "validate",
    "confirm",
    "review",
    "lint",
    "assert",
    "does it pass",
    "is it correct",
    "double-check",
    "验证",
    "检查",
    "确认",
    "审查",
    "校验",
)

_KEYWORD_HITS: tuple[tuple[TaskPhase, tuple[str, ...], int], ...] = (
    (TaskPhase.VERIFY, _VERIFY_KEYWORDS, 3),
    (TaskPhase.EXECUTE, _EXECUTE_KEYWORDS, 3),
    (TaskPhase.CONVERGE, _CONVERGE_KEYWORDS, 2),
    (TaskPhase.EXPLORE, _EXPLORE_KEYWORDS, 2),
)

_RECENT_MESSAGES = 4
_TOOL_RESULT_SAMPLE = 6


def detect_task_phase(
    messages: list[dict[str, Any]],
    channel_snapshot: dict[str, Any] | None = None,
) -> TaskPhase:
    """Classify the conversation's current task phase.

    Signals, in decreasing priority:

    1. **Failed tool results present** in the recent window → VERIFY
       (the agent should diagnose / confirm before proceeding).
    2. **Explicit verification intent** in recent user text → VERIFY.
    3. **Explicit execution intent** in recent user text → EXECUTE.
    4. **Convergence intent** → CONVERGE.
    5. **Phase carried in channel state** (``_task_phase``) when valid.
    6. Default → EXPLORE.

    Args:
        messages: Conversation messages (oldest to newest).
        channel_snapshot: Optional channel state; ``_task_phase`` key wins
            when it carries a valid phase name (set by orchestration logic
            that knows better than the heuristic).

    Returns:
        The detected TaskPhase.
    """
    if channel_snapshot:
        carried = channel_snapshot.get("_task_phase")
        if isinstance(carried, str):
            try:
                return TaskPhase(carried.lower())
            except ValueError:
                pass

    recent = [m for m in messages if isinstance(m, dict)][-_RECENT_MESSAGES:]
    text = " ".join(str(m.get("content", "")) for m in recent if m.get("role") in ("user", "assistant")).lower()

    if not text.strip():
        return TaskPhase.EXPLORE

    tool_results = [m for m in messages[-_TOOL_RESULT_SAMPLE:] if isinstance(m, dict) and m.get("role") == "tool"]
    if any(_looks_failed(m) for m in tool_results):
        return TaskPhase.VERIFY

    user_text = " ".join(str(m.get("content", "")) for m in recent if m.get("role") == "user").lower()

    if any(k in user_text for k in _VERIFY_KEYWORDS):
        return TaskPhase.VERIFY
    if any(k in user_text for k in _EXECUTE_KEYWORDS):
        return TaskPhase.EXECUTE

    scored: list[tuple[int, TaskPhase]] = []
    for phase, keywords, weight in _KEYWORD_HITS:
        hits = sum(1 for k in keywords if k in text)
        if hits:
            scored.append((hits * weight, phase))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    return TaskPhase.EXPLORE


def _looks_failed(tool_message: dict[str, Any]) -> bool:
    """Heuristic: does this tool-role message carry a failure?"""
    content = tool_message.get("content")
    if not isinstance(content, str):
        return False
    lowered = content.lower()
    markers = ("error", "failed", "exception", "traceback", "is_error")
    return any(marker in lowered for marker in markers)
