"""Agent version lifecycle API endpoints (1.3.20).

Provides the versioning surface for agents, mirroring the workflow
version routes:

- ``GET  /api/agents/{id}/versions`` — list versions (newest first)
- ``POST /api/agents/{id}/versions/commit`` — freeze the draft into a new version
- ``GET  /api/agents/{id}/versions/{version}`` — version detail (with snapshot)
- ``PATCH /api/agents/{id}/versions/{version}`` — rename / edit release notes
- ``DELETE /api/agents/{id}/versions/{version}`` — delete (published versions refused)
- ``GET  /api/agents/{id}/versions/{version}/drift`` — reference drift report
- ``POST /api/agents/{id}/publish/{version}`` — move the published pointer (gated)
- ``GET  /api/agents/{id}/published`` — currently published version
- ``POST /api/agents/{id}/rollback/{version}`` — restore as a new version
- ``GET  /api/agents/{id}/diff`` — compare two versions
- ``GET  /api/agents/{id}/version-status`` — badge state (dirty flag, pointers)

The gate itself is configured through the regular agent update endpoint
(``evaluation_gate`` field, same shape as workflows).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.agent_version import (
    AgentVersionCommitSchema,
    AgentVersionDiffSchema,
    AgentVersionStatusSchema,
    AgentVersionUpdateSchema,
)
from hecate.studio.agents.versioning import (
    AgentPublishEvaluationGateBlockedError,
    AgentVersionService,
)

router = APIRouter()


def _not_found(exc: ValueError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": str(exc), "details": None}},
    )


@router.get("/agents/{agent_id}/versions")
async def list_agent_versions(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """List an agent's versions, newest first."""
    try:
        items = await AgentVersionService(db).list_versions(agent_id)
    except ValueError as e:
        raise _not_found(e) from e
    return {"items": items, "total": len(items)}


@router.post("/agents/{agent_id}/versions/commit")
async def commit_agent_version(
    agent_id: uuid.UUID,
    body: AgentVersionCommitSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Freeze the agent's current draft into a new immutable version."""
    try:
        return await AgentVersionService(db).commit_version(
            agent_id,
            name=body.name,
            change_summary=body.change_summary,
            actor_user_id=ctx.user_id,
        )
    except ValueError as e:
        raise _not_found(e) from e


@router.get("/agents/{agent_id}/versions/{version}")
async def get_agent_version(
    agent_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Fetch one version including its full snapshot."""
    try:
        return await AgentVersionService(db).get_version(agent_id, version, include_snapshot=True)
    except ValueError as e:
        raise _not_found(e) from e


@router.patch("/agents/{agent_id}/versions/{version}")
async def update_agent_version(
    agent_id: uuid.UUID,
    version: int,
    body: AgentVersionUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Rename a version / edit its release notes (metadata only)."""
    try:
        return await AgentVersionService(db).update_version(
            agent_id,
            version,
            name=body.name,
            change_summary=body.change_summary,
        )
    except ValueError as e:
        raise _not_found(e) from e


@router.delete("/agents/{agent_id}/versions/{version}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent_version(
    agent_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Delete a version; published and channel-pinned versions are refused."""
    service = AgentVersionService(db)
    try:
        await service.delete_version(agent_id, version)
    except ValueError as e:
        message = str(e)
        if "published" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "VERSION_PUBLISHED", "message": message, "details": None}},
            ) from e
        if "pinned by channel" in message:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "VERSION_PINNED", "message": message, "details": None}},
            ) from e
        raise _not_found(e) from e


@router.get("/agents/{agent_id}/versions/{version}/drift")
async def get_agent_version_drift(
    agent_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Report manifest entries whose live content differs from the snapshot."""
    try:
        return await AgentVersionService(db).version_drift(agent_id, version)
    except ValueError as e:
        raise _not_found(e) from e


@router.post("/agents/{agent_id}/publish/{version}")
async def publish_agent_version(
    agent_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    body: dict | None = None,
) -> dict:
    """Publish a version (move the ``published_version`` pointer).

    The optional JSON body ``{"force": true}`` overrides a require-mode
    evaluation gate; the bypass is recorded in the audit log. A rejected
    publish returns 409 ``EVALUATION_GATE_BLOCKED`` with the gate
    verdict.
    """
    service = AgentVersionService(db)
    force = bool((body or {}).get("force") or False)
    try:
        return await service.publish_version(agent_id, version, force=force, actor_user_id=ctx.user_id)
    except AgentPublishEvaluationGateBlockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "EVALUATION_GATE_BLOCKED",
                    "message": str(exc),
                    "details": {"gate": asdict(exc.gate_result), "evaluation_report": exc.report},
                }
            },
        ) from exc
    except ValueError as e:
        raise _not_found(e) from e


@router.get("/agents/{agent_id}/published")
async def get_published_agent_version(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Fetch the currently published version."""
    service = AgentVersionService(db)
    try:
        status_data = await service.get_version_status(agent_id)
        published = status_data["published_version"]
        if published is None:
            raise ValueError(f"Agent {agent_id} has no published version")
        return await service.get_version(agent_id, published)
    except ValueError as e:
        message = str(e)
        code = "NO_PUBLISHED_VERSION" if "no published version" in message else "NOT_FOUND"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": code, "message": message, "details": None}},
        ) from e


@router.post("/agents/{agent_id}/rollback/{version}")
async def rollback_agent_version(
    agent_id: uuid.UUID,
    version: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Restore a target version's content as a new version (pointer untouched)."""
    try:
        return await AgentVersionService(db).rollback_to_version(agent_id, version, actor_user_id=ctx.user_id)
    except ValueError as e:
        raise _not_found(e) from e


@router.get("/agents/{agent_id}/diff", response_model=AgentVersionDiffSchema)
async def diff_agent_versions(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    v1: Annotated[int, Query(ge=1)],
    v2: Annotated[int, Query(ge=1)],
) -> dict:
    """Compare two versions' snapshots (own config + reference changes)."""
    try:
        return await AgentVersionService(db).diff_versions(agent_id, v1, v2)
    except ValueError as e:
        raise _not_found(e) from e


@router.get("/agents/{agent_id}/version-status", response_model=AgentVersionStatusSchema)
async def get_agent_version_status(
    agent_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Badge state: latest version, published pointer, uncommitted-changes flag."""
    try:
        return await AgentVersionService(db).get_version_status(agent_id)
    except ValueError as e:
        raise _not_found(e) from e
