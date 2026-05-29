"""Authentication API endpoints.

Provides registration, login, token refresh, and current user info.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.database import get_db
from hecate.core.deps import get_current_user_id
from hecate.models.user import (
    LoginSchema,
    RefreshTokenSchema,
    RegisterSchema,
    TokenResponseSchema,
    UserReadSchema,
)
from hecate.services.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
_auth_service = AuthService()


@router.post(
    "/register",
    response_model=UserReadSchema,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserReadSchema:
    """Register a new user with email and password."""
    try:
        user = await _auth_service.register(db, body.email, body.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CONFLICT", "message": "Email already registered", "details": None}},
        ) from None
    return UserReadSchema.model_validate(user)


@router.post("/login", response_model=TokenResponseSchema)
async def login(
    body: LoginSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponseSchema:
    """Authenticate a user and return JWT tokens."""
    try:
        user, access_token, refresh_token = await _auth_service.login(db, body.email, body.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid credentials", "details": None}},
        ) from None
    return TokenResponseSchema(
        access_token=access_token,
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=TokenResponseSchema)
async def refresh_token(
    body: RefreshTokenSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponseSchema:
    """Refresh access and refresh tokens."""
    try:
        access, refresh = await _auth_service.refresh_tokens(db, body.refresh_token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": "Invalid refresh token", "details": None}},
        ) from None
    return TokenResponseSchema(access_token=access, refresh_token=refresh)


@router.get("/me", response_model=UserReadSchema)
async def get_me(
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserReadSchema:
    """Return the authenticated user's profile."""
    user = await _auth_service.get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": "User not found", "details": None}},
        )
    return UserReadSchema.model_validate(user)
