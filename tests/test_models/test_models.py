from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import (
    AgentCreateSchema,
    AgentModel,
    AgentReadSchema,
)
from hecate.models.checkpoint import (
    CheckpointCreateSchema,
    CheckpointModel,
    CheckpointReadSchema,
)
from hecate.models.conversation import (
    ConversationModel,
)
from hecate.models.document import (
    DocumentModel,
)
from hecate.models.knowledge import (
    KnowledgeBaseCreateSchema,
    KnowledgeBaseModel,
)
from hecate.models.message import (
    MessageModel,
    MessageReadSchema,
)
from hecate.models.session import (
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
)


class TestAgentModel:
    @pytest.mark.asyncio
    async def test_create_agent(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="test-agent", model_config_db={"model": "gpt-4o", "temperature": 0.7})
        db_session.add(agent)
        await db_session.flush()
        assert agent.id is not None
        assert agent.name == "test-agent"
        assert agent.mode == "chat"
        assert agent.workspace_id == uuid.UUID("00000000-0000-0000-0000-000000000000")

    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="delete-me", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        agent.deleted_at = datetime.now()
        await db_session.flush()
        assert agent.deleted_at is not None

    @pytest.mark.asyncio
    async def test_jsonb_fields(self, db_session: AsyncSession) -> None:
        agent = AgentModel(
            name="jsonb-test",
            model_config_db={"model": "gpt-4o"},
            tools=[{"name": "search"}],
            knowledge_base_ids=[str(uuid.uuid4())],
        )
        db_session.add(agent)
        await db_session.flush()
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
    async def test_create_conversation(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="conv-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        conv = ConversationModel(agent_id=agent.id, title="Test Conversation")
        db_session.add(conv)
        await db_session.flush()
        assert conv.id is not None
        assert conv.agent_id == agent.id

    @pytest.mark.asyncio
    async def test_conversation_auto_create_with_session(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="auto-conv", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        session = SessionModel(agent_id=agent.id, conversation_id=None)
        db_session.add(session)
        await db_session.flush()
        assert session.conversation_id is None
        conv = ConversationModel(agent_id=agent.id)
        db_session.add(conv)
        await db_session.flush()
        session.conversation_id = conv.id
        await db_session.flush()
        assert session.conversation_id == conv.id


class TestSessionModel:
    @pytest.mark.asyncio
    async def test_create_session(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="session-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        session = SessionModel(agent_id=agent.id)
        db_session.add(session)
        await db_session.flush()
        assert session.status == "active"
        assert session.agent_id == agent.id

    @pytest.mark.asyncio
    async def test_session_status_transition(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="status-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        session = SessionModel(agent_id=agent.id)
        db_session.add(session)
        await db_session.flush()
        session.status = "interrupted"
        session.current_node = "plan"
        await db_session.flush()
        assert session.status == "interrupted"
        assert session.current_node == "plan"


class TestMessageModel:
    @pytest.mark.asyncio
    async def test_create_message(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="msg-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        conv = ConversationModel(agent_id=agent.id)
        db_session.add(conv)
        await db_session.flush()
        msg = MessageModel(
            conversation_id=conv.id,
            role="user",
            content="Hello",
        )
        db_session.add(msg)
        await db_session.flush()
        assert msg.id is not None
        assert msg.role == "user"

    @pytest.mark.asyncio
    async def test_message_with_tool_calls(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="tc-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        conv = ConversationModel(agent_id=agent.id)
        db_session.add(conv)
        await db_session.flush()
        msg = MessageModel(
            conversation_id=conv.id,
            role="assistant",
            content="",
            tool_calls=[{"id": "call_1", "function": {"name": "search", "arguments": "{}"}}],
        )
        db_session.add(msg)
        await db_session.flush()
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1

    @pytest.mark.asyncio
    async def test_tool_result_message(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="tr-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        conv = ConversationModel(agent_id=agent.id)
        db_session.add(conv)
        await db_session.flush()
        msg = MessageModel(
            conversation_id=conv.id,
            role="tool",
            content="result data",
            tool_call_id="call_1",
        )
        db_session.add(msg)
        await db_session.flush()
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_1"


class TestToolModel:
    @pytest.mark.asyncio
    async def test_create_builtin_tool(self, db_session: AsyncSession) -> None:
        tool = ToolModel(
            name="search",
            description="Web search",
            source="builtin",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        db_session.add(tool)
        await db_session.flush()
        assert tool.source == "builtin"
        assert tool.approval_required is False

    @pytest.mark.asyncio
    async def test_create_mcp_tool(self, db_session: AsyncSession) -> None:
        tool = ToolModel(
            name="mcp_tool",
            description="MCP tool",
            source="mcp",
            parameters={},
            mcp_server="http://mcp-server:8080",
            mcp_tool_name="web_search",
        )
        db_session.add(tool)
        await db_session.flush()
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
    async def test_create_knowledge_base(self, db_session: AsyncSession) -> None:
        kb = KnowledgeBaseModel(
            name="test-kb",
            qdrant_collection="kb_test",
        )
        db_session.add(kb)
        await db_session.flush()
        assert kb.embedding_model == "BAAI/bge-m3"
        assert kb.chunk_size == 512
        assert kb.chunk_overlap == 100

    def test_kb_create_schema_defaults(self) -> None:
        schema = KnowledgeBaseCreateSchema(name="test")
        assert schema.embedding_model == "BAAI/bge-m3"
        assert schema.chunk_strategy == "fixed"


class TestDocumentModel:
    @pytest.mark.asyncio
    async def test_create_document(self, db_session: AsyncSession) -> None:
        kb = KnowledgeBaseModel(name="doc-kb", qdrant_collection="kb_doc")
        db_session.add(kb)
        await db_session.flush()
        doc = DocumentModel(
            knowledge_base_id=kb.id,
            filename="test.pdf",
            file_path="kb/test.pdf",
            file_size=1024,
            content_type="application/pdf",
        )
        db_session.add(doc)
        await db_session.flush()
        assert doc.parsing_status == "pending"
        assert doc.chunk_count == 0

    @pytest.mark.asyncio
    async def test_document_status_transition(self, db_session: AsyncSession) -> None:
        kb = KnowledgeBaseModel(name="status-kb", qdrant_collection="kb_status")
        db_session.add(kb)
        await db_session.flush()
        doc = DocumentModel(
            knowledge_base_id=kb.id,
            filename="test.pdf",
            file_path="kb/test.pdf",
        )
        db_session.add(doc)
        await db_session.flush()
        doc.parsing_status = "parsing"
        await db_session.flush()
        doc.parsing_status = "completed"
        doc.chunk_count = 42
        await db_session.flush()
        assert doc.parsing_status == "completed"
        assert doc.chunk_count == 42

    @pytest.mark.asyncio
    async def test_document_parsing_failed(self, db_session: AsyncSession) -> None:
        kb = KnowledgeBaseModel(name="fail-kb", qdrant_collection="kb_fail")
        db_session.add(kb)
        await db_session.flush()
        doc = DocumentModel(
            knowledge_base_id=kb.id,
            filename="bad.pdf",
            file_path="kb/bad.pdf",
        )
        db_session.add(doc)
        await db_session.flush()
        doc.parsing_status = "failed"
        doc.parsing_error = "Invalid PDF format"
        await db_session.flush()
        assert doc.parsing_status == "failed"
        assert doc.parsing_error == "Invalid PDF format"


class TestSkillModel:
    @pytest.mark.asyncio
    async def test_create_skill(self, db_session: AsyncSession) -> None:
        skill = SkillModel(
            name="developer",
            description="Development skill",
            source="project",
            instructions="Follow coding standards",
        )
        db_session.add(skill)
        await db_session.flush()
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
    async def test_create_checkpoint(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="cp-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        session = SessionModel(agent_id=agent.id)
        db_session.add(session)
        await db_session.flush()
        cp = CheckpointModel(
            session_id=session.id,
            superstep=1,
            node_id="guard",
            channel_state={"messages": ["hello"]},
        )
        db_session.add(cp)
        await db_session.flush()
        assert cp.superstep == 1
        assert cp.channel_state == {"messages": ["hello"]}

    @pytest.mark.asyncio
    async def test_checkpoint_sequential_supersteps(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="seq-agent", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        session = SessionModel(agent_id=agent.id)
        db_session.add(session)
        await db_session.flush()
        for i in range(1, 4):
            cp = CheckpointModel(
                session_id=session.id,
                superstep=i,
                node_id=f"node_{i}",
                channel_state={"step": i},
            )
            db_session.add(cp)
        await db_session.flush()
        assert len([c for c in db_session.new]) == 0

    def test_checkpoint_create_schema_defaults(self) -> None:
        schema = CheckpointCreateSchema(
            session_id=uuid.uuid4(),
            superstep=1,
        )
        assert schema.channel_state == {}
        assert schema.pending_writes == []
        assert schema.metadata == {}


class TestReadSchemaFromAttributes:
    """Verify that ReadSchema.model_validate(orm_instance) works for all models with metadata_ columns."""

    def _make_base_attrs(self) -> dict:
        now = datetime.now()
        return {"id": uuid.uuid4(), "created_at": now, "updated_at": now}

    def test_session_read_schema(self) -> None:
        from hecate.models.session import SessionModel

        attrs = self._make_base_attrs()
        session = SessionModel(
            agent_id=uuid.uuid4(),
            status="active",
            metadata_={"key": "value"},
            **attrs,
        )
        schema = SessionReadSchema.model_validate(session)
        assert schema.metadata == {"key": "value"}
        assert schema.status == "active"

    def test_message_read_schema(self) -> None:
        from hecate.models.message import MessageModel

        attrs = self._make_base_attrs()
        msg = MessageModel(
            conversation_id=uuid.uuid4(),
            role="user",
            content="hello",
            tool_calls=[{"id": "1", "name": "test"}],
            metadata_={"tokens": 5},
            **attrs,
        )
        schema = MessageReadSchema.model_validate(msg)
        assert schema.metadata == {"tokens": 5}
        assert schema.tool_calls == [{"id": "1", "name": "test"}]

    def test_skill_read_schema(self) -> None:
        from hecate.models.skill import SkillModel

        attrs = self._make_base_attrs()
        skill = SkillModel(
            name="developer",
            description="A developer skill",
            source="system",
            instructions="Write code",
            allowed_tools=["tool1"],
            metadata_={"version": "1.0"},
            scripts=[],
            references=[],
            max_tokens=2000,
            auto_load=False,
            **attrs,
        )
        schema = SkillReadSchema.model_validate(skill)
        assert schema.metadata == {"version": "1.0"}
        assert schema.name == "developer"

    def test_checkpoint_read_schema(self) -> None:
        from hecate.models.checkpoint import CheckpointModel

        cp_id = uuid.uuid4()
        cp = CheckpointModel(
            session_id=uuid.uuid4(),
            superstep=1,
            node_id="test_node",
            channel_state={"messages": ["hello"]},
            pending_writes=[],
            metadata_={"interrupted": False},
        )
        cp.id = cp_id
        cp.created_at = datetime.now()
        schema = CheckpointReadSchema.model_validate(cp)
        assert schema.metadata == {"interrupted": False}
        assert schema.superstep == 1
