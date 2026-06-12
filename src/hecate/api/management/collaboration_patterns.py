"""Collaboration pattern API endpoints.

Provides access to the 6 collaboration patterns and graph generation:
- ``GET /api/collaboration-patterns`` — List all patterns with metadata
- ``POST /api/collaboration-patterns/{pattern_id}/generate`` — Generate Graph DSL JSON
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hecate.core.deps import verify_api_key
from hecate.engine.patterns import (
    PATTERN_DEFINITIONS,
    CollaborationPattern,
    build_graph_from_pattern,
)
from hecate.engine.types import GraphConfig

router = APIRouter()


def _graph_to_dsl_json(config: GraphConfig) -> dict:
    """Serialize a GraphConfig to Graph DSL JSON dict."""
    return {
        "version": config.version,
        "name": config.name,
        "state": {
            k: {"type": v.type.value, "default": v.default, "persistent": v.persistent} for k, v in config.state.items()
        },
        "nodes": {k: {"type": v.type.value, "config": v.config} for k, v in config.nodes.items()},
        "edges": [
            {
                "source": e.source,
                "target": e.target if isinstance(e.target, str) else e.target,
                "trigger": e.trigger,
            }
            for e in config.edges
        ],
        "entry": config.entry,
    }


@router.get("/collaboration-patterns")
async def list_patterns(
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """List all available collaboration patterns.

    Returns:
        Dict with ``items`` list containing pattern definitions.
    """
    return {"items": PATTERN_DEFINITIONS}


@router.post("/collaboration-patterns/{pattern_id}/generate")
async def generate_pattern(
    pattern_id: str,
    config: dict,
    api_key: Annotated[str, Depends(verify_api_key)],
) -> dict:
    """Generate a Graph DSL JSON from a pattern selection.

    Args:
        pattern_id: The collaboration pattern identifier.
        config: Pattern-specific configuration parameters.
        api_key: API key for authentication.

    Returns:
        Complete Graph DSL JSON ready for canvas loading.

    Raises:
        HTTPException: 422 if pattern_id is invalid or config is missing required fields.
    """
    try:
        pattern = CollaborationPattern(pattern_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_PATTERN",
                    "message": f"Unknown collaboration pattern: '{pattern_id}'",
                    "details": {
                        "valid_patterns": [p.value for p in CollaborationPattern],
                    },
                },
            },
        ) from None

    try:
        graph = build_graph_from_pattern(pattern, config)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "INVALID_CONFIG",
                    "message": str(e),
                    "details": None,
                },
            },
        ) from e

    return _graph_to_dsl_json(graph)
