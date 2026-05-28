"""Integration test for context assembly with memory injection."""

from __future__ import annotations

import uuid

from hecate.models.memory import MemoryBlockReadSchema, MemoryReadSchema
from hecate.services.context.assembler import ContextAssembler
from hecate.services.context.budget import BudgetManager
from hecate.services.context.types import SessionMeta


class TestContextAssemblerMemory:
    """Tests for ContextAssembler with memory injection."""

    def test_inject_memory_blocks(self) -> None:
        """Test injecting memory blocks into context."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]

        blocks = [
            MemoryBlockReadSchema(
                id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                label="persona",
                content="You are a coding expert",
                position=0,
                limit=1000,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        session_meta = SessionMeta(
            session_id=str(uuid.uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            memory_blocks=blocks,
        )

        # Should have system messages + injected block + user message
        assert len(context.messages) == 3
        assert "[persona]" in context.messages[1]["content"]
        assert context.metadata["memory_blocks_count"] == 1

    def test_inject_user_memories(self) -> None:
        """Test injecting user memories into context."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "user", "content": "What should I learn?"},
        ]

        memories = [
            MemoryReadSchema(
                id=uuid.uuid4(),
                content="User prefers Python",
                scope={"user_id": "user1"},
                memory_type="semantic",
                importance=0.8,
                access_count=0,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        session_meta = SessionMeta(
            session_id=str(uuid.uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            user_memories=memories,
        )

        # Should have injected memory + user message
        assert len(context.messages) == 2
        assert "[User memories]" in context.messages[0]["content"]
        assert context.metadata["user_memories_count"] == 1

    def test_inject_both_memory_types(self) -> None:
        """Test injecting both memory blocks and user memories."""
        budget_manager = BudgetManager()
        assembler = ContextAssembler(budget_manager)

        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
        ]

        blocks = [
            MemoryBlockReadSchema(
                id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                label="persona",
                content="Expert coder",
                position=0,
                limit=1000,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        memories = [
            MemoryReadSchema(
                id=uuid.uuid4(),
                content="User likes Python",
                scope={},
                memory_type="semantic",
                importance=0.7,
                access_count=0,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-01-01T00:00:00Z",
                deleted_at=None,
            ),
        ]

        session_meta = SessionMeta(
            session_id=str(uuid.uuid4()),
            agent_id="test-agent",
            model="gpt-4o",
        )

        context = assembler.assemble(
            messages=messages,
            tools=[],
            session_meta=session_meta,
            memory_blocks=blocks,
            user_memories=memories,
        )

        # Should have system + block + memory + user
        assert len(context.messages) == 4
        assert context.metadata["memory_blocks_count"] == 1
        assert context.metadata["user_memories_count"] == 1
