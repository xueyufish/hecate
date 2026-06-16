"""Workspace member ORM model and Pydantic schemas.

Defines the persistence layer and API schemas for workspace membership,
linking users to workspaces with role-based access control.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import Enum, Index
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class WorkspaceRole(enum.StrEnum):
    """Roles for workspace-level access control."""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class WorkspaceMemberModel(BaseModel):
    """ORM model for workspace membership.

    Each entry links a user to a workspace with a specific role.
    A user can be a member of multiple workspaces with different roles.
    """

    __tablename__ = "workspace_members"

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, name="workspace_role", create_constraint=True),
        nullable=False,
        default=WorkspaceRole.VIEWER,
    )

    __table_args__ = (
        Index("idx_ws_members_user_workspace", "user_id", "workspace_id", unique=True),
        Index("idx_ws_members_workspace", "workspace_id"),
    )


class WorkspaceMemberCreateSchema(PydanticBase):
    """Schema for adding a member to a workspace."""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role: WorkspaceRole = WorkspaceRole.VIEWER


class WorkspaceMemberUpdateSchema(PydanticBase):
    """Schema for updating a member's role."""

    model_config = ConfigDict(extra="forbid")

    role: WorkspaceRole


class WorkspaceMemberReadSchema(PydanticBase):
    """Schema for reading workspace membership data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    role: WorkspaceRole
    created_at: datetime
    updated_at: datetime
