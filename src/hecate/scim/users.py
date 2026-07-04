"""SCIM User endpoints — CRUD, filtering, pagination for /scim/v2/Users."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.models.user import UserModel
from hecate.scim.auth import verify_scim_token
from hecate.scim.filter_parser import apply_scim_filter
from hecate.scim.models import from_scim_user, make_error, make_list_response, to_scim_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scim/v2/Users", tags=["scim-users"])

SCIM_CONTENT_TYPE = "application/scim+json"


def _scim_response(content: dict, status_code: int = 200) -> JSONResponse:
    """Create a SCIM JSON response."""
    return JSONResponse(content=content, status_code=status_code, media_type=SCIM_CONTENT_TYPE)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Create a new SCIM user."""
    body = await request.json()

    email = body.get("userName", "")
    if not email:
        return _scim_response(make_error(400, "userName is required", "invalidSyntax"), 400)

    # Check for duplicate
    existing = await db.execute(select(UserModel).where(UserModel.email == email))
    if existing.scalar_one_or_none() is not None:
        return _scim_response(make_error(409, f"User with userName '{email}' already exists", "uniqueness"), 409)

    user_data = from_scim_user(body)
    user = UserModel(
        email=user_data["email"],
        hashed_password=secrets.token_urlsafe(32),
        external_id=user_data.get("external_id"),
        display_name=user_data.get("display_name"),
        given_name=user_data.get("given_name"),
        family_name=user_data.get("family_name"),
        active=user_data.get("active", True),
        sso_id=email,
    )
    db.add(user)
    await db.flush()

    location = str(request.url) + f"/{user.id}"
    return _scim_response(to_scim_user(user, location), 201)


@router.get("")
async def list_users(
    request: Request,
    startIndex: int = Query(1, ge=1),  # noqa: N803
    count: int = Query(100, ge=0, le=1000),
    filter: str | None = Query(None),
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """List users with filtering and pagination."""
    stmt = select(UserModel).where(UserModel.deleted.is_(False))
    stmt = apply_scim_filter(stmt, filter)

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Paginate
    offset = startIndex - 1  # SCIM startIndex is 1-based
    stmt = stmt.offset(offset).limit(count).order_by(UserModel.created_at.desc())
    result = await db.execute(stmt)
    users = result.scalars().all()

    resources = [to_scim_user(u, str(request.url) + f"/{u.id}") for u in users]
    return _scim_response(make_list_response(resources, total, startIndex, count))


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Get a user by ID."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)

    result = await db.execute(select(UserModel).where(UserModel.id == uid, UserModel.deleted.is_(False)))
    user = result.scalar_one_or_none()
    if user is None:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)

    return _scim_response(to_scim_user(user, str(request.url)))


@router.put("/{user_id}")
async def replace_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Replace a user (full replacement)."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)
    result = await db.execute(select(UserModel).where(UserModel.id == uid, UserModel.deleted.is_(False)))
    user = result.scalar_one_or_none()
    if user is None:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)

    body = await request.json()
    user_data = from_scim_user(body)

    for key, value in user_data.items():
        if hasattr(user, key) and value is not None:
            setattr(user, key, value)

    await db.flush()
    return _scim_response(to_scim_user(user, str(request.url)))


@router.patch("/{user_id}")
async def patch_user(
    user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Partially update a user."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)
    result = await db.execute(select(UserModel).where(UserModel.id == uid, UserModel.deleted.is_(False)))
    user = result.scalar_one_or_none()
    if user is None:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)

    body = await request.json()
    operations = body.get("Operations", [])

    for op in operations:
        op_type = op.get("op", "").lower()
        path = op.get("path", "")
        value = op.get("value")

        if op_type == "replace":
            if path == "active":
                user.active = bool(value)
            elif path == "displayName":
                user.display_name = str(value)
            elif path == "name.givenName":
                user.given_name = str(value)
            elif path == "name.familyName":
                user.family_name = str(value)
            elif not path:
                # Replace without path = replace entire resource
                user_data = from_scim_user(body if isinstance(body, dict) else {})
                for key, val in user_data.items():
                    if hasattr(user, key) and val is not None:
                        setattr(user, key, val)

    await db.flush()
    return _scim_response(to_scim_user(user, str(request.url)))


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    token: str = Depends(verify_scim_token),  # noqa: B008
) -> JSONResponse:
    """Delete (deactivate) a user."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)
    result = await db.execute(select(UserModel).where(UserModel.id == uid, UserModel.deleted.is_(False)))
    user = result.scalar_one_or_none()
    if user is None:
        return _scim_response(make_error(404, f"User {user_id} not found"), 404)

    user.active = False
    user.deleted = True
    user.deleted_at = datetime.now(UTC)
    await db.flush()

    return _scim_response({}, 204)
