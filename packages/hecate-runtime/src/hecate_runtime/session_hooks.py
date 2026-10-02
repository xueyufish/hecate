"""Session lifecycle event hooks.

Provides four hook types for session lifecycle events:
- SessionStartHook — fires when a session begins or resumes
- SessionEndHook — fires when a session ends
- UserPromptSubmitHook — fires when a user submits a prompt
- PreCompactHook — fires before context compaction

Each hook returns a HookResult that can ALLOW, BLOCK, or INJECT context.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class HookAction(StrEnum):
    """Actions a session hook can return."""

    ALLOW = "allow"
    BLOCK = "block"
    INJECT = "inject"


@dataclass
class HookResult:
    """Structured return value from a session hook invocation.

    Attributes:
        action: The hook decision (allow, block, or inject).
        context_text: Text to inject into LLM context (for INJECT action).
        reason: Human-readable explanation for BLOCK action.
    """

    action: HookAction = HookAction.ALLOW
    context_text: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# SessionStartHook
# ---------------------------------------------------------------------------


class SessionStartHook(ABC):
    """Hook that fires when a session begins or resumes.

    Use cases: load workspace config, inject project context, initialize state.
    """

    @abstractmethod
    async def on_session_start(
        self,
        session_id: str,
        agent_id: str,
        source: str,
    ) -> HookResult:
        """Called when a session starts or resumes.

        Args:
            session_id: The session identifier.
            agent_id: The agent identifier.
            source: How the session started ("startup", "resume").

        Returns:
            HookResult with optional context_text to inject.
        """


class NoOpSessionStartHook(SessionStartHook):
    """Pass-through session start hook."""

    async def on_session_start(
        self,
        session_id: str,
        agent_id: str,
        source: str,
    ) -> HookResult:
        return HookResult(action=HookAction.ALLOW)


# ---------------------------------------------------------------------------
# SessionEndHook
# ---------------------------------------------------------------------------


class SessionEndHook(ABC):
    """Hook that fires when a session ends.

    Use cases: cleanup resources, save state, log session metrics.
    """

    @abstractmethod
    async def on_session_end(
        self,
        session_id: str,
        agent_id: str,
        reason: str,
    ) -> HookResult:
        """Called when a session ends.

        Args:
            session_id: The session identifier.
            agent_id: The agent identifier.
            reason: Why the session ended ("user_disconnect", "timeout", "close").

        Returns:
            HookResult.
        """


class NoOpSessionEndHook(SessionEndHook):
    """Pass-through session end hook."""

    async def on_session_end(
        self,
        session_id: str,
        agent_id: str,
        reason: str,
    ) -> HookResult:
        return HookResult(action=HookAction.ALLOW)


# ---------------------------------------------------------------------------
# UserPromptSubmitHook
# ---------------------------------------------------------------------------


class UserPromptSubmitHook(ABC):
    """Hook that fires when a user submits a prompt.

    Use cases: inject sprint context, validate requests, log prompts.
    Can BLOCK to reject the prompt.
    """

    @abstractmethod
    async def on_user_prompt_submit(
        self,
        session_id: str,
        prompt: str,
    ) -> HookResult:
        """Called when a user submits a prompt, before LLM processing.

        Args:
            session_id: The session identifier.
            prompt: The user's prompt text.

        Returns:
            HookResult. BLOCK rejects the prompt, INJECT adds context.
        """


class NoOpUserPromptSubmitHook(UserPromptSubmitHook):
    """Pass-through user prompt submit hook."""

    async def on_user_prompt_submit(
        self,
        session_id: str,
        prompt: str,
    ) -> HookResult:
        return HookResult(action=HookAction.ALLOW)


# ---------------------------------------------------------------------------
# PreCompactHook
# ---------------------------------------------------------------------------


class PreCompactHook(ABC):
    """Hook that fires before context compaction.

    Use cases: archive transcript, preserve important decisions.
    """

    @abstractmethod
    async def on_pre_compact(
        self,
        session_id: str,
        trigger: str,
    ) -> HookResult:
        """Called before context compaction occurs.

        Args:
            session_id: The session identifier.
            trigger: What triggered compaction ("manual", "auto").

        Returns:
            HookResult.
        """


class NoOpPreCompactHook(PreCompactHook):
    """Pass-through pre-compact hook."""

    async def on_pre_compact(
        self,
        session_id: str,
        trigger: str,
    ) -> HookResult:
        return HookResult(action=HookAction.ALLOW)
