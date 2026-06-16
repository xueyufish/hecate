"""Tests for ContextAssembler suggestion mode."""

from __future__ import annotations

from uuid import uuid4

from hecate.services.context.assembler import ContextAssembler
from hecate.services.context.budget import BudgetManager
from hecate.services.context.types import AssembledContext, SessionMeta


class TestSuggestionMode:
    """Tests for suggestion_mode parameter in ContextAssembler.assemble()."""

    def test_opening_mode_returns_single_system_message(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="CodeBot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="opening")

        assert isinstance(context, AssembledContext)
        assert len(context.messages) == 1
        assert context.messages[0]["role"] == "system"
        assert "CodeBot" in context.messages[0]["content"]
        assert "suggested questions" in context.messages[0]["content"].lower()

    def test_opening_mode_includes_persona(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [{"role": "user", "content": "Hi"}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="HelperBot",
            agent_persona="A friendly coding assistant specialized in Python",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="opening")

        assert "HelperBot" in context.messages[0]["content"]
        assert "A friendly coding assistant specialized in Python" in context.messages[0]["content"]

    def test_opening_mode_excludes_persona_when_none(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [{"role": "user", "content": "Hi"}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="SimpleBot",
            agent_persona=None,
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="opening")

        assert "SimpleBot" in context.messages[0]["content"]
        assert "Persona:" not in context.messages[0]["content"]

    def test_opening_mode_returns_empty_tools(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [{"role": "user", "content": "Hi"}]
        tools = [{"type": "function", "function": {"name": "search", "description": "Search"}}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, tools, session_meta, suggestion_mode="opening")

        assert context.tools == []

    def test_opening_mode_metadata(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [{"role": "user", "content": "Hi"}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="opening")

        assert context.metadata.get("suggestion_mode") == "opening"

    def test_followup_mode_with_conversation_history(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "How do I install it?"},
            {"role": "assistant", "content": "You can install Python from python.org."},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="CodeBot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="followup")

        assert isinstance(context, AssembledContext)
        assert len(context.messages) == 1
        assert context.messages[0]["role"] == "system"
        assert "CodeBot" in context.messages[0]["content"]
        assert "What is Python?" in context.messages[0]["content"]
        assert "follow-up questions" in context.messages[0]["content"].lower()

    def test_followup_mode_limits_to_last_two_turns(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
            {"role": "user", "content": "Third question"},
            {"role": "assistant", "content": "Third answer"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="followup")

        system_content = context.messages[0]["content"]
        assert "First question" not in system_content
        assert "Second question" in system_content
        assert "Third question" in system_content

    def test_followup_mode_returns_empty_tools(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        tools = [{"type": "function", "function": {"name": "search", "description": "Search"}}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, tools, session_meta, suggestion_mode="followup")

        assert context.tools == []

    def test_followup_mode_metadata(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="followup")

        assert context.metadata.get("suggestion_mode") == "followup"

    def test_followup_mode_with_no_user_assistant_turns(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [{"role": "system", "content": "You are helpful."}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode="followup")

        assert "(No conversation history yet)" in context.messages[0]["content"]

    def test_none_mode_proceeds_with_standard_assembly(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble(messages, [], session_meta, suggestion_mode=None)

        assert isinstance(context, AssembledContext)
        assert len(context.messages) == 3
        assert context.total_tokens > 0
        assert len(context.priorities) == len(context.messages)
        assert "suggestion_mode" not in context.metadata

    def test_none_mode_preserves_existing_behavior(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        tools = [{"type": "function", "function": {"name": "search", "description": "Search"}}]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context_default = assembler.assemble(messages, tools, session_meta)
        context_explicit_none = assembler.assemble(messages, tools, session_meta, suggestion_mode=None)

        assert context_default.messages == context_explicit_none.messages
        assert context_default.tools == context_explicit_none.tools
        assert context_default.total_tokens == context_explicit_none.total_tokens

    def test_suggestion_mode_with_empty_messages(self) -> None:
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
            agent_name="Bot",
        )

        context = assembler.assemble([], [], session_meta, suggestion_mode="opening")

        assert context.messages == []
        assert context.total_tokens == 0
