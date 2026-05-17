from __future__ import annotations

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from hecate.core.database import Base
from hecate.models.agent import (
    AgentCreateSchema,
    AgentModel,
    AgentReadSchema,
    AgentUpdateSchema,
)
from hecate.models.checkpoint import (
    CheckpointCreateSchema,
    CheckpointModel,
    CheckpointReadSchema,
)
from hecate.models.conversation import (
    ConversationCreateSchema,
    ConversationModel,
    ConversationReadSchema,
)
from hecate.models.document import (
    DocumentCreateSchema,
    DocumentModel,
    DocumentReadSchema,
)
from hecate.models.knowledge import (
    KnowledgeBaseCreateSchema,
    KnowledgeBaseModel,
    KnowledgeBaseReadSchema,
)
from hecate.models.message import (
    MessageCreateSchema,
    MessageModel,
    MessageReadSchema,
)
from hecate.models.session import (
    SessionCreateSchema,
    SessionModel,
    SessionReadSchema,
)
from hecate.models.skill import (
    SkillCreateSchema,
    SkillModel,
    SkillReadSchema,
)
from hecate.models.tool import (
    ToolCreateSchema,
    ToolModel,
    ToolReadSchema,
)

TEST_DB_URL = "sqlite+aiosqlite://"
test_engine = create_async_engine(TEST_DB_URL, echo=False)
test_session_factory = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with test_session_factory() as session:
        yield session


class TestAgentModel:
    @pytest.mark.asyncio
    async def test_create_agent(self, db: AsyncSession) -> None:
        agent = AgentModel(name="test-agent", model_config_db={"model": "gpt-4o", "temperature": 0.7})
        db.add(agent)
        await db.flush()
        assert agent.id is not None
        assert agent.name == "test-agent"
        assert agent.mode == "chat"
        assert agent.workspace_id == uuid.UUID("00000000-0000-0000-0000-000000000000")

    @pytest.mark.asyncio
    async def test_soft_delete(self, db: AsyncSession) -> None:
        agent = AgentModel(name="delete-me", model_config_db={})
        db.add(agent)
        await db.flush()
        agent.deleted_at = datetime.now()
        await db.flush()
        assert agent.deleted_at is not None

    @pytest.mark.asyncio
    async def test_jsonb_fields(self, db: AsyncSession) -> None:
        agent = AgentModel(
            name="jsonb-test",
            model_config_db={"model": "gpt-4o"},
            tools=[{"name": "search"}],
            knowledge_base_ids=[str(uuid.uuid4())],
        )
        db.add(agent)
        await db.flush()
        assert agent.tools == [{"name": "search"}]
        assert len(agent.knowledge_base_ids) == 1


class TestAgentSchema:
    def test_create_schema_valid(self) -> None:
        schema = AgentCreateSchema(name="test", model_config={"model": "gpt-4o"})
        assert schema.name == "test"
        assert schema.mode == "chat"

    def test_create_schema_missing_model_config(self) -> None:
        with pytest.raises(ValidationError):
            AgentCreateSchema(name="test")

    def test_create_schema_invalid_mode(self) -> None:
        with pytest.raises(ValidationError):
            AgentCreateSchema(name="test", model_config={}, mode="invalid")

    def test_read_schema_from_attributes(self) -> None:
        now = datetime.now()
        agent = AgentModel(
            name="read-test",
            model_config_db={"model": "gpt-4"},
            created_at=now,
            updated_at=now,
        )
        agent.id = uuid.uuid4()
        agent.workspace_id = uuid.uuid4()
        agent.mode = "chat"
        agent.tools = []
        agent.skills = []
        agent.knowledge_base_ids = []
        agent.risk_level = "LOW"
        schema = AgentReadSchema.model_validate(agent)
        assert schema.name == "read-test"
        assert schema.model_config_db == {"model": "gpt-4"}


class TestConversationModel:
    @pytest.mark.asyncio
    async def test_create_conversation(self, db: AsyncSession) -> None:
        agent = AgentModel(name="conv-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        conv = ConversationModel(agent_id=agent.id, title="Test Conversation")
        db.add(conv)
        await db.flush()
        assert conv.id is not None
        assert conv.agent_id == agent.id

    @pytest.mark.asyncio
    async def test_conversation_auto_create_with_session(self, db: AsyncSession) -> None:
        agent = AgentModel(name="auto-conv", model_config_db={})
        db.add(agent)
        await db.flush()
        session = SessionModel(agent_id=agent.id, conversation_id=None)
        db.add(session)
        await db.flush()
        assert session.conversation_id is None
        conv = ConversationModel(agent_id=agent.id)
        db.add(conv)
        await db.flush()
        session.conversation_id = conv.id
        await db.flush()
        assert session.conversation_id == conv.id


class TestSessionModel:
    @pytest.mark.asyncio
    async def test_create_session(self, db: AsyncSession) -> None:
        agent = AgentModel(name="session-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        session = SessionModel(agent_id=agent.id)
        db.add(session)
        await db.flush()
        assert session.status == "active"
        assert session.agent_id == agent.id

    @pytest.mark.asyncio
    async def test_session_status_transition(self, db: AsyncSession) -> None:
        agent = AgentModel(name="status-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        session = SessionModel(agent_id=agent.id)
        db.add(session)
        await db.flush()
        session.status = "interrupted"
        session.current_node = "plan"
        await db.flush()
        assert session.status == "interrupted"
        assert session.current_node == "plan"


class TestMessageModel:
    @pytest.mark.asyncio
    async def test_create_message(self, db: AsyncSession) -> None:
        agent = AgentModel(name="msg-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        conv = ConversationModel(agent_id=agent.id)
        db.add(conv)
        await db.flush()
        msg = MessageModel(
            conversation_id=conv.id,
            role="user",
            content="Hello",
        )
        db.add(msg)
        await db.flush()
        assert msg.id is not None
        assert msg.role == "user"

    @pytest.mark.asyncio
    async def test_message_with_tool_calls(self, db: AsyncSession) -> None:
        agent = AgentModel(name="tc-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        conv = ConversationModel(agent_id=agent.id)
        db.add(conv)
        await db.flush()
        msg = MessageModel(
            conversation_id=conv.id,
            role="assistant",
            content="",
            tool_calls=[{"id": "call_1", "function": {"name": "search", "arguments": "{}"}}],
        )
        db.add(msg)
        await db.flush()
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_tool_result_message(self, db: AsyncSession) -> None:
        agent = AgentModel(name="tr-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        conv = ConversationModel(agent_id=agent.id)
        db.add(conv)
        await db.flush()
        msg = MessageModel(
            conversation_id=conv.id,
            role="tool",
            content="result data",
            tool_call_id="call_1",
        )
        db.add(msg)
        await db.flush()
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_1"


class TestToolModel:
    @pytest.mark.asyncio
    async def test_create_builtin_tool(self, db: AsyncSession) -> None:
        tool = ToolModel(
            name="search",
            description="Web search",
            source="builtin",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        db.add(tool)
        await db.flush()
        assert tool.source == "builtin"
        assert tool.approval_required is False

    @pytest.mark.asyncio
    async def test_create_mcp_tool(self, db: AsyncSession) -> None:
        tool = ToolModel(
            name="mcp_tool",
            description="MCP tool",
            source="mcp",
            parameters={},
            mcp_server="http://mcp-server:8080",
            mcp_tool_name="web_search",
        )
        db.add(tool)
        await db.flush()
        assert tool.mcp_server == "http://mcp-server:8080"

    def test_tool_create_schema(self) -> None:
        schema = ToolCreateSchema(
            name="test",
            description="desc",
            source="builtin",
            parameters={"type": "object"},
        )
        assert schema.source == "builtin"

    def test_tool_invalid_source(self) -> None:
        with pytest.raises(ValidationError):
            ToolCreateSchema(
                name="test",
                description="desc",
                source="unknown",
                parameters={},
            )


class TestKnowledgeBaseModel:
    @pytest.mark.asyncio
    async def test_create_knowledge_base(self, db: AsyncSession) -> None:
        kb = KnowledgeBaseModel(
            name="test-kb",
            qdrant_collection="kb_test",
        )
        db.add(kb)
        await db.flush()
        assert kb.embedding_model == "BAAI/bge-m3"
        assert kb.chunk_size == 512
        assert kb.chunk_overlap == 100

    def test_kb_create_schema_defaults(self) -> None:
        schema = KnowledgeBaseCreateSchema(name="test")
        assert schema.embedding_model == "BAAI/bge-m3"
        assert schema.chunk_strategy == "fixed"


class TestDocumentModel:
    @pytest.mark.asyncio
    async def test_create_document(self, db: AsyncSession) -> None:
        kb = KnowledgeBaseModel(name="doc-kb", qdrant_collection="kb_doc")
        db.add(kb)
        await db.flush()
        doc = DocumentModel(
            knowledge_base_id=kb.id,
            filename="test.pdf",
            file_path="kb/test.pdf",
            file_size=1024,
            content_type="application/pdf",
        )
        db.add(doc)
        await db.flush()
        assert doc.parsing_status == "pending"
        assert doc.chunk_count == 0

    @pytest.mark.asyncio
    async def test_document_status_transition(self, db: AsyncSession) -> None:
        kb = KnowledgeBaseModel(name="status-kb", qdrant_collection="kb_status")
        db.add(kb)
        await db.flush()
        doc = DocumentModel(
            knowledge_base_id=kb.id,
            filename="test.pdf",
            file_path="kb/test.pdf",
        )
        db.add(doc)
        await db.flush()
        doc.parsing_status = "parsing"
        await db.flush()
        doc.parsing_status = "completed"
        doc.chunk_count = 42
        await db.flush()
        assert doc.parsing_status == "completed"
        assert doc.chunk_count == 42

    @pytest.mark.asyncio
    async def test_document_parsing_failed(self, db: AsyncSession) -> None:
        kb = KnowledgeBaseModel(name="fail-kb", qdrant_collection="kb_fail")
        db.add(kb)
        await db.flush()
        doc = DocumentModel(
            knowledge_base_id=kb.id,
            filename="bad.pdf",
            file_path="kb/bad.pdf",
        )
        db.add(doc)
        await db.flush()
        doc.parsing_status = "failed"
        doc.parsing_error = "Invalid PDF format"
        await db.flush()
        assert doc.parsing_status == "failed"
        assert doc.parsing_error == "Invalid PDF format"


class TestSkillModel:
    @pytest.mark.asyncio
    async def test_create_skill(self, db: AsyncSession) -> None:
        skill = SkillModel(
            name="developer",
            description="Development skill",
            source="project",
            instructions="Follow coding standards",
        )
        db.add(skill)
        await db.flush()
        assert skill.max_tokens == 2000
        assert skill.auto_load is False

    def test_skill_name_pattern(self) -> None:
        schema = SkillCreateSchema(
            name="web-search",
            description="desc",
            source="system",
            instructions="test",
        )
        assert schema.name == "web-search"

    def test_skill_invalid_name(self) -> None:
        with pytest.raises(ValidationError):
            SkillCreateSchema(
                name="Invalid Name!",
                description="desc",
                source="system",
                instructions="test",
            )


class TestCheckpointModel:
    @pytest.mark.asyncio
    async def test_create_checkpoint(self, db: AsyncSession) -> None:
        agent = AgentModel(name="cp-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        session = SessionModel(agent_id=agent.id)
        db.add(session)
        await db.flush()
        cp = CheckpointModel(
            session_id=session.id,
            superstep=1,
            node_id="guard",
            channel_state={"messages": ["hello"]},
        )
        db.add(cp)
        await db.flush()
        assert cp.superstep == 1
        assert cp.channel_state == {"messages": ["hello"]}

    @pytest.mark.asyncio
    async def test_checkpoint_sequential_supersteps(self, db: AsyncSession) -> None:
        agent = AgentModel(name="seq-agent", model_config_db={})
        db.add(agent)
        await db.flush()
        session = SessionModel(agent_id=agent.id)
        db.add(session)
        await db.flush()
        for i in range(1, 4):
            cp = CheckpointModel(
                session_id=session.id,
                superstep=i,
                node_id=f"node_{i}",
                channel_state={"step": i},
            )
            db.add(cp)
        await db.flush()
        assert len([c for c in db.new]) == 0

    def test_checkpoint_create_schema_defaults(self) -> None:
        schema = CheckpointCreateSchema(
            session_id=uuid.uuid4(),
            superstep=1,
        )
        assert schema.channel_state == {}
        assert schema.pending_writes == []
        assert schema.metadata == {}
