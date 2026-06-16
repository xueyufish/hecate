"""Orchestration template API endpoints.

Provides read-only access to pre-built multi-agent orchestration templates:
- ``GET /api/orchestration-templates`` — List all templates with metadata
- ``GET /api/orchestration-templates/{template_id}`` — Get full Graph DSL JSON
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hecate.core.deps import verify_api_key
from hecate.engine.patterns import infer_pattern

logger = logging.getLogger(__name__)

router = APIRouter()

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "data" / "orchestration_templates"

_template_cache: dict[str, dict] | None = None


def _load_templates() -> dict[str, dict]:
    """Load all template JSON files from the templates directory.

    Templates are loaded once and cached for the process lifetime.

    Returns:
        Dict mapping template_id to template data.
    """
    global _template_cache
    if _template_cache is not None:
        return _template_cache

    templates: dict[str, dict] = {}
    if not TEMPLATES_DIR.exists():
        logger.warning("Templates directory not found: %s", TEMPLATES_DIR)
        _template_cache = templates
        return templates

    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            template_id = path.stem
            templates[template_id] = data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load template %s: %s", path, e)

    _template_cache = templates
    return templates


@router.get("/orchestration-templates")
async def list_templates(
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """List all available orchestration templates.

    Returns:
        Dict with ``items`` list containing template metadata.
    """
    templates = _load_templates()
    items = []
    for template_id, data in templates.items():
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])
        agent_nodes = [nid for nid, n in nodes.items() if n.get("type") == "agent"]

        pattern_type: str | None = None
        try:
            from hecate.engine.graph_dsl import parse_graph

            # Strip non-DSL fields before parsing to avoid schema validation errors
            dsl_data = {
                "version": data.get("version", "1.0"),
                "name": data.get("name", ""),
                "state": data.get("state", {}),
                "nodes": data.get("nodes", {}),
                "edges": data.get("edges", []),
            }
            if "entry" in data:
                dsl_data["entry"] = data["entry"]

            graph_config = parse_graph(dsl_data)
            detected = infer_pattern(graph_config)
            if detected is not None:
                pattern_type = detected.value
        except Exception:
            logger.debug("Could not infer pattern for template %s", template_id)

        items.append(
            {
                "id": template_id,
                "name": data.get("name", template_id),
                "description": data.get("description", ""),
                "category": data.get("category", "general"),
                "pattern_type": pattern_type,
                "preview": {
                    "total_nodes": len(nodes),
                    "agent_nodes": len(agent_nodes),
                    "total_edges": len(edges),
                },
            },
        )
    return {"items": items}


@router.get("/orchestration-templates/{template_id}")
async def get_template(
    template_id: str,
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Get the full Graph DSL JSON for a specific template.

    Args:
        template_id: The template identifier (filename without extension).

    Returns:
        The complete Graph DSL JSON.

    Raises:
        HTTPException: 404 if template not found.
    """
    templates = _load_templates()
    if template_id not in templates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": f"Template '{template_id}' not found", "details": None}},
        )
    return templates[template_id]
