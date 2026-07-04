"""SCIM Group endpoints — CRUD for /scim/v2/Groups."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.scim.auth import verify_scim_token
from hecate.scim.models import make_error, make_list_response, to_scim_group

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scim/v2/Groups", tags=["scim-groups"])

SCIM_CONTENT_TYPE = "application/scim+json"

# In-memory group store (replace with DB model in production)
_groups: dict[str, dict] = {}


def _scim_response(content: dict, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content=content, status_code=status_code, media_type=SCIM_CONTENT_TYPE)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_group(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Create a new SCIM group."""
    body = await request.json()
    display_name = body.get("displayName", "")
    if not display_name:
        return _scim_response(make_error(400, "displayName is required", "invalidSyntax"), 400)

    group_id = str(uuid.uuid4())
    members = body.get("members", [])

    group = {
        "id": group_id,
        "displayName": display_name,
        "members": members,
        "created": datetime.now(UTC).isoformat(),
    }
    _groups[group_id] = group

    location = str(request.url) + f"/{group_id}"
    return _scim_response(to_scim_group(group_id, display_name, members, location), 201)


@router.get("")
async def list_groups(
    request: Request,
    startIndex: int = Query(1, ge=1),  # noqa: N803
    count: int = Query(100, ge=0, le=1000),
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """List groups."""
    all_groups = list(_groups.values())
    total = len(all_groups)

    offset = startIndex - 1
    page = all_groups[offset : offset + count]

    resources = [
        to_scim_group(g["id"], g["displayName"], g.get("members", []), str(request.url) + f"/{g['id']}") for g in page
    ]
    return _scim_response(make_list_response(resources, total, startIndex, count))


@router.get("/{group_id}")
async def get_group(
    group_id: str,
    request: Request,
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Get a group by ID."""
    group = _groups.get(group_id)
    if group is None:
        return _scim_response(make_error(404, f"Group {group_id} not found"), 404)

    return _scim_response(to_scim_group(group_id, group["displayName"], group.get("members", []), str(request.url)))


@router.patch("/{group_id}")
async def patch_group(
    group_id: str,
    request: Request,
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Update group membership."""
    group = _groups.get(group_id)
    if group is None:
        return _scim_response(make_error(404, f"Group {group_id} not found"), 404)

    body = await request.json()
    operations = body.get("Operations", [])

    for op in operations:
        op_type = op.get("op", "").lower()
        value = op.get("value", {})

        if op_type == "add" and isinstance(value, list):
            for member in value:
                if member not in group.get("members", []):
                    group.setdefault("members", []).append(member)
        elif op_type == "remove" and isinstance(value, list):
            group["members"] = [m for m in group.get("members", []) if m not in value]

    return _scim_response(to_scim_group(group_id, group["displayName"], group.get("members", []), str(request.url)))


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Delete a group."""
    if group_id not in _groups:
        return _scim_response(make_error(404, f"Group {group_id} not found"), 404)

    del _groups[group_id]
    return _scim_response({}, 204)
