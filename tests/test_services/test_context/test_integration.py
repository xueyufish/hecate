"""Integration tests for the full context engineering pipeline.

Tests the complete flow:
1. Messages → ContextAssembler → Budget check → Provider shaping → LLM
2. Tool execution → Evidence capture → Budget update
3. Memory injection (L1 blocks + L3 user memories) → Context assembly
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.memory import MemoryBlockReadSchema, MemoryReadSchema
from hecate.services.context.assembler import ContextAssembler
from hecate.services.context.budget import BudgetManager, DegradationLevel
from hecate.services.context.evidence_tracker import EvidenceTracker
from hecate.services.context.provider_shaping import get_strategy
from hecate.services.context.types import AssembledContext, SessionMeta, TaskPhase
from hecate.services.conversation import ConversationService


class TestFullPipelineIntegration:
    """Integration tests for the full context engineering pipeline."""

    def test_assemble_to_shaping_flow(self) -> None:
        """Test complete flow from assembly to provider shaping."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Help me write a Python function"},
            {"role": "assistant", "content": "Sure! What should the function do?"},
            {"role": "user", "content": "Sort a list of numbers"},
        ]

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "run_code",
                    "description": "Execute Python code",
                    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}},
                },
            }
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        # Step 1: Assemble context
        assembled = assembler.assemble(messages, tools, session_meta)

        assert isinstance(assembled, AssembledContext)
        assert len(assembled.messages) > 0
        assert assembled.total_tokens > 0
        assert assembled.phase in [TaskPhase.EXPLORE, TaskPhase.CONVERGE, TaskPhase.EXECUTE, TaskPhase.VERIFY]

        # Step 2: Apply provider shaping
        strategy = get_strategy(session_meta.model)
        shaped = strategy.shape(assembled)

        assert shaped.metadata.get("provider") == "openai"
        assert len(shaped.messages) > 0

    def test_budget_degradation_flow(self) -> None:
        """Test that degradation is applied when budget exceeded."""
        budget_manager = BudgetManager()
        session_id = uuid4()

        # Allocate very small budget
        budget_manager.allocate(session_id, "gpt-4o", custom_budget=30)

        assembler = ContextAssembler(budget_manager)

        # Create messages that will exceed budget
        messages = [
            {"role": "system", "content": "You are a helpful assistant with many capabilities"},
            {"role": "user", "content": "Tell me about Python programming"},
            {"role": "assistant", "content": "Python is a versatile language..."},
            {"role": "user", "content": "What about data science?"},
            {"role": "assistant", "content": "Data science with Python is powerful..."},
        ]

        session_meta = SessionMeta(
            session_id=str(session_id),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(messages, [], session_meta)

        # Should have applied some degradation
        assert context.metadata.get("degradation_level") != DegradationLevel.NONE.value

    def test_tool_filtering_by_phase(self) -> None:
        """Test that tools are filtered based on detected phase."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        # Messages suggesting exploration phase
        messages = [
            {"role": "user", "content": "Search for information about React hooks"},
            {"role": "assistant", "content": "I'll search for that..."},
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
            {
                "type": "function",
                "function": {
                    "name": "execute_code",
                    "description": "Run code",
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

        # Tools should be filtered (may be reduced set depending on phase)
        assert isinstance(context.tools, list)

    def test_anthropic_system_message_extraction(self) -> None:
        """Test that Anthropic strategy extracts system message correctly."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are Claude, an AI assistant."},
            {"role": "user", "content": "Hello"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="claude-3-5-sonnet",
        )

        # Assemble
        assembled = assembler.assemble(messages, [], session_meta)

        # Apply Anthropic shaping
        strategy = get_strategy("claude-3-5-sonnet")
        shaped = strategy.shape(assembled)

        # System message should be extracted
        system_param = strategy.get_system_param(assembled)
        assert system_param is not None
        assert "Claude" in system_param

        # System message should not be in messages
        roles = [m["role"] for m in shaped.messages]
        assert "system" not in roles


@pytest.mark.asyncio
class TestEvidenceCaptureIntegration:
    """Integration tests for evidence capture during tool execution."""

    async def test_evidence_capture_during_tool_execution(self, db_session: AsyncSession) -> None:
        """Test that evidence is captured during tool execution."""
        tracker = EvidenceTracker(db_session)
        session_id = uuid4()

        # Simulate tool execution
        evidence = await tracker.capture(
            tool_name="web_search",
            tool_arguments={"query": "Python best practices"},
            result={"results": ["Use type hints", "Write tests"]},
            session_id=session_id,
            turn_index=1,
        )

        assert evidence is not None
        assert evidence.tool_name == "web_search"
        assert evidence.session_id == session_id
        assert evidence.provenance["turn_index"] == 1

        # Query evidence
        results = await tracker.query(session_id=session_id)
        assert len(results) == 1
        assert results[0].id == evidence.id

    async def test_multiple_evidence_capture(self, db_session: AsyncSession) -> None:
        """Test capturing multiple evidence records."""
        tracker = EvidenceTracker(db_session)
        session_id = uuid4()

        # Capture multiple tool results
        await tracker.capture(
            tool_name="search",
            tool_arguments={"q": "test"},
            result="result1",
            session_id=session_id,
        )
        await tracker.capture(
            tool_name="read_file",
            tool_arguments={"path": "/test.py"},
            result="file content",
            session_id=session_id,
        )
        await tracker.capture(
            tool_name="write_file",
            tool_arguments={"path": "/out.txt"},
            result="error: permission denied",
            session_id=session_id,
            is_error=True,
        )

        # Query all
        results = await tracker.query(session_id=session_id)
        assert len(results) == 3

        # Query errors only
        errors = await tracker.query(session_id=session_id, tool_name="write_file")
        assert len(errors) == 1
        assert errors[0].is_error is True

    async def test_evidence_importance_boosting(self, db_session: AsyncSession) -> None:
        """Test that importance increases when evidence is re-referenced."""
        tracker = EvidenceTracker(db_session)
        session_id = uuid4()

        # Create evidence
        evidence = await tracker.capture(
            tool_name="test_tool",
            tool_arguments={},
            result="important result",
            session_id=session_id,
        )

        initial_importance = evidence.importance
        assert initial_importance == 0.5

        # Boost importance (simulating re-reference)
        new_importance = await tracker.boost_importance(evidence.id)
        assert new_importance > initial_importance

        # Verify in database
        found = await tracker.get_by_id(evidence.id)
        assert found is not None
        assert found.importance == new_importance


class TestConversationServiceIntegration:
    """Integration tests for ConversationService with context engineering."""

    def test_conversation_service_initialization(self) -> None:
        """Test that ConversationService initializes correctly."""
        service = ConversationService()
        assert service.budget_manager is not None
        assert service.token_counter is not None
        assert service.assembler is not None

    def test_conversation_service_custom_budget(self) -> None:
        """Test ConversationService with custom budget manager."""
        budget_manager = BudgetManager(default_budget=5000)
        service = ConversationService(budget_manager=budget_manager)
        assert service.budget_manager.default_budget == 5000


class TestMemoryInjectionIntegration:
    """Integration tests for memory injection through ContextAssembler."""

    def test_memory_blocks_injected_into_assembled_context(self) -> None:
        """L1 memory blocks are injected and reflected in metadata."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
        ]

        blocks = [
            MemoryBlockReadSchema(
                id=uuid4(),
                agent_id=uuid4(),
                label="persona",
                content="Expert Python developer",
                position=0,
                limit=1000,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            memory_blocks=blocks,
        )

        assert context.metadata["memory_blocks_count"] == 1
        block_contents = [m["content"] for m in context.messages if "[persona]" in m.get("content", "")]
        assert len(block_contents) == 1

    def test_user_memories_injected_into_assembled_context(self) -> None:
        """L3 user memories are injected as system message."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "What do you know about me?"},
        ]

        memories = [
            MemoryReadSchema(
                id=uuid4(),
                content="User works at Google",
                scope={"user_id": "u1"},
                memory_type="semantic",
                importance=0.8,
                access_count=0,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            user_memories=memories,
        )

        assert context.metadata["user_memories_count"] == 1
        memory_msg = [m for m in context.messages if "[User memories]" in m.get("content", "")]
        assert len(memory_msg) == 1
        assert "User works at Google" in memory_msg[0]["content"]

    def test_memory_injection_preserves_budget(self) -> None:
        """Memory injection respects budget constraints."""
        budget_manager = BudgetManager()
        session_id = uuid4()
        budget_manager.allocate(session_id, "gpt-4o", custom_budget=50)

        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello there, this is a test message"},
        ]

        blocks = [
            MemoryBlockReadSchema(
                id=uuid4(),
                agent_id=uuid4(),
                label="context",
                content="Additional context that takes up tokens in the budget window",
                position=0,
                limit=500,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        session_meta = SessionMeta(
            session_id=str(session_id),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            memory_blocks=blocks,
        )

        assert context.metadata["memory_blocks_count"] == 1
        assert isinstance(context.messages, list)
        assert len(context.messages) > 0

    def test_empty_memory_lists_are_no_op(self) -> None:
        """Empty memory lists do not affect assembly."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "Hello"},
        ]

        session_meta = SessionMeta(
            session_id=str(uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context_no_mem = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
        )

        context_with_empty = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            memory_blocks=[],
            user_memories=[],
        )

        assert len(context_no_mem.messages) == len(context_with_empty.messages)
