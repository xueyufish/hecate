"""FeatureFlagModel ORM for runtime feature flags (Tier 2)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from hecate.core.database import Base


class FeatureFlagModel(Base):
    """Runtime feature flag persisted in the database.

    Tier 2 feature flags — mutated at runtime via REST API, evaluated
    on the hot path with Redis caching, support lifecycle states and
    targeting rules (percentage / tenant allowlist / user allowlist).
    """

    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    targeting_rules: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_removal_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    evaluation_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_true_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
