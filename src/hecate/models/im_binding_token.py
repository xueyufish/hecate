"""IMBindingTokenModel — one-time short-lived tokens for the IM binding workflow.

When an unbound IM user sends a message to a Hecate bot, the system issues a
binding token and asks the user to click through the Web UI to confirm
identity. The token is stored as a SHA-256 hash (never plaintext), expires
after 10 minutes, and can be used at most once. Successful confirmation
creates an :class:`IMIdentityBindingModel` row in the same transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel as PydanticBase
from pydantic import ConfigDict
from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class IMBindingTokenModel(BaseModel):
    """ORM model for one-time IM identity binding tokens.

    Key fields:

    - **token_hash** — SHA-256 of the plaintext token shown to the user. The
      plaintext token is never persisted.
    - **expires_at** — hard cutoff. The confirmation endpoint returns 410 Gone
      once the token is past this timestamp.
    - **confirmed_at** — set when the user successfully confirms. A second
      attempt returns 410 Gone.
    - **bound_user_id** — the Hecate user that confirmed the binding, set
      alongside ``confirmed_at``.
    - **im_user_id**, **channel_type**, **im_app_id** — the IM-platform
      identity that this token binds (cached so a webhook arriving after
      token issuance still resolves correctly).
    """

    __tablename__ = "im_binding_tokens"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id"),
        nullable=False,
        default=lambda: uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    im_app_id: Mapped[str] = mapped_column(String(128), nullable=False)
    im_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bound_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
        nullable=True,
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("idx_im_binding_tokens_workspace", "workspace_id", "deleted"),
        Index("idx_im_binding_tokens_lookup", "channel_type", "im_app_id", "im_user_id", "deleted"),
    )


class IMBindingTokenReadSchema(PydanticBase):
    """Read schema for an :class:`IMBindingTokenModel` (internal/admin only).

    Token hashes and identifying metadata are exposed; plaintext tokens are
    never returned. This schema is not intended for end-user API responses.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    channel_type: str
    im_app_id: str
    im_user_id: str
    token_hash: str
    expires_at: datetime
    confirmed_at: datetime | None
    bound_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted: bool


def token_expires_at_default() -> datetime:
    """Return the default expiration timestamp for newly issued tokens (10 minutes)."""
    from datetime import timedelta

    return datetime.now(UTC) + timedelta(minutes=10)
