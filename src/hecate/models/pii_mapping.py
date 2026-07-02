"""PII mapping ORM model for encrypted PII storage in mask_and_encrypt mode."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import LargeBinary

from hecate.models.base import BaseModel


class PIIMappingModel(BaseModel):
    """ORM model for storing Fernet-encrypted PII mappings.

    Each row maps a placeholder (e.g., ``[EMAIL_1]``) to its encrypted
    original value within a session scope.
    """

    __tablename__ = "pii_mappings"

    session_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    placeholder: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    pii_type: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)

    __table_args__ = (
        UniqueConstraint("session_id", "placeholder", name="uq_pii_mappings_session_placeholder"),
        Index("idx_pii_mappings_session", "session_id", "deleted"),
    )
