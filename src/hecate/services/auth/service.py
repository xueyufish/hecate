"""Authentication service handling registration, login, and token management."""

from __future__ import annotations

import logging
from uuid import UUID

from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.user import UserModel
from hecate.models.workspace_member import WorkspaceMemberModel
from hecate.services.auth.password import hash_password, verify_password
from hecate.services.auth.token import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Handles user registration, login, and token operations."""

    async def register(self, db: AsyncSession, email: str, password: str) -> UserModel:
        """Register a new user.

        Args:
            db: Async database session.
            email: User's email address.
            password: Plaintext password (will be hashed).

        Returns:
            The newly created UserModel.

        Raises:
            ValueError: If email is already registered.
        """
        existing = await db.execute(select(UserModel).where(UserModel.email == email))
        if existing.scalar_one_or_none() is not None:
            raise ValueError("Email already registered")

        user = UserModel(
            email=email,
            hashed_password=hash_password(password),
        )
        db.add(user)
        await db.flush()
        logger.info("User registered: %s", email)
        return user

    async def _resolve_workspace_context(
        self, db: AsyncSession, user_id: UUID
    ) -> tuple[UUID | None, UUID | None, str | None]:
        """Resolve the user's first workspace membership.

        Returns:
            Tuple of (org_id, workspace_id, role) or (None, None, None).
        """
        result = await db.execute(
            select(WorkspaceMemberModel)
            .where(WorkspaceMemberModel.user_id == user_id)
            .order_by(WorkspaceMemberModel.created_at)
            .limit(1)
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            return None, None, None
        return membership.workspace_id, membership.workspace_id, membership.role.value

    async def get_user_workspaces(self, db: AsyncSession, user_id: UUID) -> list[dict[str, str]]:
        """Get all workspaces the user has access to.

        Args:
            db: Async database session.
            user_id: The user's UUID.

        Returns:
            List of dicts with workspace info.
        """
        from hecate.models.workspace import WorkspaceModel

        result = await db.execute(
            select(WorkspaceMemberModel, WorkspaceModel)
            .join(WorkspaceModel, WorkspaceMemberModel.workspace_id == WorkspaceModel.id)
            .where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.deleted.is_(False),
                WorkspaceModel.deleted.is_(False),
            )
        )
        rows = result.all()
        return [
            {
                "id": str(ws.id),
                "name": ws.name,
                "slug": ws.slug,
                "org_id": str(ws.org_id),
                "role": member.role.value,
            }
            for member, ws in rows
        ]

    async def login(
        self, db: AsyncSession, email: str, password: str
    ) -> tuple[UserModel, str, str, list[dict[str, str]]]:
        """Authenticate a user and issue tokens.

        Resolves the user's first workspace membership for token context.

        Args:
            db: Async database session.
            email: User's email address.
            password: Plaintext password to verify.

        Returns:
            Tuple of (user, access_token, refresh_token, workspaces).

        Raises:
            ValueError: If credentials are invalid.
        """
        result = await db.execute(select(UserModel).where(UserModel.email == email))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(password, user.hashed_password):
            raise ValueError("Invalid credentials")

        org_id, workspace_id, role = await self._resolve_workspace_context(db, user.id)
        access_token = create_access_token(user.id, org_id, workspace_id, role)
        refresh_token = create_refresh_token(user.id, org_id, workspace_id, role)
        workspaces = await self.get_user_workspaces(db, user.id)

        logger.info("User logged in: %s", email)
        return user, access_token, refresh_token, workspaces

    async def refresh_tokens(self, db: AsyncSession, refresh_token: str) -> tuple[str, str]:
        """Issue new tokens from a valid refresh token.

        Preserves workspace context from the refresh token claims.

        Args:
            db: Async database session.
            refresh_token: The refresh token to validate.

        Returns:
            Tuple of (new_access_token, new_refresh_token).

        Raises:
            ValueError: If the refresh token is invalid or user not found.
        """
        try:
            payload = decode_refresh_token(refresh_token)
        except JWTError as exc:
            raise ValueError("Invalid refresh token") from exc

        user_id = UUID(payload["sub"])
        result = await db.execute(select(UserModel).where(UserModel.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError("User not found")

        org_id_raw = payload.get("org_id")
        workspace_id_raw = payload.get("workspace_id")
        role_raw = payload.get("role")

        org_id = UUID(org_id_raw) if org_id_raw else None
        workspace_id = UUID(workspace_id_raw) if workspace_id_raw else None

        new_access = create_access_token(user.id, org_id, workspace_id, role_raw)
        new_refresh = create_refresh_token(user.id, org_id, workspace_id, role_raw)
        return new_access, new_refresh

    async def switch_workspace(self, db: AsyncSession, user_id: UUID, workspace_id: UUID) -> tuple[str, str]:
        """Issue new tokens scoped to a different workspace.

        Args:
            db: Async database session.
            user_id: The user's UUID.
            workspace_id: The target workspace UUID.

        Returns:
            Tuple of (new_access_token, new_refresh_token).

        Raises:
            ValueError: If user is not a member of the target workspace.
        """
        result = await db.execute(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.user_id == user_id,
                WorkspaceMemberModel.workspace_id == workspace_id,
                WorkspaceMemberModel.deleted.is_(False),
            )
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            raise ValueError("Not a member of this workspace")

        from hecate.models.workspace import WorkspaceModel

        ws_result = await db.execute(select(WorkspaceModel).where(WorkspaceModel.id == workspace_id))
        workspace = ws_result.scalar_one_or_none()
        if workspace is None:
            raise ValueError("Workspace not found")

        access_token = create_access_token(user_id, workspace.org_id, workspace_id, membership.role.value)
        refresh_token = create_refresh_token(user_id, workspace.org_id, workspace_id, membership.role.value)
        return access_token, refresh_token

    async def get_user_by_id(self, db: AsyncSession, user_id: UUID) -> UserModel | None:
        """Look up a user by ID.

        Args:
            db: Async database session.
            user_id: The user's UUID.

        Returns:
            The UserModel or None if not found.
        """
        result = await db.execute(select(UserModel).where(UserModel.id == user_id))
        return result.scalar_one_or_none()
