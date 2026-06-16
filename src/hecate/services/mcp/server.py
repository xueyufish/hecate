"""MCP Server exposing Hecate capabilities as MCP tools, resources, and prompts.

Provides a ``create_mcp_server()`` factory that builds a configured ``FastMCP``
instance with all Hecate capabilities registered as MCP primitives.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import select

from hecate.core.database import async_session_factory
from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.message import MessageModel
from hecate.models.tool import ToolModel
from hecate.services.mcp.session_manager import MCPSessionManager

logger = logging.getLogger(__name__)


def create_mcp_server() -> FastMCP:
    """Create and configure the Hecate MCP Server.

    Returns:
        A ``FastMCP`` instance with all tools, resources, and prompts registered.
    """
    mcp = FastMCP("hecate-mcp-server")
    session_mgr = MCPSessionManager()

    # ----------------------------------------------------------------
    # AGENT RUNTIME TOOLS
    # ----------------------------------------------------------------

    @mcp.tool
    async def session_create(agent_id: str) -> str:
        """Create a new Hecate session for an agent.

        Args:
            agent_id: UUID string of the agent to create a session for.

        Returns:
            JSON with ``session_id`` and ``status``.
        """
        async with async_session_factory() as db:
            try:
                result = await session_mgr.create_session(agent_id, db)
                await db.commit()
                return json.dumps(result)
            except Exception as e:
                await db.rollback()
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def agent_chat(session_id: str, message: str) -> str:
        """Send a message to an active agent session and get the response.

        Args:
            session_id: UUID string of the session (from ``session_create``).
            message: The user message to send.

        Returns:
            JSON with the agent's ``response`` and optional metadata.
        """
        async with async_session_factory() as db:
            try:
                session = await session_mgr.get_session(session_id, db)
                if session is None:
                    return json.dumps({"error": "Session not found"})

                from hecate.services.llm.service import llm_service
                from hecate.services.orchestration.engine_port_adapter import create_engine_port
                from hecate.services.workflow.execution_service import WorkflowExecutionService

                port = create_engine_port(db, llm_service)
                exec_service = WorkflowExecutionService(port=port, db=db)

                result = await exec_service.execute(
                    agent_mode="chat",
                    messages=[{"role": "user", "content": message}],
                    stream=False,
                    session_id=session_id,
                    agent_id=str(session.agent_id),
                )

                content = result.get("content", "") if isinstance(result, dict) else str(result)
                return json.dumps({"response": content, "session_id": session_id})
            except Exception as e:
                logger.error("agent_chat failed: %s", e, exc_info=True)
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def session_list(agent_id: str | None = None) -> str:
        """List active sessions, optionally filtered by agent_id.

        Args:
            agent_id: Optional UUID string to filter sessions.

        Returns:
            JSON array of session summaries.
        """
        async with async_session_factory() as db:
            try:
                sessions = await session_mgr.list_sessions(agent_id, db)
                return json.dumps(sessions)
            except Exception as e:
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def session_resume(session_id: str, message: str) -> str:
        """Resume an interrupted session with a new message.

        Args:
            session_id: UUID string of the interrupted session.
            message: The user message to resume with.

        Returns:
            JSON with the agent's ``response``.
        """
        async with async_session_factory() as db:
            try:
                session = await session_mgr.get_session(session_id, db)
                if session is None:
                    return json.dumps({"error": "Session not found"})

                from hecate.services.llm.service import llm_service
                from hecate.services.orchestration.engine_port_adapter import create_engine_port
                from hecate.services.workflow.execution_service import WorkflowExecutionService

                port = create_engine_port(db, llm_service)
                exec_service = WorkflowExecutionService(port=port, db=db)

                result = await exec_service.execute(
                    agent_mode="chat",
                    messages=[{"role": "user", "content": message}],
                    stream=False,
                    session_id=session_id,
                    agent_id=str(session.agent_id),
                )

                content = result.get("content", "") if isinstance(result, dict) else str(result)
                return json.dumps({"response": content, "session_id": session_id})
            except Exception as e:
                logger.error("session_resume failed: %s", e, exc_info=True)
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def conversation_history(conversation_id: str) -> str:
        """Retrieve conversation message history.

        Args:
            conversation_id: UUID string of the conversation.

        Returns:
            JSON array of messages with role, content, and timestamp.
        """
        async with async_session_factory() as db:
            try:
                cid = uuid.UUID(conversation_id)
                result = await db.execute(
                    select(MessageModel)
                    .where(MessageModel.conversation_id == cid, ~MessageModel.deleted)
                    .order_by(MessageModel.created_at.asc())
                    .limit(100)
                )
                messages = result.scalars().all()
                return json.dumps(
                    [
                        {
                            "id": str(m.id),
                            "role": m.role,
                            "content": m.content,
                            "created_at": m.created_at.isoformat() if m.created_at else None,
                        }
                        for m in messages
                    ]
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

    # ----------------------------------------------------------------
    # AGENT CRUD TOOLS
    # ----------------------------------------------------------------

    @mcp.tool
    async def agent_list(workspace_id: str | None = None) -> str:
        """List all agents with ID, name, mode, and model config.

        Args:
            workspace_id: Optional UUID string to filter by workspace.

        Returns:
            JSON array of agent summaries.
        """
        async with async_session_factory() as db:
            try:
                query = select(AgentModel).where(~AgentModel.deleted)
                if workspace_id:
                    query = query.where(AgentModel.workspace_id == uuid.UUID(workspace_id))
                result = await db.execute(query.order_by(AgentModel.created_at.desc()).limit(100))
                agents = result.scalars().all()
                return json.dumps(
                    [
                        {
                            "id": str(a.id),
                            "name": a.name,
                            "mode": a.mode,
                            "model_config": a.model_config_db,
                            "persona": a.persona,
                        }
                        for a in agents
                    ]
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def agent_create(
        name: str,
        model_config: dict,
        mode: str = "chat",
        persona: str | None = None,
        tools: list | None = None,
        knowledge_base_ids: list | None = None,
    ) -> str:
        """Create a new agent.

        Args:
            name: Agent name.
            model_config: LLM configuration dict (e.g. ``{"model": "gpt-4o"}``).
            mode: Execution mode — ``"chat"``, ``"three_layer"``, or ``"workflow"``.
            persona: Optional system persona/prompt.
            tools: Optional list of tool IDs.
            knowledge_base_ids: Optional list of knowledge base IDs.

        Returns:
            JSON with agent ``id`` and metadata.
        """
        async with async_session_factory() as db:
            try:
                agent = AgentModel(
                    name=name,
                    model_config_db=model_config,
                    mode=mode,
                    persona=persona,
                    tools=tools or [],
                    knowledge_base_ids=knowledge_base_ids or [],
                )
                db.add(agent)
                await db.flush()
                await db.refresh(agent)
                await db.commit()
                return json.dumps(
                    {
                        "id": str(agent.id),
                        "name": agent.name,
                        "mode": agent.mode,
                        "model_config": agent.model_config_db,
                    }
                )
            except Exception as e:
                await db.rollback()
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def agent_update(agent_id: str, **fields: Any) -> str:
        """Update agent fields. Pass only the fields to change.

        Args:
            agent_id: UUID string of the agent to update.
            **fields: Fields to update (name, persona, mode, model_config, tools, knowledge_base_ids).

        Returns:
            JSON with updated agent metadata.
        """
        allowed_fields = {"name", "persona", "mode", "model_config", "tools", "knowledge_base_ids", "risk_level"}
        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(AgentModel).where(AgentModel.id == uuid.UUID(agent_id), ~AgentModel.deleted)
                )
                agent = result.scalar_one_or_none()
                if agent is None:
                    return json.dumps({"error": "Agent not found"})

                for key, value in fields.items():
                    if key not in allowed_fields:
                        continue
                    if key == "model_config":
                        agent.model_config_db = value
                    else:
                        setattr(agent, key, value)

                await db.flush()
                await db.refresh(agent)
                await db.commit()
                return json.dumps(
                    {
                        "id": str(agent.id),
                        "name": agent.name,
                        "mode": agent.mode,
                        "model_config": agent.model_config_db,
                    }
                )
            except Exception as e:
                await db.rollback()
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def agent_delete(agent_id: str) -> str:
        """Soft-delete an agent by ID.

        Args:
            agent_id: UUID string of the agent to delete.

        Returns:
            JSON confirmation.
        """
        import datetime as _dt

        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(AgentModel).where(AgentModel.id == uuid.UUID(agent_id), ~AgentModel.deleted)
                )
                agent = result.scalar_one_or_none()
                if agent is None:
                    return json.dumps({"error": "Agent not found"})

                agent.deleted = True
                agent.deleted_at = _dt.datetime.now(_dt.timezone.utc)  # noqa: UP017
                await db.flush()
                await db.commit()
                return json.dumps({"deleted": True, "agent_id": agent_id})
            except Exception as e:
                await db.rollback()
                return json.dumps({"error": str(e)})

    # ----------------------------------------------------------------
    # KNOWLEDGE BASE TOOLS
    # ----------------------------------------------------------------

    @mcp.tool
    async def knowledge_list() -> str:
        """List all knowledge bases.

        Returns:
            JSON array of knowledge base summaries.
        """
        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(KnowledgeBaseModel)
                    .where(~KnowledgeBaseModel.deleted)
                    .order_by(KnowledgeBaseModel.created_at.desc())
                    .limit(100)
                )
                kbs = result.scalars().all()
                return json.dumps(
                    [
                        {
                            "id": str(kb.id),
                            "name": kb.name,
                            "description": kb.description,
                            "embedding_model": kb.embedding_model,
                            "collection_name": kb.collection_name,
                            "search_mode": kb.search_mode,
                        }
                        for kb in kbs
                    ]
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def knowledge_search(
        kb_id: str,
        query: str,
        limit: int = 10,
        mode: str = "hybrid",
    ) -> str:
        """Search a knowledge base for relevant document chunks.

        Args:
            kb_id: UUID string of the knowledge base.
            query: Search query text.
            limit: Maximum results to return (default 10).
            mode: Search mode — ``"hybrid"``, ``"dense"``, or ``"sparse"``.

        Returns:
            JSON array of matching chunks with content, score, and metadata.
        """
        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == uuid.UUID(kb_id),
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None:
                    return json.dumps({"error": "Knowledge base not found"})

                from hecate.services.rag.service import knowledge_base_service

                search_results = await knowledge_base_service.search(
                    collection_name=kb.collection_name,
                    query=query,
                    limit=limit,
                    mode=mode,
                )
                return json.dumps(
                    [
                        {
                            "content": r.content,
                            "score": r.score,
                            "metadata": r.metadata if hasattr(r, "metadata") else {},
                        }
                        for r in search_results
                    ]
                )
            except Exception as e:
                logger.error("knowledge_search failed: %s", e, exc_info=True)
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def knowledge_create(
        name: str,
        description: str = "",
        embedding_model: str = "BAAI/bge-m3",
        chunk_strategy: str = "auto",
    ) -> str:
        """Create a new knowledge base with a vector collection.

        Args:
            name: Knowledge base name.
            description: Optional description.
            embedding_model: Embedding model identifier.
            chunk_strategy: Chunking strategy — ``"auto"``, ``"fixed"``, or ``"semantic"``.

        Returns:
            JSON with knowledge base ``id``, ``name``, and ``collection_name``.
        """
        async with async_session_factory() as db:
            try:
                collection_name = f"kb_{uuid.uuid4().hex[:8]}"
                kb = KnowledgeBaseModel(
                    name=name,
                    description=description,
                    embedding_model=embedding_model,
                    chunk_strategy=chunk_strategy,
                    collection_name=collection_name,
                    search_mode="hybrid",
                )
                db.add(kb)
                await db.flush()
                await db.refresh(kb)

                from hecate.services.rag.service import knowledge_base_service

                await knowledge_base_service.create_collection(
                    collection_name=collection_name,
                    with_sparse=True,
                )

                await db.commit()
                return json.dumps(
                    {
                        "id": str(kb.id),
                        "name": kb.name,
                        "collection_name": collection_name,
                    }
                )
            except Exception as e:
                await db.rollback()
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def knowledge_ingest(
        kb_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> str:
        """Ingest text content into a knowledge base.

        Args:
            kb_id: UUID string of the knowledge base.
            content: Text content to ingest.
            metadata: Optional metadata dict to attach to chunks.

        Returns:
            JSON with ingestion result.
        """
        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == uuid.UUID(kb_id),
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None:
                    return json.dumps({"error": "Knowledge base not found"})

                from hecate.services.rag.service import knowledge_base_service

                ingest_result = await knowledge_base_service.ingest_document_text(
                    text=content,
                    collection_name=kb.collection_name,
                    metadata=metadata,
                )
                return json.dumps(ingest_result)
            except Exception as e:
                logger.error("knowledge_ingest failed: %s", e, exc_info=True)
                return json.dumps({"error": str(e)})

    # ----------------------------------------------------------------
    # TOOL EXECUTION TOOLS
    # ----------------------------------------------------------------

    @mcp.tool
    async def tool_list(source: str | None = None) -> str:
        """List registered tools, optionally filtered by source.

        Args:
            source: Optional filter — ``"builtin"``, ``"custom"``, or ``"mcp"``.

        Returns:
            JSON array of tool summaries.
        """
        async with async_session_factory() as db:
            try:
                query = select(ToolModel).where(~ToolModel.deleted)
                if source:
                    query = query.where(ToolModel.source == source)
                result = await db.execute(query.order_by(ToolModel.created_at.desc()).limit(100))
                tools = result.scalars().all()
                return json.dumps(
                    [
                        {
                            "id": str(t.id),
                            "name": t.name,
                            "description": t.description,
                            "source": t.source,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                )
            except Exception as e:
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def tool_execute(tool_name: str, arguments: dict) -> str:
        """Execute a registered tool by name.

        Args:
            tool_name: Name of the tool to execute.
            arguments: Tool arguments dict.

        Returns:
            JSON with the tool execution result.
        """
        async with async_session_factory() as db:
            try:
                from hecate.core.config import settings
                from hecate.services.tool.builtin import BuiltInToolExecutor
                from hecate.services.tool.registry import ToolRegistry
                from hecate.services.tool.search.factory import create_search_provider

                search_provider = create_search_provider(
                    provider=settings.SEARCH_PROVIDER,
                    api_key=settings.SEARCH_API_KEY,
                )
                builtin_executor = BuiltInToolExecutor(
                    search_provider=search_provider,
                    workspace_root=settings.WORKSPACE_ROOT,
                )
                registry = ToolRegistry(db=db, builtin_executor=builtin_executor)
                result = await registry.execute(tool_name, arguments)
                return json.dumps({"result": result})
            except Exception as e:
                logger.error("tool_execute failed: %s", e, exc_info=True)
                return json.dumps({"error": str(e)})

    @mcp.tool
    async def tool_create(
        name: str,
        description: str,
        parameters: dict,
        source: str = "custom",
    ) -> str:
        """Register a new tool.

        Args:
            name: Tool name.
            description: Tool description.
            parameters: JSON Schema for tool parameters.
            source: Tool source — ``"builtin"``, ``"custom"``, or ``"mcp"``.

        Returns:
            JSON with tool ``id`` and metadata.
        """
        async with async_session_factory() as db:
            try:
                tool = ToolModel(
                    name=name,
                    description=description,
                    source=source,
                    parameters=parameters,
                )
                db.add(tool)
                await db.flush()
                await db.refresh(tool)
                await db.commit()
                return json.dumps(
                    {
                        "id": str(tool.id),
                        "name": tool.name,
                        "source": tool.source,
                    }
                )
            except Exception as e:
                await db.rollback()
                return json.dumps({"error": str(e)})

    # ----------------------------------------------------------------
    # RESOURCES
    # ----------------------------------------------------------------

    @mcp.resource("agent://list")
    async def resource_agent_list() -> str:
        """Agent catalog — all agents with metadata."""
        async with async_session_factory() as db:
            result = await db.execute(select(AgentModel).where(~AgentModel.deleted).limit(100))
            agents = result.scalars().all()
            return json.dumps([{"id": str(a.id), "name": a.name, "mode": a.mode} for a in agents])

    @mcp.resource("knowledge://list")
    async def resource_knowledge_list() -> str:
        """Knowledge base catalog."""
        async with async_session_factory() as db:
            result = await db.execute(select(KnowledgeBaseModel).where(~KnowledgeBaseModel.deleted).limit(100))
            kbs = result.scalars().all()
            return json.dumps(
                [{"id": str(kb.id), "name": kb.name, "collection_name": kb.collection_name} for kb in kbs]
            )

    @mcp.resource("tool://list")
    async def resource_tool_list() -> str:
        """Tool catalog."""
        async with async_session_factory() as db:
            result = await db.execute(select(ToolModel).where(~ToolModel.deleted).limit(100))
            tools = result.scalars().all()
            return json.dumps([{"id": str(t.id), "name": t.name, "source": t.source} for t in tools])

    # ----------------------------------------------------------------
    # PROMPTS
    # ----------------------------------------------------------------

    @mcp.prompt
    async def system_template(prompt_id: str) -> str:
        """Retrieve a stored prompt template by ID.

        Args:
            prompt_id: UUID string of the prompt.

        Returns:
            The prompt template content.
        """
        from hecate.models.prompt import PromptVersionModel

        async with async_session_factory() as db:
            try:
                result = await db.execute(
                    select(PromptVersionModel)
                    .where(
                        PromptVersionModel.prompt_id == uuid.UUID(prompt_id),
                        ~PromptVersionModel.deleted,
                    )
                    .order_by(PromptVersionModel.version.desc())
                    .limit(1)
                )
                version = result.scalar_one_or_none()
                if version is None:
                    return f"Prompt template {prompt_id} not found"
                return version.content
            except Exception:
                return f"Prompt template {prompt_id} not found"

    return mcp
