"""Verification tests for docs/tutorials/01-first-agent.md.

Walks through every step of the "Build Your First Agent" tutorial and asserts
that the documented behavior actually holds against the running code. Designed
to surface discrepancies between the tutorial prose and the implementation.

Each test class corresponds to a tutorial step.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.core.deps import get_current_user_id
from hecate.main import app


@pytest.fixture(autouse=True)
def _override_user_id():
    """Pin a stable user id for all tests in this module."""

    def override() -> uuid.UUID:
        return uuid.UUID("00000000-0000-0000-0000-000000000000")

    app.dependency_overrides[get_current_user_id] = override
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


def _mock_llm(content: str = "OK", *, tool_calls=None) -> MagicMock:
    """Build a MagicMock stand-in for an LLM response."""

    return MagicMock(
        content=content,
        model="gpt-4o-mini",
        tool_calls=tool_calls,
        finish_reason="stop",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )


# --------------------------------------------------------------------------- #
# Step 2 — Create an agent (REST API)
# --------------------------------------------------------------------------- #
class TestStep2CreateAgent:
    """POST /api/agents must accept the documented body and return all fields."""

    async def test_create_returns_documented_fields(self, client):
        response = await client.post(
            "/api/agents",
            json={
                "name": "Tech Support Agent",
                "persona": "You are a patient, precise technical support engineer.",
                "model_config": {"model": "gpt-4o-mini", "temperature": 0.3},
                "mode": "chat",
                "risk_level": "LOW",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()

        # Core fields documented in the tutorial table + response example.
        for field in (
            "id",
            "workspace_id",
            "name",
            "persona",
            "model_config",
            "mode",
            "workflow_id",
            "tools",
            "skills",
            "knowledge_base_ids",
            "risk_level",
            "opening_remarks",
            "enable_suggestions",
            "guardrail_config",
            "created_at",
            "updated_at",
            "deleted",
            "deleted_at",
            "model_available",
        ):
            assert field in data, f"missing field in create response: {field}"

        assert data["name"] == "Tech Support Agent"
        assert data["mode"] == "chat"
        assert data["model_config"] == {"model": "gpt-4o-mini", "temperature": 0.3}
        assert data["risk_level"] == "LOW"
        assert data["deleted"] is False
        # id must be a UUID
        uuid.UUID(data["id"])

    async def test_create_rejects_invalid_mode(self, client):
        response = await client.post(
            "/api/agents",
            json={
                "name": "Bad",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "bogus",
            },
        )
        assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Step 3 — List and get agents
# --------------------------------------------------------------------------- #
class TestStep3ListAndGet:
    async def test_list_returns_items_and_total(self, client):
        for i in range(3):
            await client.post(
                "/api/agents",
                json={
                    "name": f"Agent {i}",
                    "model_config": {"model": "gpt-4o-mini"},
                    "mode": "chat",
                },
            )
        response = await client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        # Tutorial CLI renders a table; the API returns a paginated envelope.
        assert "items" in data and "total" in data
        assert data["total"] >= 3
        assert len(data["items"]) >= 3

    async def test_get_by_id(self, client):
        create = await client.post(
            "/api/agents",
            json={
                "name": "Solo",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        response = await client.get(f"/api/agents/{agent_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "Solo"

    async def test_get_unknown_returns_404(self, client):
        response = await client.get(f"/api/agents/{uuid.uuid4()}")
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Step 4 — Chat with an agent (model: "agent/<id>")
# --------------------------------------------------------------------------- #
class TestStep4ChatWithAgent:
    async def test_agent_prefix_resolves_and_injects_persona(self, client):
        create = await client.post(
            "/api/agents",
            json={
                "name": "Tech Support",
                "persona": "You are a patient support engineer.",
                "model_config": {"model": "gpt-4o-mini", "temperature": 0.3},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]

        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=_mock_llm("Exit code 137 means SIGKILL..."))
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": f"agent/{agent_id}",
                    "messages": [{"role": "user", "content": "Container exits with 137?"}],
                },
            )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["object"] == "chat.completion"
        assert data["choices"][0]["message"]["content"] == "Exit code 137 means SIGKILL..."

        # The persona must be injected as the system prompt.
        called_messages = mock_llm.chat.call_args.kwargs["messages"]
        assert called_messages[0]["role"] == "system"
        assert "patient support engineer" in called_messages[0]["content"]
        # The effective model must come from the agent config, not the request.
        assert mock_llm.chat.call_args.kwargs["model"] == "gpt-4o-mini"

    async def test_bare_uuid_model_is_not_resolved_as_agent(self, client):
        """A bare UUID (no ``agent/`` prefix) is treated as a raw model name.

        This documents why ``hecate chat`` (which sends a bare agent id) fails.
        """
        create = await client.post(
            "/api/agents",
            json={
                "name": "X",
                "persona": "persona text",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]

        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=_mock_llm())
            await client.post(
                "/v1/chat/completions",
                json={
                    "model": agent_id,  # bare UUID — no agent/ prefix
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        called_messages = mock_llm.chat.call_args.kwargs["messages"]
        # Persona is NOT injected because the agent was never resolved.
        assert not any(m["role"] == "system" for m in called_messages)
        # The bare UUID is passed straight through as the model name.
        assert mock_llm.chat.call_args.kwargs["model"] == agent_id

    async def test_unknown_agent_returns_404(self, client):
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": f"agent/{uuid.uuid4()}",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Step 5 — Bind a tool and watch tool calling
# --------------------------------------------------------------------------- #
class TestStep5AgentTools:
    """Verify that an agent's configured ``tools`` drive the tool-calling loop.

    The tutorial claims: after ``PUT /api/agents/{id}`` with ``tools:
    ["web_search"]``, chatting with ``model: "agent/<id>"`` makes the LLM
    propose tool calls that Hecate executes and feeds back. These tests pin
    down what actually happens.
    """

    async def test_update_sets_tools(self, client):
        create = await client.post(
            "/api/agents",
            json={
                "name": "Tech Support",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        response = await client.put(
            f"/api/agents/{agent_id}",
            json={"tools": ["web_search"]},
        )
        assert response.status_code == 200
        assert response.json()["tools"] == ["web_search"]

    async def test_agent_tools_forwarded_to_llm(self, client):
        """The chat endpoint loads the agent's configured tools and passes
        them to the LLM so it can propose tool calls."""
        create = await client.post(
            "/api/agents",
            json={
                "name": "Tech Support",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        await client.put(f"/api/agents/{agent_id}", json={"tools": ["web_search"]})

        with (
            patch("hecate.api.v1.chat.llm_service") as mock_llm,
            patch("hecate.api.v1.chat._build_tool_registry") as mock_registry_builder,
        ):
            mock_registry_builder.return_value = MagicMock()
            mock_llm.chat = AsyncMock(return_value=_mock_llm("answer"))
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": f"agent/{agent_id}",
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is the latest stable Python? Search the web.",
                        }
                    ],
                },
            )
        assert response.status_code == 200, response.text
        # The tools kwarg the LLM received must include web_search.
        tools_passed = mock_llm.chat.call_args.kwargs.get("tools")
        assert tools_passed, "Expected agent tools to be forwarded to the LLM"
        assert any(t["function"]["name"] == "web_search" for t in tools_passed)

    async def test_agent_tools_trigger_tool_loop(self, client):
        """When the LLM emits a tool call, the registry executes it and the
        result is fed back into the next LLM round."""
        create = await client.post(
            "/api/agents",
            json={
                "name": "Tech Support",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        await client.put(f"/api/agents/{agent_id}", json={"tools": ["web_search"]})

        fake_registry = MagicMock()
        fake_registry.execute = AsyncMock(return_value={"results": [{"title": "Python 3.13", "url": "..."}]})

        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "web_search", "arguments": '{"query": "latest python"}'},
        }

        with (
            patch("hecate.api.v1.chat.llm_service") as mock_llm,
            patch("hecate.api.v1.chat._build_tool_registry", return_value=fake_registry),
        ):
            mock_llm.chat = AsyncMock(
                side_effect=[
                    _mock_llm("", tool_calls=[tool_call]),
                    _mock_llm("Python 3.13 is the latest stable release."),
                ]
            )
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": f"agent/{agent_id}",
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is the latest stable Python? Search the web.",
                        }
                    ],
                },
            )

        assert response.status_code == 200, response.text
        data = response.json()
        # Final answer comes back after the tool round-trip.
        assert data["choices"][0]["message"]["content"] == "Python 3.13 is the latest stable release."

        # The registry executed web_search exactly once with parsed arguments.
        fake_registry.execute.assert_awaited_once()
        name, args = fake_registry.execute.await_args.args
        assert name == "web_search"
        assert args == {"query": "latest python"}

        # The second LLM round received the injected tool result.
        second_messages = mock_llm.chat.call_args_list[1].kwargs["messages"]
        assert any(m["role"] == "tool" for m in second_messages)
        # And tools were forwarded on the first round.
        first_tools = mock_llm.chat.call_args_list[0].kwargs.get("tools")
        assert first_tools
        assert any(t["function"]["name"] == "web_search" for t in first_tools)

    async def test_agent_tools_stream_loop(self, client):
        """Streaming variant: tool iterations run silently, only the final
        answer is streamed as SSE chunks."""
        create = await client.post(
            "/api/agents",
            json={
                "name": "Tech Support",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        await client.put(f"/api/agents/{agent_id}", json={"tools": ["web_search"]})

        fake_registry = MagicMock()
        fake_registry.execute = AsyncMock(return_value={"results": [{"title": "Python 3.13", "url": "..."}]})

        tool_call = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "web_search", "arguments": '{"query": "latest python"}'},
        }

        # chat_stream is called once per tool-loop round: round 1 streams a
        # tool_call, round 2 streams the final answer.
        rounds = [
            [{"content": None, "tool_calls": [tool_call], "finish_reason": "tool_calls"}],
            [
                {"content": "Python 3.13 ", "finish_reason": None},
                {"content": "is the latest.", "finish_reason": "stop"},
            ],
        ]

        async def fake_chat_stream(*args, **kwargs):
            for chunk in rounds.pop(0):
                yield chunk

        with (
            patch("hecate.api.v1.chat.llm_service") as mock_llm,
            patch("hecate.api.v1.chat._build_tool_registry", return_value=fake_registry),
        ):
            mock_llm.chat_stream = fake_chat_stream
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": f"agent/{agent_id}",
                    "messages": [
                        {
                            "role": "user",
                            "content": "What is the latest stable Python? Search the web.",
                        }
                    ],
                    "stream": True,
                },
            )

        assert response.status_code == 200
        body = response.text
        assert "Python 3.13 " in body
        assert "is the latest." in body
        assert "data: [DONE]" in body
        fake_registry.execute.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Step 6 — Multi-turn conversations with sessions
# --------------------------------------------------------------------------- #
class TestStep6Sessions:
    async def test_session_id_accepted(self, client):
        create = await client.post(
            "/api/agents",
            json={
                "name": "X",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        sid = str(uuid.uuid4())

        with patch("hecate.api.v1.chat.llm_service") as mock_llm:
            mock_llm.chat = AsyncMock(return_value=_mock_llm("reply"))
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": f"agent/{agent_id}",
                    "messages": [{"role": "user", "content": "first"}],
                    "session_id": sid,
                },
            )
        assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Step 8 — Export, import, delete
# --------------------------------------------------------------------------- #
class TestStep8ManageAgents:
    async def test_export_returns_portable_json(self, client):
        create = await client.post(
            "/api/agents",
            json={
                "name": "Export Me",
                "persona": "p",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
                "tools": ["web_search"],
            },
        )
        agent_id = create.json()["id"]
        response = await client.get(f"/api/agents/{agent_id}/export")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "1.0"
        assert data["agent"]["name"] == "Export Me"
        assert data["agent"]["tools"] == ["web_search"]

    async def test_import_creates_new_agent(self, client):
        payload = {
            "version": "1.0",
            "exported_at": "2026-01-15T10:30:00+00:00",
            "agent": {
                "name": "Imported",
                "persona": "imported persona",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
                "tools": [],
                "skills": [],
                "knowledge_base_ids": [],
                "risk_level": "LOW",
            },
        }
        response = await client.post("/api/agents/import", json=payload)
        assert response.status_code == 201
        assert response.json()["name"] == "Imported"

    async def test_soft_delete_hides_from_list(self, client):
        create = await client.post(
            "/api/agents",
            json={
                "name": "Delete Me",
                "model_config": {"model": "gpt-4o-mini"},
                "mode": "chat",
            },
        )
        agent_id = create.json()["id"]
        response = await client.delete(f"/api/agents/{agent_id}")
        assert response.status_code == 204

        get_resp = await client.get(f"/api/agents/{agent_id}")
        assert get_resp.status_code == 404


# --------------------------------------------------------------------------- #
# Step 7 — Interactive CLI chat: model-string contract
# --------------------------------------------------------------------------- #
class TestStep7CliChatContract:
    """The CLI ``chat send``/``chat interactive`` commands build the request
    body in ``hecate.cli.commands.chat``. The tutorial promises agent-aware
    chat; verify the ``model`` field the CLI emits is agent-addressable.
    """

    def test_cli_chat_uses_agent_prefix(self):
        import inspect

        from hecate.cli.commands import chat as chat_cmd

        source = inspect.getsource(chat_cmd)
        # The server resolves an agent only when model starts with "agent/".
        # Both ``send`` and ``interactive`` must therefore emit "agent/<id>";
        # a bare agent UUID would be treated as a raw model name and fail.
        assert '"agent/"' in source or "'agent/'" in source or 'f"agent/' in source, (
            "hecate chat send/interactive sets model to the bare agent_id; server will not resolve it as an agent"
        )
