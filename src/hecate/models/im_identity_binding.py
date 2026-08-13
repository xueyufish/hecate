"""IMIdentityBindingModel — maps an IM-platform user to a Hecate user within a workspace.

The binding is the cornerstone of Hecate's mandatory Bound Identity model for
IM channels: every inbound IM message must resolve to a known
``(workspace_id, user_id)`` via this table before being routed to an Agent.

Schema design rationale:

- ``(workspace_id, channel_type, im_app_id, im_user_id)`` is the unique
  identity key for active bindings. Because SQLite and most SQL engines
  treat ``NULL`` as distinct in UNIQUE constraints, application-level
  uniqueness is enforced by :class:`IMBindingService` (look up an active
  binding before insert). For PostgreSQL deployments, a partial unique
  index ``WHERE unbound_at IS NULL`` provides defense in depth.
- When a user unbinds, the row is marked ``deleted=True`` and
  ``deleted_at=<now>`` for audit history; the ``unbound_at`` column on
  this table is reserved for a future migration that uses partial
  indexes on Postgres.
- The same Hecate user can hold bindings on multiple channels and apps —
  implemented by a non-unique ``user_id`` column with a workspace-scoped
  composite index.
- Cross-workspace duplication is allowed by including ``workspace_id``
  in the unique key.
- All bindings are soft-deletable via the inherited ``BaseModel``
  machinery so audit history is preserved when a user unbinds.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class IMIdentityBindingModel(BaseModel):
    """ORM model for IM identity bindings — IM user ↔ Hecate user per workspace.

    Key fields:

    - **workspace_id** — FK to :class:`WorkspaceModel.id`. Required. Same IM
      identity can bind to different users across workspaces.
    - **user_id** — FK to :class:`UserModel.id`. Required. One Hecate user may
      have many bindings (Feishu + Slack + future channels).
    - **channel_type** — lowercase identifier such as ``"feishu"`` or
      ``"slack"``.
    - **im_app_id** — the IM-platform App identifier (e.g., Feishu
      ``cli_xxx``). Required because a workspace may host multiple IM Apps
      per channel type in principle.
    - **im_user_id** — the IM-platform user identifier (e.g., Feishu
      ``open_id`` ``ou_xxx``, Slack ``U...`` ID).
    - **unbound_at** — set when a user unbinds. Combined with ``deleted``,
      distinguishes active from historical rows. Application code MUST check
      ``unbound_at IS NULL AND deleted = false`` to find active bindings.
    - **metadata_** — passthrough JSON for IM-platform-specific data that
      does not warrant a dedicated column (display name, avatar URL, etc.).
    """

    __tablename__ = "im_identity_bindings"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"),
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    im_app_id: Mapped[str] = mapped_column(String(128), nullable=False)
    im_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    unbound_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_im_identity_bindings_user", "user_id", "deleted"),
        Index("idx_im_identity_bindings_workspace", "workspace_id", "deleted"),
        # Lookup by (workspace, channel, app, im_user, unbound_at) — the
        # service layer adds ``unbound_at IS NULL`` to find active bindings.
        Index(
            "idx_im_identity_bindings_active",
            "workspace_id",
            "channel_type",
            "im_app_id",
            "im_user_id",
            "unbound_at",
        ),
    )


class IMIdentityBindingCreateSchema:
    """Placeholder for parity with other models' CreateSchema siblings.

    Concrete Pydantic schemas for create/read are added alongside the API
    endpoints in a later task; this file currently only defines the ORM model.
    """
