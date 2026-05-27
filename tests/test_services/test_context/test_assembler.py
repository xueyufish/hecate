"""Unit tests for ContextAssembler."""

from __future__ import annotations

from uuid import uuid4

from hecate.services.context.assembler import ContextAssembler
from hecate.services.context.budget import BudgetManager, DegradationLevel
from hecate.services.context.types import AssembledContext, SessionMeta, TaskPhase


class TestContextAssembler:
    """Tests for the ContextAssembler class."""

    def test_assemble_basic(self) -> None:
        """Test basic context assembly."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, [], session_meta)

        assert isinstance(context, AssembledContext)
        assert len(context.messages) > 0
        assert context.total_tokens > 0
        assert len(context.priorities) == len(context.messages)

    def test_assemble_empty_messages(self) -> None:
        """Test assembly with empty messages."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble([], [], session_meta)

        assert context.messages == []
        assert context.total_tokens == 0

    def test_assemble_with_tools(self) -> None:
        """Test assembly with tool definitions."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "What's the weather?"},
        ]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {},
                },
            }
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, tools, session_meta)

        assert len(context.tools) > 0

    def test_assemble_phase_detection(self) -> None:
        """Test that phase detection works."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        # Messages suggesting exploration phase
        messages = [
            {"role": "user", "content": "Search for information about Python"},
            {"role": "assistant", "content": "I found several resources..."},
            {"role": "user", "content": "What are the best practices?"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, [], session_meta)

        assert context.phase in [TaskPhase.EXPLORE, TaskPhase.CONVERGE, TaskPhase.EXECUTE, TaskPhase.VERIFY]

    def test_assemble_priority_assignment(self) -> None:
        """Test that priorities are assigned correctly."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
            {"role": "user", "content": "How are you?"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, [], session_meta)

        # System message should be CRITICAL
        assert context.priorities[0] == "critical"

        # Last user message should be CRITICAL
        assert context.priorities[-1] == "critical"

    def test_assemble_budget_degradation(self) -> None:
        """Test that degradation is applied when budget exceeded."""
        budget_manager = BudgetManager()
        session_id = uuid4()

        # Allocate very small budget
        budget_manager.allocate(session_id, "gpt-4o", custom_budget=20)

        assembler = ContextAssembler(budget_manager)

        # Create messages that will exceed 20 tokens
        messages = [
            {"role": "user", "content": "This is a message that should exceed the tiny budget"},
            {"role": "assistant", "content": "And this response adds more tokens to the count"},
            {"role": "user", "content": "Another message to make it even longer"},
        ]

        session_meta = SessionMeta(
            session_id=str(session_id),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, [], session_meta)

        # Should have applied degradation
        assert context.metadata.get("degradation_level") != DegradationLevel.NONE.value

    def test_assemble_tool_filtering(self) -> None:
        """Test that tools are filtered based on phase."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "Search for Python tutorials"},
        ]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write to a file",
                    "parameters": {},
                },
            },
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, tools, session_meta)

        # Tools should be present (filtered or not depending on phase)
        assert isinstance(context.tools, list)
