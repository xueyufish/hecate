"""Integration tests for ConversationService memory injection.

Tests L1 block loading, L2 compression trigger, L3 retrieval/extraction,
and memory tool registration within ConversationService.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.memory import MemoryBlockModel, MemoryModel
from hecate.services.conversation import ConversationService

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _mock_llm_response(content: str = "Hi there!") -> MagicMock:
    """Create a mock LLM response object."""
    resp = MagicMock()
    resp.content = content
    resp.tool_calls = None
    resp.model = "gpt-4o"
    resp.usage = {}
    resp.finish_reason = "stop"
    return resp


def _patch_llm():
    """Patch llm_service with AsyncMock.chat returning mock response."""
    mock_llm = MagicMock()
    mock_llm.chat = AsyncMock()
    return patch("hecate.services.conversation.llm_service", mock_llm)


@pytest.fixture
def agent_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def user_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
async def agent_with_blocks(db_session: AsyncSession, agent_id: uuid.UUID) -> uuid.UUID:
    """Create an agent with L1 memory blocks."""
    agent = AgentModel(
        id=agent_id,
        name="Test Agent",
        mode="chat",
    )
    agent.model_config = {"model": "gpt-4o"}
    db_session.add(agent)

    block = MemoryBlockModel(
        workspace_id=_DEFAULT_WORKSPACE,
        agent_id=agent_id,
        label="persona",
        content="You are a helpful coding assistant",
        position=0,
        limit=1000,
    )
    db_session.add(block)
    await db_session.flush()
    return agent_id


@pytest.fixture
async def user_with_memories(db_session: AsyncSession, user_id: str) -> str:
    """Create a user with L3 memories."""
    memory = MemoryModel(
        workspace_id=_DEFAULT_WORKSPACE,
        content="User prefers Python over JavaScript",
        scope={"user_id": user_id},
        memory_type="semantic",
        importance=0.8,
        embedding=[0.0] * 1024,
    )
    db_session.add(memory)
    await db_session.flush()
    return user_id


class TestL1MemoryInjection:
    """Test L1 working memory block loading in ConversationService."""

    async def test_l1_blocks_loaded_when_agent_id_provided(
        self, db_session: AsyncSession, agent_with_blocks: uuid.UUID
    ) -> None:
        """L1 blocks are loaded from DB and passed to assembler."""
        service = ConversationService()
        messages = [{"role": "user", "content": "Hello"}]

        with _patch_llm() as mock_llm:
            mock_llm.chat.return_value = _mock_llm_response("Hi there!")

            result = await service.chat(
                messages=messages,
                model="gpt-4o",
                db=db_session,
                agent_id=str(agent_with_blocks),
            )

        assert result["content"] == "Hi there!"

    async def test_l1_blocks_not_loaded_without_db(self, agent_id: uuid.UUID) -> None:
        """No L1 loading when db is None."""
        service = ConversationService()

        with _patch_llm() as mock_llm:
            mock_llm.chat.return_value = _mock_llm_response("Hi!")

            result = await service.chat(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4o",
                agent_id=str(agent_id),
            )

        assert result["content"] == "Hi!"


class TestL2CompressionTrigger:
    """Test L2 compression trigger in ConversationService."""

    def test_compress_method_chains_levels(self) -> None:
        """CompressionPipeline.compress() chains snip+microcompact+autocompact."""
        from hecate.services.memory.compression import CompressionPipeline

        pipeline = CompressionPipeline()
        messages = [
            {"role": "system", "content": "You are helpful."},
        ]
        for i in range(20):
            messages.append({"role": "user", "content": f"Question {i} " * 50})
            messages.append({"role": "assistant", "content": f"Answer {i} " * 50})

        result = pipeline.compress(messages, token_threshold=100, recent_window=4)

        assert result.level_applied != "none"
        assert result.compressed_count < len(messages)
        assert result.tokens_saved > 0

    def test_compress_returns_none_when_under_threshold(self) -> None:
        """compress() returns 'none' when messages are under threshold."""
        from hecate.services.memory.compression import CompressionPipeline

        pipeline = CompressionPipeline()
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]

        result = pipeline.compress(messages, token_threshold=99999)

        assert result.level_applied == "none"
        assert result.messages == messages


class TestL3MemoryRetrieval:
    """Test L3 user memory retrieval in ConversationService."""

    async def test_l3_memories_retrieved_when_user_id_provided(
        self, db_session: AsyncSession, user_with_memories: str
    ) -> None:
        """L3 memories are retrieved from DB and passed to assembler."""
        service = ConversationService()
        messages = [{"role": "user", "content": "What language do I prefer?"}]

        with _patch_llm() as mock_llm:
            mock_llm.chat.return_value = _mock_llm_response("You prefer Python!")

            result = await service.chat(
                messages=messages,
                model="gpt-4o",
                db=db_session,
                user_id=user_with_memories,
            )

        assert result["content"] == "You prefer Python!"

    async def test_l3_not_loaded_without_user_id(self) -> None:
        """No L3 loading when user_id is None."""
        service = ConversationService()

        with _patch_llm() as mock_llm:
            mock_llm.chat.return_value = _mock_llm_response("Hi!")

            result = await service.chat(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4o",
            )

        assert result["content"] == "Hi!"


class TestL3FactExtraction:
    """Test L3 fact extraction after assistant response."""

    async def test_extract_facts_called_after_response(self, db_session: AsyncSession, user_id: str) -> None:
        """extract_facts is called after assistant response."""
        service = ConversationService()
        messages = [{"role": "user", "content": "I prefer Python"}]

        with (
            _patch_llm() as mock_llm,
            patch("hecate.services.conversation.UserMemoryService") as mock_um_cls,
        ):
            mock_llm.chat.return_value = _mock_llm_response("Noted!")

            mock_um = AsyncMock()
            mock_um.extract_facts.return_value = ["I prefer Python"]
            mock_um.store_memory = AsyncMock()
            mock_um.retrieve_memories.return_value = []
            mock_um_cls.return_value = mock_um

            result = await service.chat(
                messages=messages,
                model="gpt-4o",
                db=db_session,
                user_id=user_id,
            )

        assert result["content"] == "Noted!"
        mock_um.extract_facts.assert_called_once()

    async def test_extract_facts_not_called_without_db(self) -> None:
        """No fact extraction when db is None."""
        service = ConversationService()

        with _patch_llm() as mock_llm:
            mock_llm.chat.return_value = _mock_llm_response("Hi!")

            result = await service.chat(
                messages=[{"role": "user", "content": "Hello"}],
                model="gpt-4o",
            )

        assert result["content"] == "Hi!"


class TestMemoryToolRegistration:
    """Test memory tool registration in ConversationService."""

    def test_build_memory_tools_with_agent_id(self) -> None:
        """update_memory_block tool registered when agent_id provided."""
        service = ConversationService()
        tools = service._build_memory_tools(db=MagicMock(), agent_id="test-agent", user_id=None)
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "update_memory_block"

    def test_build_memory_tools_with_user_id(self) -> None:
        """search_user_memory tool registered when user_id provided."""
        service = ConversationService()
        tools = service._build_memory_tools(db=MagicMock(), agent_id=None, user_id="user-1")
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "search_user_memory"

    def test_build_memory_tools_with_both(self) -> None:
        """Both tools registered when both agent_id and user_id provided."""
        service = ConversationService()
        tools = service._build_memory_tools(db=MagicMock(), agent_id="agent-1", user_id="user-1")
        assert len(tools) == 2
        names = {t["function"]["name"] for t in tools}
        assert names == {"update_memory_block", "search_user_memory"}

    def test_build_memory_tools_without_db(self) -> None:
        """No tools registered when db is None."""
        service = ConversationService()
        tools = service._build_memory_tools(db=None, agent_id="a", user_id="u")
        assert tools == []
