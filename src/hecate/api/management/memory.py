"""Memory management API endpoints.

Provides endpoints for L1 working memory blocks and L3 user memories:

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
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.memory import (
    MemoryBlockCreateSchema,
    MemoryBlockUpdateSchema,
    MemoryCreateSchema,
)
from hecate.services.memory.user_memory import UserMemoryService
from hecate.services.memory.working_memory import WorkingMemoryService

router = APIRouter()


# --- Memory Block Endpoints ---


@router.post("/agents/{agent_id}/memory-blocks", status_code=status.HTTP_201_CREATED)
async def create_memory_block(
    agent_id: uuid.UUID,
    data: MemoryBlockCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Create a new memory block for an agent.

    Args:
        agent_id: The agent to create the block for.
        data: Block creation data.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The created block data.
    """
    service = WorkingMemoryService(db)
    try:
        result = await service.create_block(agent_id, data)
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
    api_key: Annotated[str, Depends(verify_api_key)],
) -> list[dict]:
    """List all memory blocks for an agent.

    Args:
        agent_id: The agent to list blocks for.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        list: List of memory block dicts ordered by position.
    """
    service = WorkingMemoryService(db)
    blocks = await service.list_blocks(agent_id)
    return [b.model_dump() for b in blocks]


@router.get("/agents/{agent_id}/memory-blocks/{block_id}")
async def get_memory_block(
    agent_id: uuid.UUID,
    block_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get a memory block by ID.

    Args:
        agent_id: The agent that owns the block.
        block_id: The block ID.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The block data.

    Raises:
        HTTPException: 404 if block not found.
    """
    service = WorkingMemoryService(db)
    try:
        result = await service.get_block(agent_id, block_id)
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
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Update a memory block.

    Args:
        agent_id: The agent that owns the block.
        block_id: The block ID.
        data: Update data.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The updated block data.

    Raises:
        HTTPException: 404 if block not found.
    """
    service = WorkingMemoryService(db)
    try:
        result = await service.update_block(agent_id, block_id, data)
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
    api_key: Annotated[str, Depends(verify_api_key)],
) -> None:
    """Delete a memory block.

    Args:
        agent_id: The agent that owns the block.
        block_id: The block ID.
        db: The async database session.
        api_key: The validated API key.

    Raises:
        HTTPException: 404 if block not found.
    """
    service = WorkingMemoryService(db)
    try:
        await service.delete_block(agent_id, block_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e


# --- User Memory Endpoints ---


@router.post("/memory", status_code=status.HTTP_201_CREATED)
async def create_memory(
    data: MemoryCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Create a new user memory.

    Args:
        data: Memory creation data.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The created memory data.
    """
    service = UserMemoryService(db)
    result = await service.store_memory(data)
    return result.model_dump()


@router.get("/memory")
async def list_memories(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    memory_type: str | None = Query(None, description="Filter by memory type"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="Minimum importance"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
) -> list[dict]:
    """List/search user memories.

    Args:
        db: The async database session.
        api_key: The validated API key.
        memory_type: Optional type filter.
        min_importance: Minimum importance threshold.
        limit: Maximum results.

    Returns:
        list: List of memory dicts.
    """
    service = UserMemoryService(db)
    memories = await service.list_memories(
        memory_type=memory_type,
        min_importance=min_importance,
        limit=limit,
    )
    return [m.model_dump() for m in memories]


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> None:
    """Delete a user memory.

    Args:
        memory_id: The memory to delete.
        db: The async database session.
        api_key: The validated API key.

    Raises:
        HTTPException: 404 if memory not found.
    """
    service = UserMemoryService(db)
    try:
        await service.delete_memory(memory_id)
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
    api_key: Annotated[str, Depends(verify_api_key)],
    q: str = Query(..., min_length=1, description="Search query"),
    top_k: int = Query(5, ge=1, le=20, description="Max results"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="Minimum importance"),
) -> dict:
    """Semantic search for user memories.

    Args:
        user_id: The user to search memories for.
        q: Search query string.
        db: The async database session.
        api_key: The validated API key.
        top_k: Maximum number of results.
        min_importance: Minimum importance threshold.

    Returns:
        dict with items list of matching memories.
    """
    service = UserMemoryService(db)
    memories = await service.retrieve_memories(
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
    api_key: Annotated[str, Depends(verify_api_key)],
    memory_type: str | None = Query(None, description="Filter by memory type"),
    min_importance: float = Query(0.0, ge=0.0, le=1.0, description="Minimum importance"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict:
    """List memories for a specific user.

    Args:
        user_id: The user to list memories for.
        db: The async database session.
        api_key: The validated API key.
        memory_type: Optional type filter.
        min_importance: Minimum importance threshold.
        limit: Maximum results.
        offset: Pagination offset.

    Returns:
        dict with items list and total count.
    """
    service = UserMemoryService(db)
    memories = await service.list_memories(
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
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get compression status for a session.

    Returns metadata about compression operations applied to this session.

    Args:
        session_id: The session to check.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict with compression metadata.
    """
    return {
        "session_id": str(session_id),
        "compression_applied": False,
        "levels_available": ["snip", "microcompact", "autocompact"],
        "message": "Compression status tracking will be implemented with session persistence.",
    }
