"""Tests for AgentWorker — AGENT node execution via EnginePort."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from hecate.engine.ports import EnginePort
from hecate.engine.types import WorkerResult
from hecate.engine.workers.agent_worker import AgentWorker


class MockAgentPort(EnginePort):
    """Mock EnginePort that returns canned agent responses."""

    def __init__(self, response: str = "Mock agent response", should_fail: bool = False) -> None:
        self._response = response
        self._should_fail = should_fail
        self.last_agent_id: uuid.UUID | None = None
        self.last_messages: list[dict] | None = None

    async def agent_execute(
        self,
        agent_id: uuid.UUID,
        messages: list[dict],
        channel_snapshot: dict,
        context: dict | None = None,
    ) -> dict:
        if self._should_fail:
            raise ValueError(f"Agent {agent_id} not found")
        self.last_agent_id = agent_id
        self.last_messages = messages
        return {"response": self._response, "usage": {"tokens": 42}}

    async def llm_invoke(self, messages: list[dict], config: dict) -> AsyncGenerator[str, None]:
        yield ""

    async def tool_execute(self, name: str, args: dict, context: dict | None = None) -> Any:
        return None

    async def knowledge_query(self, query: str, kb_ids: list[uuid.UUID]) -> list[dict]:
        return []

    async def checkpoint_save(self, state: dict) -> uuid.UUID:
        return uuid.uuid4()

    async def checkpoint_load(self, checkpoint_id: uuid.UUID) -> dict:
        return {}

    async def conversation_load(self, session_id: uuid.UUID) -> list[dict]:
        return []

    async def conversation_save(self, session_id: uuid.UUID, messages: list[dict]) -> None:
        pass


async def test_agent_worker_valid_agent_id():
    """AgentWorker calls port.agent_execute with correct arguments."""
    agent_id = uuid.uuid4()
    port = MockAgentPort(response="Specialist response")
    worker = AgentWorker(port=port)

    result = await worker.execute(
        node_id="agent_1",
        node_config={"agent_id": str(agent_id)},
        channel_snapshot={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert isinstance(result, WorkerResult)
    assert result.node_id == "agent_1"
    assert result.error is None
    assert result.channel_updates["messages"][0]["role"] == "assistant"
    assert result.channel_updates["messages"][0]["content"] == "Specialist response"
    assert port.last_agent_id == agent_id
    assert port.last_messages == [{"role": "user", "content": "hello"}]


async def test_agent_worker_missing_agent_id():
    """AgentWorker returns error when agent_id is missing from config."""
    port = MockAgentPort()
    worker = AgentWorker(port=port)

    result = await worker.execute(
        node_id="agent_1",
        node_config={},
        channel_snapshot={},
    )

    assert result.error is not None
    assert "missing required config field 'agent_id'" in str(result.error)


async def test_agent_worker_invalid_agent_id():
    """AgentWorker returns error when agent_id is not a valid UUID."""
    port = MockAgentPort()
    worker = AgentWorker(port=port)

    result = await worker.execute(
        node_id="agent_1",
        node_config={"agent_id": "not-a-uuid"},
        channel_snapshot={},
    )

    assert result.error is not None
    assert "invalid agent_id" in str(result.error)


async def test_agent_worker_port_failure():
    """AgentWorker returns error when port.agent_execute raises."""
    agent_id = uuid.uuid4()
    port = MockAgentPort(should_fail=True)
    worker = AgentWorker(port=port)

    result = await worker.execute(
        node_id="agent_1",
        node_config={"agent_id": str(agent_id)},
        channel_snapshot={"messages": []},
    )

    assert result.error is not None
    assert "not found" in str(result.error)


async def test_agent_worker_context_isolation():
    """AgentWorker passes only messages from channel_snapshot to port."""
    agent_id = uuid.uuid4()
    port = MockAgentPort()
    worker = AgentWorker(port=port)

    await worker.execute(
        node_id="isolated_agent",
        node_config={"agent_id": str(agent_id)},
        channel_snapshot={
            "messages": [{"role": "user", "content": "test"}],
            "internal_state": "should_not_be_passed",
        },
    )

    assert port.last_messages == [{"role": "user", "content": "test"}]


async def test_agent_worker_non_list_messages():
    """AgentWorker handles non-list messages gracefully."""
    agent_id = uuid.uuid4()
    port = MockAgentPort()
    worker = AgentWorker(port=port)

    result = await worker.execute(
        node_id="agent_1",
        node_config={"agent_id": str(agent_id)},
        channel_snapshot={"messages": "not a list"},
    )

    assert result.error is None
    assert port.last_messages == []


async def test_agent_worker_uuid_object():
    """AgentWorker accepts UUID object (not just string) as agent_id."""
    agent_id = uuid.uuid4()
    port = MockAgentPort()
    worker = AgentWorker(port=port)

    result = await worker.execute(
        node_id="agent_1",
        node_config={"agent_id": agent_id},
        channel_snapshot={"messages": []},
    )

    assert result.error is None
    assert port.last_agent_id == agent_id
