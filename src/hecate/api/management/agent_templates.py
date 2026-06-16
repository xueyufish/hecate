"""Agent template API endpoints.

Provides endpoints for listing and instantiating agent templates.
Templates are loaded from JSON files in src/hecate/data/agent_templates/.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.deps import get_db, verify_api_key
from hecate.models.knowledge import KnowledgeBaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "data" / "agent_templates"

_template_cache: list[dict[str, Any]] | None = None


def _load_templates() -> list[dict[str, Any]]:
    """Load all templates from JSON files with caching."""
    global _template_cache
    if _template_cache is not None:
        return _template_cache

    templates = []
    if TEMPLATES_DIR.exists():
        for file in TEMPLATES_DIR.glob("*.json"):
            try:
                data = json.loads(file.read_text())
                templates.append(data)
            except Exception as e:
                logger.warning(f"Failed to load template {file}: {e}")

    _template_cache = templates
    return templates


def _get_template(template_id: str) -> dict[str, Any] | None:
    """Get a template by ID."""
    for template in _load_templates():
        if template.get("id") == template_id:
            return template
    return None


async def _validate_kb_ids(db: AsyncSession, kb_ids: list[str]) -> list[str]:
    """Validate KB IDs and return list of invalid ones."""
    if not kb_ids:
        return []

    kb_uuids = []
    for kid in kb_ids:
        try:
            kb_uuids.append(uuid.UUID(kid))
        except ValueError:
            kb_uuids.append(None)

    valid_uuids = [u for u in kb_uuids if u is not None]
    if not valid_uuids:
        return kb_ids

    stmt = select(KnowledgeBaseModel.id).where(
        KnowledgeBaseModel.id.in_(valid_uuids),
        ~KnowledgeBaseModel.deleted,
    )
    result = await db.execute(stmt)
    found_ids = {str(row[0]) for row in result.all()}

    return [kid for kid in kb_ids if kid not in found_ids]


@router.get("/agent-templates")
async def list_templates() -> dict:
    """List all agent templates with metadata.

    Returns:
        dict: {"items": [...]} with template metadata.
    """
    templates = _load_templates()
    return {
        "items": [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t["description"],
                "category": t["category"],
                "preview": t.get("preview", {}),
            }
            for t in templates
        ]
    }


@router.get("/agent-templates/{template_id}")
async def get_template(template_id: str) -> dict:
    """Get a template by ID.

    Args:
        template_id: The template ID.

    Returns:
        dict: Full template data.

    Raises:
        HTTPException: 404 if template not found.
    """
    template = _get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Template not found", "details": None}},
        )
    return template


@router.post("/agent-templates/{template_id}/instantiate")
async def instantiate_template(
    template_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Instantiate a template — validate and return config for agent creation.

    Args:
        template_id: The template ID.
        db: The async database session.
        api_key: The validated API key.

    Returns:
        dict: Template config ready for POST /api/agents.

    Raises:
        HTTPException: 404 if template not found.
        HTTPException: 422 if template has invalid KB IDs.
    """
    template = _get_template(template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "Template not found", "details": None}},
        )

    config = template.get("config", {})
    kb_ids = config.get("knowledge_base_ids", [])
    invalid_ids = await _validate_kb_ids(db, kb_ids)

    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_KB_IDS",
                    "message": f"Template references invalid knowledge bases: {', '.join(invalid_ids)}",
                    "details": {"invalid_ids": invalid_ids},
                }
            },
        )

    return config
