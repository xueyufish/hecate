"""Agent State — per-session working state for agent execution.

Provides AgentState Pydantic model representing the volatile per-session
working state (conversation buffer, compressed summary, permission context,
tool/task sub-contexts) separated from the durable per-agent Environment.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """Per-session working state for an agent.

    AgentState captures the volatile working memory of a single agent session.
    It is persisted via AgentStateStore at the end of each execution call,
    enabling cross-process session resume.

    Fields:
        session_id: Unique identifier for this session.
        agent_id: Identifier of the agent this state belongs to.
        summary: Compressed summary of the conversation (reserved for future
            ContextEngine integration; empty in MVP).
        context: Current conversation buffer (list of message dicts).
        permission_context: Cached tool permission rules for this session.
        tool_context: Tool sub-context (active tool groups, file caches).
        task_context: Task sub-context (todo list, task tracking).
        environment_root: Filesystem path to the agent's environment.
        metadata: Arbitrary session-level metadata.
    """

    session_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    agent_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    summary: str = ""
    context: list[dict] = Field(default_factory=list)
    permission_context: dict = Field(default_factory=dict)
    tool_context: dict = Field(default_factory=dict)
    task_context: dict = Field(default_factory=dict)
    environment_root: str | None = None
    metadata: dict = Field(default_factory=dict)
