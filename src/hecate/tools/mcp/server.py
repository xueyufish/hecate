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

from hecate.core.auth_context import AuthContext
from hecate.core.database import async_session_factory
from hecate.models.agent import AgentModel
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.tool import ToolModel
from hecate.tools.mcp.auth_middleware import get_transport_auth_context
from hecate.tools.mcp.session_manager import MCPSessionManager

logger = logging.getLogger(__name__)

_BUNDLED_WS = uuid.UUID(int=0)


def _auth() -> AuthContext:
    """Caller identity resolved by the transport middleware.

    Raises PermissionError for in-process calls without an HTTP transport —
    tools never execute with global permissions.
    """
    return get_transport_auth_context()


def _workspace_ok(resource_workspace_id: Any, ctx: AuthContext) -> bool:
    """Whether ctx may touch a resource owned by this workspace."""
    if ctx.is_system_scope:
        return True
    return resource_workspace_id == (ctx.workspace_id or _BUNDLED_WS)


def _tenant_filter(model_workspace_col: Any, ctx: AuthContext) -> Any:
    """SQL filter constraining a model column to the caller's workspace."""
    if ctx.is_system_scope:
        return True
    return model_workspace_col.in_([ctx.workspace_id or _BUNDLED_WS, _BUNDLED_WS])


def _maybe_attach_gateway(mcp: FastMCP, gateway_enabled: bool | None) -> None:
    """Attach the MCP Gateway middleware when the gateway is enabled.

    With the gateway on, ``tools/list`` becomes caller-scoped (first-party
    ∪ federated, filtered through the policy pipeline) and ``tools/call``
    is authorized and routed for federated names.
    """
    from hecate.core.config import settings

    if not (gateway_enabled if gateway_enabled is not None else settings.GATEWAY_ENABLED):
        return

    from hecate.tools.api.mcp import get_mcp_manager
    from hecate.tools.gateway.authz import GatewayAuthorizer
    from hecate.tools.gateway.executor import RestToolExecutor
    from hecate.tools.gateway.federation import FederatedCatalog
    from hecate.tools.gateway.middleware import GatewayMiddleware

    manager = get_mcp_manager()
    executor = RestToolExecutor(timeout=settings.MCP_REQUEST_TIMEOUT)
    catalog = FederatedCatalog(
        db_session_factory=async_session_factory,
        mcp_manager=manager,
        executor=executor,
    )
    mcp.add_middleware(GatewayMiddleware(catalog, GatewayAuthorizer()))
    logger.info("MCP Gateway middleware attached (federated catalog active)")


def create_mcp_server(gateway_enabled: bool | None = None) -> FastMCP:
    """Create and configure the Hecate MCP Server.

    Args:
        gateway_enabled: Force the MCP Gateway middleware on/off; ``None``
            (default) follows the ``GATEWAY_ENABLED`` setting. When on, the
            server's tool catalog becomes caller-scoped and federates
            gateway targets (rest projections + external MCP servers).

    Returns:
        A ``FastMCP`` instance with all tools, resources, and prompts registered.
    """
    mcp = FastMCP("hecate-mcp-server")
    session_mgr = MCPSessionManager()

    _maybe_attach_gateway(mcp, gateway_enabled)

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
                ctx = _auth()
                agent_row = await db.execute(
                    select(AgentModel).where(AgentModel.id == uuid.UUID(agent_id), ~AgentModel.deleted)
                )
                agent = agent_row.scalar_one_or_none()
                if agent is None or not _workspace_ok(agent.workspace_id, ctx):
                    return json.dumps({"error": "Agent not found"})
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
                ctx = _auth()
                agent_row = await db.execute(
                    select(AgentModel).where(AgentModel.id == session.agent_id, ~AgentModel.deleted)
                )
                agent = agent_row.scalar_one_or_none()
                if agent is None or not _workspace_ok(agent.workspace_id, ctx):
                    return json.dumps({"error": "Session not found"})

                from hecate_llm.service import llm_service

                from hecate.core.composition.runtime_port_adapter import create_runtime_port
                from hecate.studio.workflows.execution_service import WorkflowExecutionService

                port = create_runtime_port(db, llm_service)
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
                ctx = _auth()
                sessions = await session_mgr.list_sessions(agent_id, db)
                if not ctx.is_system_scope:
                    allowed_ws = ctx.workspace_id or _BUNDLED_WS
                    agent_rows = await db.execute(
                        select(AgentModel.id).where(AgentModel.workspace_id == allowed_ws, ~AgentModel.deleted)
                    )
                    allowed_ids = {str(row) for row in agent_rows.scalars().all()}
                    sessions = [s for s in sessions if s.get("agent_id") in allowed_ids]
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
                ctx = _auth()
                agent_row = await db.execute(
                    select(AgentModel).where(AgentModel.id == session.agent_id, ~AgentModel.deleted)
                )
                agent = agent_row.scalar_one_or_none()
                if agent is None or not _workspace_ok(agent.workspace_id, ctx):
                    return json.dumps({"error": "Session not found"})

                from hecate_llm.service import llm_service

                from hecate.core.composition.runtime_port_adapter import create_runtime_port
                from hecate.studio.workflows.execution_service import WorkflowExecutionService

                port = create_runtime_port(db, llm_service)
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

        A2 closure: messages are projected from the EventStore via
        SessionModel.conversation_id → derive_session_messages.

        Args:
            conversation_id: UUID string of the conversation.

        Returns:
            JSON array of messages with role, content, and timestamp.
        """
        from hecate.core.config import settings
        from hecate.models.session import SessionModel
        from hecate.studio.event_state import create_event_store
        from hecate.studio.replay.assembler import derive_session_messages

        async with async_session_factory() as db:
            try:
                ctx = _auth()
                cid = uuid.UUID(conversation_id)
                session_rows = (
                    (await db.execute(select(SessionModel).where(SessionModel.conversation_id == cid))).scalars().all()
                )
                if not ctx.is_system_scope:
                    allowed_ws = ctx.workspace_id or _BUNDLED_WS
                    allowed_ids = set(
                        (
                            await db.execute(
                                select(AgentModel.id).where(AgentModel.workspace_id == allowed_ws, ~AgentModel.deleted)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    session_rows = [s for s in session_rows if s.agent_id in allowed_ids]
                    if not session_rows:
                        return json.dumps([])

                store = create_event_store(settings)
                messages: list[dict] = []
                for session in session_rows:
                    messages.extend(await derive_session_messages(session.id, store))
                messages = messages[:100]

                return json.dumps(
                    [
                        {
                            "id": m.get("created_at"),
                            "role": m.get("role"),
                            "content": m.get("content"),
                            "created_at": m.get("created_at"),
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
    async def agent_list() -> str:
        """List agents visible to the caller, with ID, name, mode, and model config.

        The workspace filter comes from the caller's authenticated identity —
        callers cannot widen it by passing a workspace id.

        Returns:
            JSON array of agent summaries.
        """
        async with async_session_factory() as db:
            try:
                ctx = _auth()
                query = select(AgentModel).where(~AgentModel.deleted)
                if not ctx.is_system_scope:
                    query = query.where(AgentModel.workspace_id == (ctx.workspace_id or _BUNDLED_WS))
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
                ctx = _auth()
                if not ctx.is_system_scope and ctx.workspace_id is None:
                    return json.dumps({"error": "Workspace context required"})
                agent = AgentModel(
                    workspace_id=ctx.workspace_id or _BUNDLED_WS,
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
    async def agent_update(agent_id: str, fields: dict[str, Any]) -> str:
        """Update agent fields. Pass only the fields to change.

        fastmcp 4 does not support ``**kwargs`` tool parameters; callers pass a
        single ``fields`` dict containing only the keys they want to update.

        Args:
            agent_id: UUID string of the agent to update.
            fields: Mapping of field name to new value. Allowed keys: ``name``,
                ``persona``, ``mode``, ``model_config``, ``tools``,
                ``knowledge_base_ids``, ``risk_level``.

        Returns:
            JSON with updated agent metadata.
        """
        allowed_fields = {"name", "persona", "mode", "model_config", "tools", "knowledge_base_ids", "risk_level"}
        async with async_session_factory() as db:
            try:
                ctx = _auth()
                result = await db.execute(
                    select(AgentModel).where(AgentModel.id == uuid.UUID(agent_id), ~AgentModel.deleted)
                )
                agent = result.scalar_one_or_none()
                if agent is None or not _workspace_ok(agent.workspace_id, ctx):
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
                ctx = _auth()
                result = await db.execute(
                    select(AgentModel).where(AgentModel.id == uuid.UUID(agent_id), ~AgentModel.deleted)
                )
                agent = result.scalar_one_or_none()
                if agent is None or not _workspace_ok(agent.workspace_id, ctx):
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
                ctx = _auth()
                result = await db.execute(
                    select(KnowledgeBaseModel)
                    .where(~KnowledgeBaseModel.deleted, _tenant_filter(KnowledgeBaseModel.workspace_id, ctx))
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
                ctx = _auth()
                result = await db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == uuid.UUID(kb_id),
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None or not _workspace_ok(kb.workspace_id, ctx):
                    return json.dumps({"error": "Knowledge base not found"})

                from hecate.core.composition.memory_provider import (
                    resolve_memory_provider,
                )

                provider = resolve_memory_provider()
                if provider is None:
                    return json.dumps({"error": "No memory provider configured"})

                search_results = await provider.search(
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
                ctx = _auth()
                if not ctx.is_system_scope and ctx.workspace_id is None:
                    return json.dumps({"error": "Workspace context required"})
                collection_name = f"kb_{uuid.uuid4().hex[:8]}"
                kb = KnowledgeBaseModel(
                    workspace_id=ctx.workspace_id or _BUNDLED_WS,
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

                from hecate_memory.rag.service import knowledge_base_service

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
                ctx = _auth()
                result = await db.execute(
                    select(KnowledgeBaseModel).where(
                        KnowledgeBaseModel.id == uuid.UUID(kb_id),
                        ~KnowledgeBaseModel.deleted,
                    )
                )
                kb = result.scalar_one_or_none()
                if kb is None or not _workspace_ok(kb.workspace_id, ctx):
                    return json.dumps({"error": "Knowledge base not found"})

                from hecate_memory.rag.service import knowledge_base_service

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
                ctx = _auth()
                query = select(ToolModel).where(~ToolModel.deleted, _tenant_filter(ToolModel.workspace_id, ctx))
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
                ctx = _auth()
                tool_row = await db.execute(select(ToolModel).where(ToolModel.name == tool_name, ~ToolModel.deleted))
                registered = tool_row.scalar_one_or_none()
                if registered is not None and not _workspace_ok(registered.workspace_id, ctx):
                    return json.dumps({"error": "Tool not found"})

                from hecate.core.config import settings
                from hecate.tools.tool.builtin import BuiltInToolExecutor
                from hecate.tools.tool.registry import ToolRegistry
                from hecate.tools.tool.search.factory import create_search_provider

                search_provider = create_search_provider(
                    provider=settings.SEARCH_PROVIDER,
                    api_key=settings.SEARCH_API_KEY,
                )
                memory_backend = None
                if settings.MEMORY_TOOLS_ENABLED:
                    try:
                        from hecate_memory.memory.tools_backend import MemoryToolBackend

                        memory_backend = MemoryToolBackend(db)
                    except ImportError:
                        memory_backend = None
                builtin_executor = BuiltInToolExecutor(
                    search_provider=search_provider,
                    workspace_root=settings.WORKSPACE_ROOT,
                    memory_backend=memory_backend,
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
                ctx = _auth()
                if not ctx.is_system_scope and ctx.workspace_id is None:
                    return json.dumps({"error": "Workspace context required"})
                tool = ToolModel(
                    workspace_id=ctx.workspace_id or _BUNDLED_WS,
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
        """Agent catalog — agents visible to the caller."""
        async with async_session_factory() as db:
            ctx = _auth()
            query = select(AgentModel).where(~AgentModel.deleted).limit(100)
            if not ctx.is_system_scope:
                query = query.where(AgentModel.workspace_id == (ctx.workspace_id or _BUNDLED_WS))
            result = await db.execute(query)
            agents = result.scalars().all()
            return json.dumps([{"id": str(a.id), "name": a.name, "mode": a.mode} for a in agents])

    @mcp.resource("knowledge://list")
    async def resource_knowledge_list() -> str:
        """Knowledge base catalog visible to the caller."""
        async with async_session_factory() as db:
            ctx = _auth()
            result = await db.execute(
                select(KnowledgeBaseModel)
                .where(~KnowledgeBaseModel.deleted, _tenant_filter(KnowledgeBaseModel.workspace_id, ctx))
                .limit(100)
            )
            kbs = result.scalars().all()
            return json.dumps(
                [{"id": str(kb.id), "name": kb.name, "collection_name": kb.collection_name} for kb in kbs]
            )

    @mcp.resource("tool://list")
    async def resource_tool_list() -> str:
        """Tool catalog visible to the caller."""
        async with async_session_factory() as db:
            ctx = _auth()
            result = await db.execute(
                select(ToolModel).where(~ToolModel.deleted, _tenant_filter(ToolModel.workspace_id, ctx)).limit(100)
            )
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
