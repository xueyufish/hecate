"""AgentCard signing key ORM model for persisting key pairs."""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class AgentCardKeyModel(BaseModel):
    """ORM model for AgentCard signing keys.

    Key fields:
    - kid: Unique key identifier (UUID string).
    - private_key: Private key in JWK format (JSON).
    - public_key: Public key in JWK format (JSON).
    - algorithm: Signing algorithm (e.g., "ES256").
    - workspace_id: Tenant scope.
    - status: Key status (active, rotating, revoked).
    """

    __tablename__ = "agent_card_keys"

    kid: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    private_key: Mapped[dict] = mapped_column(JSON, nullable=False)
    public_key: Mapped[dict] = mapped_column(JSON, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(10), nullable=False, default="ES256")
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=uuid.UUID("00000000-0000-0000-0000-000000000000"),
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    __table_args__ = (Index("idx_agent_card_keys_workspace", "workspace_id", "deleted"),)
