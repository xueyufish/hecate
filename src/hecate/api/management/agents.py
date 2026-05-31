"""Agent management API endpoints.

Provides CRUD operations for agents:
- ``POST /api/agents`` — Create a new agent
- ``GET /api/agents`` — List agents (paginated)
- ``GET /api/agents/{id}`` — Get agent by ID
- ``PUT /api/agents/{id}`` — Update agent
- ``DELETE /api/agents/{id}`` — Soft delete agent
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.agent import (
    AgentCreateSchema,
    AgentModel,
    AgentReadSchema,
    AgentUpdateSchema,
)
from hecate.models.knowledge import KnowledgeBaseModel
from hecate.models.model_provider import ModelProviderModel, ModelRegistryModel

router = APIRouter()


async def validate_knowledge_base_ids(
    db: AsyncSession,
    kb_ids: list[str],
) -> None:
    """Validate that all KB IDs reference existing, non-deleted knowledge bases.

    Args:
        db: The async database session.
        kb_ids: List of knowledge base UUID strings to validate.

    Raises:
        HTTPException: 400 if any KB ID is invalid or references a deleted KB.
    """
    if not kb_ids:
        return

    kb_uuids = []
    for kid in kb_ids:
        try:
            kb_uuids.append(uuid.UUID(kid))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "INVALID_KB_ID",
                        "message": f"Invalid knowledge_base_id format: {kid}",
                        "details": None,
                    }
                },
            ) from None

    stmt = select(KnowledgeBaseModel.id).where(
        KnowledgeBaseModel.id.in_(kb_uuids),
        KnowledgeBaseModel.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    found_ids = {str(row[0]) for row in result.all()}

    invalid_ids = [kid for kid in kb_ids if kid not in found_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_KB_IDS",
                    "message": f"Knowledge bases not found: {', '.join(invalid_ids)}",
                    "details": {"invalid_ids": invalid_ids},
                }
            },
        )


async def _check_models_availability(
    db: AsyncSession,
    model_names: set[str],
) -> dict[str, bool]:
    """Check which models are available (enabled registry entry + active provider).

    Args:
        db: The async database session.
        model_names: Set of model identifiers to check.

    Returns:
        Dict mapping model_id → availability (True if available).
    """
    if not model_names:
        return {}
    stmt = (
        select(ModelRegistryModel.model_id)
        .join(ModelProviderModel, ModelProviderModel.id == ModelRegistryModel.provider_id)
        .where(
            ModelRegistryModel.model_id.in_(model_names),
            ModelRegistryModel.deleted_at.is_(None),
            ModelRegistryModel.is_enabled.is_(True),
            ModelProviderModel.deleted_at.is_(None),
            ModelProviderModel.is_enabled.is_(True),
            ModelProviderModel.status == "active",
        )
    )
    result = await db.execute(stmt)
    available_models = {row[0] for row in result.all()}
    return {name: (name in available_models) for name in model_names}


@router.post("/agents", status_code=status.HTTP_201_CREATED)
async def create_agent(
    data: AgentCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Create a new agent.

    Args:
        data: The agent creation data.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The created agent data.
    """
    await validate_knowledge_base_ids(db, data.knowledge_base_ids)

    agent = AgentModel(
        name=data.name,
        persona=data.persona,
        model_config_db=data.llm_config,
        mode=data.mode,
        workflow_id=data.workflow_id,
        tools=data.tools,
        skills=data.skills,
        knowledge_base_ids=data.knowledge_base_ids,
        risk_level=data.risk_level,
    )
    db.add(agent)
    await db.flush()
    await db.refresh(agent)
    return AgentReadSchema.model_validate(agent).model_dump(by_alias=True)


@router.get("/agents")
async def list_agents(
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List agents with pagination.

    Args:
        db: The async database session.
        api_key: The validated API key.
        page: Page number (1-indexed).
        page_size: Number of items per page.

    Returns:
        dict: ``{"items": [...], "total": int}`` with agent list and total count.
    """
    # Count total
    count_stmt = select(func.count()).select_from(AgentModel).where(AgentModel.deleted_at.is_(None))
    total = (await db.execute(count_stmt)).scalar_one()

    # Fetch page
    offset = (page - 1) * page_size
    stmt = (
        select(AgentModel)
        .where(AgentModel.deleted_at.is_(None))
        .order_by(AgentModel.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    agents = result.scalars().all()

    model_names: set[str] = set()
    for a in agents:
        model_name = a.model_config_db.get("model") if isinstance(a.model_config_db, dict) else None
        if model_name:
            model_names.add(model_name)
    availability = await _check_models_availability(db, model_names)

    items: list[dict[str, Any]] = []
    for a in agents:
        schema = AgentReadSchema.model_validate(a)
        model_name = a.model_config_db.get("model") if isinstance(a.model_config_db, dict) else None
        schema.model_available = availability.get(model_name, None) if model_name else None
        items.append(schema.model_dump(by_alias=True))

    return {
        "items": items,
        "total": total,
    }


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get an agent by ID.

    Args:
        agent_id: The UUID of the agent to retrieve.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The agent data.

    Raises:
        HTTPException: 404 if agent not found or deleted.
    """
    result = await db.execute(
        select(AgentModel).where(
            AgentModel.id == agent_id,
            AgentModel.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Agent not found", "details": None}},
        )

    schema = AgentReadSchema.model_validate(agent)
    model_name = agent.model_config_db.get("model") if isinstance(agent.model_config_db, dict) else None
    if model_name:
        availability = await _check_models_availability(db, {model_name})
        schema.model_available = availability.get(model_name, False)

    return schema.model_dump(by_alias=True)


@router.put("/agents/{agent_id}")
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Update an existing agent.

    Args:
        agent_id: The UUID of the agent to update.
        data: The update data (all fields optional).
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: The updated agent data.

    Raises:
        HTTPException: 404 if agent not found or deleted.
    """
    result = await db.execute(
        select(AgentModel).where(
            AgentModel.id == agent_id,
            AgentModel.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Agent not found", "details": None}},
        )

    update_data = data.model_dump(exclude_unset=True, by_alias=True)
    if "knowledge_base_ids" in update_data:
        await validate_knowledge_base_ids(db, update_data["knowledge_base_ids"])
    for field, value in update_data.items():
        if field == "model_config":
            agent.model_config_db = value
        else:
            setattr(agent, field, value)

    await db.flush()
    await db.refresh(agent)
    return AgentReadSchema.model_validate(agent).model_dump(by_alias=True)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> None:
    """Soft delete an agent.

    Args:
        agent_id: The UUID of the agent to delete.
        db: The async database session.
        api_key: The validated API key.

    Raises:
        HTTPException: 404 if agent not found or already deleted.
    """
    result = await db.execute(
        select(AgentModel).where(
            AgentModel.id == agent_id,
            AgentModel.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Agent not found", "details": None}},
        )

    agent.deleted_at = datetime.now(UTC)
    await db.flush()
