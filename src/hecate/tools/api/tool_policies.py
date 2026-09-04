"""REST API for tool policy rule and agent policy config management."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db
from hecate.models.tool_policy import (
    AgentPolicyConfigCreateSchema,
    AgentPolicyConfigModel,
    AgentPolicyConfigReadSchema,
    ToolPolicyRuleCreateSchema,
    ToolPolicyRuleModel,
    ToolPolicyRuleReadSchema,
)

router = APIRouter(prefix="/api/tool-policies", tags=["tool-policies"])

DEFAULT_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@router.get("/rules", response_model=list[ToolPolicyRuleReadSchema])
async def list_rules(
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: str | None = None,
) -> list[ToolPolicyRuleModel]:
    stmt = select(ToolPolicyRuleModel).where(ToolPolicyRuleModel.deleted_at.is_(None))
    if agent_id:
        stmt = stmt.where(ToolPolicyRuleModel.agent_id == uuid.UUID(agent_id))
    result = await db.execute(stmt.order_by(ToolPolicyRuleModel.priority.desc()))
    return list(result.scalars().all())


@router.post("/rules", response_model=ToolPolicyRuleReadSchema, status_code=201)
async def create_rule(
    rule: ToolPolicyRuleCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ToolPolicyRuleModel:
    model = ToolPolicyRuleModel(
        workspace_id=DEFAULT_WORKSPACE_ID,
        agent_id=uuid.UUID(rule.agent_id) if rule.agent_id else None,
        tool_pattern=rule.tool_pattern,
        action=rule.action,
        priority=rule.priority,
        arg_conditions=rule.arg_conditions,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)
    return model


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    stmt = select(ToolPolicyRuleModel).where(
        ToolPolicyRuleModel.id == uuid.UUID(rule_id),
        ToolPolicyRuleModel.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Policy rule not found")
    await db.delete(rule)


@router.get("/agents/{agent_id}/config", response_model=AgentPolicyConfigReadSchema)
async def get_agent_config(
    agent_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentPolicyConfigModel:
    stmt = select(AgentPolicyConfigModel).where(
        AgentPolicyConfigModel.agent_id == uuid.UUID(agent_id),
        AgentPolicyConfigModel.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Agent policy config not found")
    return config


@router.put("/agents/{agent_id}/config", response_model=AgentPolicyConfigReadSchema)
async def upsert_agent_config(
    agent_id: str,
    config: AgentPolicyConfigCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AgentPolicyConfigModel:
    agent_uuid = uuid.UUID(agent_id)
    stmt = select(AgentPolicyConfigModel).where(
        AgentPolicyConfigModel.agent_id == agent_uuid,
        AgentPolicyConfigModel.deleted_at.is_(None),
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
        existing.mode = config.mode
        existing.tool_allowlist = config.tool_allowlist
        existing.tool_denylist = config.tool_denylist
        await db.flush()
        await db.refresh(existing)
        return existing

    model = AgentPolicyConfigModel(
        workspace_id=DEFAULT_WORKSPACE_ID,
        agent_id=agent_uuid,
        mode=config.mode,
        tool_allowlist=config.tool_allowlist,
        tool_denylist=config.tool_denylist,
    )
    db.add(model)
    await db.flush()
    await db.refresh(model)
    return model
