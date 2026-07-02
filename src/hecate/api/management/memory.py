"""Memory management API endpoints.

Provides endpoints for L1 working memory blocks, L3 user memories,
and L4 knowledge memories:

**Memory Blocks (per agent):**
- ``POST /api/agents/{id}/memory-blocks`` — Create memory block
- ``GET /api/agents/{id}/memory-blocks`` — List memory blocks
- ``GET /api/agents/{id}/memory-blocks/{block_id}`` — Get memory block
- ``PUT /api/agents/{id}/memory-blocks/{block_id}`` — Update memory block
- ``DELETE /api/agents/{id}/memory-blocks/{block_id}`` — Delete memory block

**User Memories:**
- ``POST /api/memory`` — Create memory
- ``GET /api/memory`` — List/search memories
- ``DELETE /api/memory/{id}`` — Delete memory

**Knowledge Memories (per agent):**
- ``POST /api/agents/{id}/knowledge`` — Create knowledge memory
- ``GET /api/agents/{id}/knowledge`` — List knowledge memories
- ``GET /api/agents/{id}/knowledge/{memory_id}`` — Get knowledge memory
- ``DELETE /api/agents/{id}/knowledge/{memory_id}`` — Delete knowledge memory
- ``POST /api/agents/{id}/knowledge/search`` — Search knowledge memories
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.agent import AgentModel
from hecate.models.memory import (
    KnowledgeMemoryCreateSchema,
    KnowledgeMemorySearchSchema,
    MemoryBlockCreateSchema,
    MemoryBlockUpdateSchema,
    MemoryCreateSchema,
)
from hecate.services.memory.knowledge_memory import KnowledgeMemoryService
from hecate.services.memory.user_memory import UserMemoryService
from hecate.services.memory.working_memory import WorkingMemoryService

router = APIRouter()


async def _get_agent(db: AsyncSession, agent_id: uuid.UUID) -> AgentModel:
    """Load an agent or raise 404.

    Args:
        db: Database session.
        agent_id: Agent ID.

    Returns:
        The agent model.

    Raises:
        HTTPException: 404 if agent not found.
    """
    stmt = select(AgentModel).where(AgentModel.id == agent_id, ~AgentModel.deleted)
    result = await db.execute(stmt)
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": f"Agent {agent_id} not found", "details": None}},
        )
    return agent


# --- Memory Block Endpoints (L1) ---


@router.post("/agents/{agent_id}/memory-blocks", status_code=status.HTTP_201_CREATED)
async def create_memory_block(
    agent_id: uuid.UUID,
    data: MemoryBlockCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new memory block for an agent."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = WorkingMemoryService(db)
    try:
        result = await service.create_block(agent_id, workspace_id, data)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": str(e), "details": None}},
        ) from e


@router.get("/agents/{agent_id}/memory-blocks")
async def list_memory_blocks(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> list[dict]:
    """List all memory blocks for an agent."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = WorkingMemoryService(db)
    blocks = await service.list_blocks(agent_id, workspace_id)
    return [b.model_dump() for b in blocks]


@router.get("/agents/{agent_id}/memory-blocks/{block_id}")
async def get_memory_block(
    agent_id: uuid.UUID,
    block_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a memory block by ID."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = WorkingMemoryService(db)
    try:
        result = await service.get_block(agent_id, workspace_id, block_id)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.put("/agents/{agent_id}/memory-blocks/{block_id}")
async def update_memory_block(
    agent_id: uuid.UUID,
    block_id: uuid.UUID,
    data: MemoryBlockUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update a memory block."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = WorkingMemoryService(db)
    try:
        result = await service.update_block(agent_id, workspace_id, block_id, data)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.delete("/agents/{agent_id}/memory-blocks/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory_block(
    agent_id: uuid.UUID,
    block_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a memory block."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = WorkingMemoryService(db)
    try:
        await service.delete_block(agent_id, workspace_id, block_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


# --- User Memory Endpoints (L3) ---


@router.post("/memory", status_code=status.HTTP_201_CREATED)
async def create_memory(
    data: MemoryCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new user memory."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = UserMemoryService(db)
    result = await service.store_memory(workspace_id, data)
    return result.model_dump()


@router.get("/memory")
async def list_memories(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    memory_type: str | None = Query(None, description="Filter by memory type"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="Minimum importance"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
) -> list[dict]:
    """List/search user memories."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = UserMemoryService(db)
    memories = await service.list_memories(
        workspace_id=workspace_id,
        memory_type=memory_type,
        min_importance=min_importance,
        limit=limit,
    )
    return [m.model_dump() for m in memories]


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a user memory."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = UserMemoryService(db)
    try:
        await service.delete_memory(workspace_id, memory_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


# --- User-Scoped Memory Endpoints ---


@router.get("/users/{user_id}/memories/search")
async def search_user_memories(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    q: str = Query(..., min_length=1, description="Search query"),
    top_k: int = Query(5, ge=1, le=20, description="Max results"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="Minimum importance"),
) -> dict:
    """Semantic search for user memories."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = UserMemoryService(db)
    memories = await service.retrieve_memories(
        workspace_id=workspace_id,
        query=q,
        scope={"user_id": str(user_id)},
        top_k=top_k,
        min_importance=min_importance,
    )
    return {
        "items": [m.model_dump() for m in memories],
        "query": q,
        "total": len(memories),
    }


@router.get("/users/{user_id}/memories")
async def list_user_memories(
    user_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    memory_type: str | None = Query(None, description="Filter by memory type"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="Minimum importance"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict:
    """List memories for a specific user."""
    workspace_id = ctx.workspace_id or uuid.UUID(int=0)
    service = UserMemoryService(db)
    memories = await service.list_memories(
        workspace_id=workspace_id,
        scope={"user_id": str(user_id)},
        memory_type=memory_type,
        min_importance=min_importance,
        limit=limit,
    )
    return {
        "items": [m.model_dump() for m in memories],
        "total": len(memories),
        "offset": offset,
        "limit": limit,
    }


# --- Session Compression Endpoint ---


@router.get("/sessions/{session_id}/compression")
async def get_compression_status(
    session_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get compression status for a session."""
    return {
        "session_id": str(session_id),
        "compression_applied": False,
        "levels_available": ["snip", "microcompact", "autocompact"],
        "message": "Compression status tracking will be implemented with session persistence.",
    }


# --- Knowledge Memory Endpoints (L4) ---


@router.post("/agents/{agent_id}/knowledge", status_code=status.HTTP_201_CREATED)
async def create_knowledge_memory(
    agent_id: uuid.UUID,
    data: KnowledgeMemoryCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new knowledge memory for an agent."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = KnowledgeMemoryService(db)
    result = await service.insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content=data.content,
        tags=data.tags,
        importance=data.importance,
        user_id=data.user_id,
        source=data.source,
    )
    return result.model_dump()


@router.get("/agents/{agent_id}/knowledge")
async def list_knowledge_memories(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    tags: str | None = Query(None, description="Comma-separated tag filter"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict:
    """List knowledge memories for an agent."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = KnowledgeMemoryService(db)
    tag_list = tags.split(",") if tags else None
    memories, total = await service.list_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [m.model_dump() for m in memories],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.get("/agents/{agent_id}/knowledge/{memory_id}")
async def get_knowledge_memory(
    agent_id: uuid.UUID,
    memory_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get a specific knowledge memory."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = KnowledgeMemoryService(db)
    try:
        result = await service.get_knowledge(agent_id, workspace_id, memory_id)
        return result.model_dump()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.delete("/agents/{agent_id}/knowledge/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_memory(
    agent_id: uuid.UUID,
    memory_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a knowledge memory."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = KnowledgeMemoryService(db)
    try:
        await service.delete_knowledge(agent_id, workspace_id, memory_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


@router.post("/agents/{agent_id}/knowledge/search")
async def search_knowledge_memories(
    agent_id: uuid.UUID,
    data: KnowledgeMemorySearchSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Search knowledge memories with hybrid search."""
    agent = await _get_agent(db, agent_id)
    workspace_id = agent.workspace_id
    service = KnowledgeMemoryService(db)
    results = await service.search_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        query=data.query,
        top_k=data.top_k,
        tags=data.tags,
        user_id=data.user_id,
        mode=data.mode,
    )
    return {
        "items": [
            {
                "memory": r.memory.model_dump(),
                "score": r.score,
                "dense_score": r.dense_score,
                "sparse_score": r.sparse_score,
            }
            for r in results
        ],
        "query": data.query,
        "total": len(results),
    }
