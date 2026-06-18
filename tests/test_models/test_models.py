"""Tests for ORM models and Pydantic schemas.

Covers two concerns:

1. **ORM models** — verify that every SQLAlchemy model can be persisted,
   flushed, and queried through an async session, including JSONB columns,
   nullable foreign keys, default values, and status transitions.
2. **Pydantic schemas** — verify that create/read schemas validate correctly,
   reject invalid input, and can be built from ORM instances via
   ``model_validate`` (including the ``metadata_`` -> ``metadata`` column alias
   used to avoid colliding with SQLAlchemy's reserved ``metadata`` attribute).
"""

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
    """Verify Agent ORM persistence: creation with defaults, soft delete, and JSONB fields."""

    @pytest.mark.asyncio
    async def test_create_agent(self, db_session: AsyncSession) -> None:
        """A newly created agent gets server-generated defaults for mode and workspace_id."""
        agent = AgentModel(name="test-agent", model_config_db={"model": "gpt-4o", "temperature": 0.7})
        db_session.add(agent)
        await db_session.flush()
        assert agent.id is not None
        assert agent.name == "test-agent"
        assert agent.mode == "chat"
        assert agent.workspace_id == uuid.UUID("00000000-0000-0000-0000-000000000000")

    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session: AsyncSession) -> None:
        """Setting deleted_at marks the agent as soft-deleted without removing the row."""
        agent = AgentModel(name="delete-me", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        agent.deleted_at = datetime.now()
        await db_session.flush()
        assert agent.deleted_at is not None

    @pytest.mark.asyncio
    async def test_jsonb_fields(self, db_session: AsyncSession) -> None:
        """JSONB columns (tools, knowledge_base_ids) round-trip lists and dicts correctly."""
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

    @pytest.mark.asyncio
    async def test_opening_remarks_default(self, db_session: AsyncSession) -> None:
        """An agent without explicit opening_remarks defaults to None and enable_suggestions to True."""
        agent = AgentModel(name="default-remarks", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        assert agent.opening_remarks is None
        assert agent.enable_suggestions is True

    @pytest.mark.asyncio
    async def test_opening_remarks_set(self, db_session: AsyncSession) -> None:
        """An agent can store opening_remarks text and disable suggestions."""
        agent = AgentModel(
            name="with-remarks",
            model_config_db={},
            opening_remarks="Hello! How can I help?",
            enable_suggestions=False,
        )
        db_session.add(agent)
        await db_session.flush()
        assert agent.opening_remarks == "Hello! How can I help?"
        assert agent.enable_suggestions is False


class TestAgentSchema:
    """Validate AgentCreateSchema and AgentReadSchema Pydantic validation rules."""

    def test_create_schema_valid(self) -> None:
        """A minimal valid payload fills in the default mode."""
        schema = AgentCreateSchema(name="test", model_config={"model": "gpt-4o"})
        assert schema.name == "test"
        assert schema.mode == "chat"

    def test_create_schema_missing_model_config(self) -> None:
        """Omitting the required model_config field raises a validation error."""
        with pytest.raises(ValidationError):
            AgentCreateSchema(name="test")

    def test_create_schema_invalid_mode(self) -> None:
        """An unsupported mode value is rejected."""
        with pytest.raises(ValidationError):
            AgentCreateSchema(name="test", model_config={}, mode="invalid")

    def test_read_schema_from_attributes(self) -> None:
        """AgentReadSchema can be built from an ORM instance with all fields populated."""
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
        agent.opening_remarks = None
        agent.enable_suggestions = True
        schema = AgentReadSchema.model_validate(agent)
        assert schema.name == "read-test"
        assert schema.model_config_db == {"model": "gpt-4"}

    def test_create_schema_with_opening_remarks(self) -> None:
        """AgentCreateSchema accepts opening_remarks and enable_suggestions fields."""
        schema = AgentCreateSchema(
            name="test",
            model_config={"model": "gpt-4o"},
            opening_remarks="Hello! How can I help you?",
            enable_suggestions=False,
        )
        assert schema.opening_remarks == "Hello! How can I help you?"
        assert schema.enable_suggestions is False

    def test_create_schema_defaults_for_suggestion_fields(self) -> None:
        """AgentCreateSchema defaults opening_remarks to None and enable_suggestions to True."""
        schema = AgentCreateSchema(name="test", model_config={"model": "gpt-4o"})
        assert schema.opening_remarks is None
        assert schema.enable_suggestions is True

    def test_update_schema_with_opening_remarks(self) -> None:
        """AgentUpdateSchema accepts opening_remarks and enable_suggestions fields."""
        from hecate.models.agent import AgentUpdateSchema

        schema = AgentUpdateSchema(opening_remarks="Updated remarks", enable_suggestions=False)
        assert schema.opening_remarks == "Updated remarks"
        assert schema.enable_suggestions is False

    def test_read_schema_with_opening_remarks(self) -> None:
        """AgentReadSchema exposes opening_remarks and enable_suggestions from ORM instance."""
        now = datetime.now()
        agent = AgentModel(
            name="remarks-test",
            model_config_db={"model": "gpt-4"},
            opening_remarks="Welcome!",
            enable_suggestions=False,
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
        assert schema.opening_remarks == "Welcome!"
        assert schema.enable_suggestions is False

    @pytest.mark.asyncio
    async def test_guardrail_config_default_none(self, db_session: AsyncSession) -> None:
        agent = AgentModel(name="no-guardrail", model_config_db={})
        db_session.add(agent)
        await db_session.flush()
        assert agent.guardrail_config is None

    @pytest.mark.asyncio
    async def test_guardrail_config_with_dict(self, db_session: AsyncSession) -> None:
        config = {
            "input_security": {"enabled": True, "pii_entities": ["email", "phone"]},
            "output_security": {"enabled": True},
            "data_security": {"pii_storage_mode": "mask_only"},
        }
        agent = AgentModel(name="with-guardrail", model_config_db={}, guardrail_config=config)
        db_session.add(agent)
        await db_session.flush()
        assert agent.guardrail_config is not None
        assert agent.guardrail_config["input_security"]["pii_entities"] == ["email", "phone"]

    def test_create_schema_with_guardrail_config(self) -> None:
        schema = AgentCreateSchema(
            name="test",
            model_config={"model": "gpt-4o"},
            guardrail_config={"input_security": {"enabled": True}},
        )
        assert schema.guardrail_config == {"input_security": {"enabled": True}}

    def test_create_schema_guardrail_config_default_none(self) -> None:
        schema = AgentCreateSchema(name="test", model_config={"model": "gpt-4o"})
        assert schema.guardrail_config is None

    def test_update_schema_guardrail_config(self) -> None:
        from hecate.models.agent import AgentUpdateSchema

        schema = AgentUpdateSchema(guardrail_config={"input_security": {"enabled": False}})
        assert schema.guardrail_config == {"input_security": {"enabled": False}}

    def test_read_schema_guardrail_config(self) -> None:
        now = datetime.now()
        agent = AgentModel(
            name="gc-test",
            model_config_db={"model": "gpt-4"},
            guardrail_config={"data_security": {"pii_storage_mode": "mask_and_encrypt"}},
            enable_suggestions=True,
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
        assert schema.guardrail_config == {"data_security": {"pii_storage_mode": "mask_and_encrypt"}}


class TestConversationModel:
    """Verify Conversation ORM creation and its one-to-one link with Session."""

    @pytest.mark.asyncio
    async def test_create_conversation(self, db_session: AsyncSession) -> None:
        """A conversation linked to an agent persists with the correct foreign key."""
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
        """A session can be created first with a null conversation_id and linked later."""
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
    """Verify Session ORM creation, default status, and status transitions."""

    @pytest.mark.asyncio
    async def test_create_session(self, db_session: AsyncSession) -> None:
        """A new session defaults to ``active`` status."""
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
        """Session status and current_node can be updated after initial creation."""
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
    """Verify Message ORM creation for user, assistant (with tool calls), and
    tool-result messages."""

    @pytest.mark.asyncio
    async def test_create_message(self, db_session: AsyncSession) -> None:
        """A basic user message persists with the correct role and content."""
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
        """An assistant message can store structured tool call data in a JSONB column."""
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
        """A tool-result message links back to its originating tool call via tool_call_id."""
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
    """Verify Tool ORM creation for builtin and MCP tools, plus schema validation."""

    @pytest.mark.asyncio
    async def test_create_builtin_tool(self, db_session: AsyncSession) -> None:
        """A builtin tool defaults to ``approval_required=False``."""
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
        """An MCP tool stores its server URL and tool name for runtime discovery."""
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
        """ToolCreateSchema accepts a valid builtin tool definition."""
        schema = ToolCreateSchema(
            name="test",
            description="desc",
            source="builtin",
            parameters={"type": "object"},
        )
        assert schema.source == "builtin"

    def test_tool_invalid_source(self) -> None:
        """An unrecognised tool source is rejected by the schema."""
        with pytest.raises(ValidationError):
            ToolCreateSchema(
                name="test",
                description="desc",
                source="unknown",
                parameters={},
            )

    @pytest.mark.asyncio
    async def test_tool_with_available_when(self, db_session: AsyncSession) -> None:
        """ToolModel accepts available_when field and persists to database."""
        tool = ToolModel(
            name="admin_tool",
            description="Admin only",
            source="builtin",
            parameters={},
            available_when="user_role == 'admin'",
        )
        db_session.add(tool)
        await db_session.flush()
        assert tool.available_when == "user_role == 'admin'"

    @pytest.mark.asyncio
    async def test_tool_without_available_when(self, db_session: AsyncSession) -> None:
        """ToolModel without available_when defaults to None."""
        tool = ToolModel(
            name="public_tool",
            description="Public",
            source="builtin",
            parameters={},
        )
        db_session.add(tool)
        await db_session.flush()
        assert tool.available_when is None

    def test_tool_create_schema_with_available_when(self) -> None:
        """ToolCreateSchema accepts optional available_when string."""
        schema = ToolCreateSchema(
            name="test",
            description="desc",
            source="builtin",
            parameters={},
            available_when="user_role == 'admin'",
        )
        assert schema.available_when == "user_role == 'admin'"

    def test_tool_create_schema_without_available_when(self) -> None:
        """ToolCreateSchema without available_when defaults to None."""
        schema = ToolCreateSchema(
            name="test",
            description="desc",
            source="builtin",
            parameters={},
        )
        assert schema.available_when is None

    def test_tool_read_schema_includes_available_when(self) -> None:
        """ToolReadSchema includes available_when in serialized output."""
        from datetime import datetime

        from hecate.models.tool import ToolReadSchema

        schema = ToolReadSchema(
            id="00000000-0000-0000-0000-000000000001",
            workspace_id="00000000-0000-0000-0000-000000000000",
            name="test",
            description="desc",
            source="builtin",
            parameters={},
            returns=None,
            risk_level="LOW",
            approval_required=False,
            sandbox_enabled=False,
            sandbox_config={},
            mcp_server=None,
            mcp_tool_name=None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            deleted=False,
            deleted_at=None,
            available_when="user_role == 'admin'",
        )
        assert schema.available_when == "user_role == 'admin'"


class TestKnowledgeBaseModel:
    """Verify KnowledgeBase ORM creation with defaults and schema defaults."""

    @pytest.mark.asyncio
    async def test_create_knowledge_base(self, db_session: AsyncSession) -> None:
        """A new knowledge base gets server defaults for embedding model and chunk settings."""
        kb = KnowledgeBaseModel(
            name="test-kb",
            collection_name="kb_test",
        )
        db_session.add(kb)
        await db_session.flush()
        assert kb.embedding_model == "BAAI/bge-m3"
        assert kb.chunk_size == 512
        assert kb.chunk_overlap == 100

    def test_kb_create_schema_defaults(self) -> None:
        """KnowledgeBaseCreateSchema provides defaults for embedding_model and chunk_strategy."""
        schema = KnowledgeBaseCreateSchema(name="test")
        assert schema.embedding_model == "BAAI/bge-m3"
        assert schema.chunk_strategy == "fixed"


class TestDocumentModel:
    """Verify Document ORM creation, initial status, and status transitions
    through the parsing lifecycle."""

    @pytest.mark.asyncio
    async def test_create_document(self, db_session: AsyncSession) -> None:
        """A new document defaults to ``pending`` parsing status with zero chunks."""
        kb = KnowledgeBaseModel(name="doc-kb", collection_name="kb_doc")
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
        """A document can transition from pending to parsing to completed with a chunk count."""
        kb = KnowledgeBaseModel(name="status-kb", collection_name="kb_status")
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
        """A failed document stores an error message alongside the failed status."""
        kb = KnowledgeBaseModel(name="fail-kb", collection_name="kb_fail")
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
    """Verify Skill ORM creation with defaults and schema name-pattern validation."""

    @pytest.mark.asyncio
    async def test_create_skill(self, db_session: AsyncSession) -> None:
        """A new skill gets server defaults for max_tokens and auto_load."""
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
        """SkillCreateSchema accepts kebab-case names matching the allowed pattern."""
        schema = SkillCreateSchema(
            name="web-search",
            description="desc",
            source="system",
            instructions="test",
        )
        assert schema.name == "web-search"

    def test_skill_invalid_name(self) -> None:
        """Names with spaces or special characters are rejected by the schema."""
        with pytest.raises(ValidationError):
            SkillCreateSchema(
                name="Invalid Name!",
                description="desc",
                source="system",
                instructions="test",
            )


class TestCheckpointModel:
    """Verify Checkpoint ORM creation, sequential superstep ordering, and schema defaults."""

    @pytest.mark.asyncio
    async def test_create_checkpoint(self, db_session: AsyncSession) -> None:
        """A checkpoint stores channel_state as JSONB alongside its superstep index."""
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
        """Multiple checkpoints can be persisted for the same session with increasing superstep numbers."""
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
        """CheckpointCreateSchema fills empty defaults for channel_state, pending_writes, and metadata."""
        schema = CheckpointCreateSchema(
            session_id=uuid.uuid4(),
            superstep=1,
        )
        assert schema.channel_state == {}
        assert schema.pending_writes == []
        assert schema.metadata == {}


class TestReadSchemaFromAttributes:
    """Verify that ``ReadSchema.model_validate(orm_instance)`` works for all
    models that have a ``metadata_`` column.

    Because ``metadata`` is a reserved attribute on SQLAlchemy's declarative
    base, the ORM column is named ``metadata_`` while the Pydantic schema
    exposes it as ``metadata``.  These tests confirm that the alias mapping
    works correctly when building a read schema directly from an ORM object.
    """

    def _make_base_attrs(self) -> dict:
        """Return common attributes (id, timestamps) shared by all models."""
        now = datetime.now()
        return {"id": uuid.uuid4(), "created_at": now, "updated_at": now}

    def test_session_read_schema(self) -> None:
        from hecate.models.session import SessionModel

        attrs = self._make_base_attrs()
        session = SessionModel(
            agent_id=uuid.uuid4(),
            status="active",
            metadata_={"key": "value"},
            workspace_id=uuid.UUID(int=0),
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
            workspace_id=uuid.UUID(int=0),
            **attrs,
        )
        schema = MessageReadSchema.model_validate(msg)
        assert schema.metadata == {"tokens": 5}
        assert schema.tool_calls == [{"id": "1", "name": "test"}]

    def test_skill_read_schema(self) -> None:
        from hecate.models.skill import SkillModel

        attrs = self._make_base_attrs()
        skill = SkillModel(
            workspace_id=uuid.UUID(int=0),
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
            workspace_id=uuid.UUID(int=0),
        )
        cp.id = cp_id
        cp.created_at = datetime.now()
        schema = CheckpointReadSchema.model_validate(cp)
        assert schema.metadata == {"interrupted": False}
        assert schema.superstep == 1
