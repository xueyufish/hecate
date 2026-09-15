"""Tests for top-level prompt_override consumption in AgentExecutionPort (6.19).

The optimization pipeline rolls out candidate templates by injecting them as
a per-invocation ``prompt_override`` on ``agent_execute``. Three behaviors
are contracted: a definition-level override replaces the agent's persona,
a delegation-context override (the ``ctx`` key written by AgentTool) is
honored as a fallback, and absent overrides leave behavior unchanged.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.models.agent import AgentModel
from hecate.runtime.agent_execution_port import AgentExecutionPort
from hecate.runtime.agent_tool import AgentDefinition


def _make_agent(persona: str = "Production persona.") -> AgentModel:
    agent = AgentModel(
        name="OptimizedAgent",
        persona=persona,
        model_config_db={"model": "gpt-4o"},
        tools=[],
        knowledge_base_ids=[],
    )
    agent.id = uuid.uuid4()
    agent.workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")
    return agent


def _mock_llm(capture: dict) -> MagicMock:
    mock_response = MagicMock()
    mock_response.content = "ok"
    mock_response.usage = {"total_tokens": 7}
    mock_response.model = "gpt-4o"
    mock_llm = MagicMock()

    async def _chat(**kwargs):
        capture["messages"] = kwargs["messages"]
        return mock_response

    mock_llm.chat = AsyncMock(side_effect=_chat)
    return mock_llm


async def _execute(db_session, agent, capture, **kwargs):
    port = AgentExecutionPort(db_session)
    with patch("hecate_llm.service.llm_service", _mock_llm(capture)):
        return await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "hello"}],
            channel_snapshot={},
            **kwargs,
        )


def _system_content(capture: dict) -> str:
    return capture["messages"][0]["content"]


@pytest.mark.asyncio
async def test_prompt_override_from_agent_definition_replaces_persona(db_session):
    """A definition-level prompt_override is used as the system prompt."""
    agent = _make_agent()
    db_session.add(agent)
    await db_session.flush()

    capture: dict = {}
    definition = AgentDefinition(agent_id=agent.id, description="rollout", prompt_override="Candidate template v2")
    await _execute(db_session, agent, capture, agent_definition=definition)

    content = _system_content(capture)
    assert content.startswith("Candidate template v2")
    assert "Production persona." not in content


@pytest.mark.asyncio
async def test_prompt_override_from_delegation_context(db_session):
    """The context key written by AgentTool (delegation path) is honored."""
    agent = _make_agent()
    db_session.add(agent)
    await db_session.flush()

    capture: dict = {}
    await _execute(db_session, agent, capture, context={"prompt_override": "Delegated template"})

    assert _system_content(capture).startswith("Delegated template")


@pytest.mark.asyncio
async def test_definition_override_wins_over_context_key(db_session):
    """When both are present the agent_definition override takes precedence."""
    agent = _make_agent()
    db_session.add(agent)
    await db_session.flush()

    capture: dict = {}
    definition = AgentDefinition(agent_id=agent.id, description="rollout", prompt_override="Definition wins")
    await _execute(
        db_session, agent, capture, context={"prompt_override": "Context loses"}, agent_definition=definition
    )

    assert _system_content(capture).startswith("Definition wins")


@pytest.mark.asyncio
async def test_no_prompt_override_keeps_persona(db_session):
    """Without an override (definition None or null field) behavior is unchanged."""
    agent = _make_agent(persona="Production persona.")
    db_session.add(agent)
    await db_session.flush()

    capture: dict = {}
    await _execute(db_session, agent, capture)
    assert _system_content(capture).startswith("Production persona.")

    capture.clear()
    definition = AgentDefinition(agent_id=agent.id, description="rollout", prompt_override=None)
    await _execute(db_session, agent, capture, agent_definition=definition)
    assert _system_content(capture).startswith("Production persona.")


@pytest.mark.asyncio
async def test_override_keeps_tools_and_model_from_agent_config(db_session):
    """Override changes only the system prompt — model config still comes from the agent."""
    agent = _make_agent()
    agent.model_config_db = {"model": "test-model-x"}
    db_session.add(agent)
    await db_session.flush()

    capture: dict = {}
    definition = AgentDefinition(agent_id=agent.id, description="rollout", prompt_override="Candidate template")
    port = AgentExecutionPort(db_session)
    with patch("hecate_llm.service.llm_service", _mock_llm(capture)) as mock_llm:
        await port.agent_execute(
            agent_id=agent.id,
            messages=[{"role": "user", "content": "hello"}],
            channel_snapshot={},
            agent_definition=definition,
        )

    assert _system_content(capture).startswith("Candidate template")
    assert mock_llm.chat.call_args.kwargs.get("model") == "test-model-x"
