"""merge dlp and plugin provenance heads

Revision ID: 09cd094a786b
Revises: c6d7e8f9a0b1, b1b2c3d4e5f6
Create Date: 2026-08-20 20:59:50.747647

"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "09cd094a786b"
down_revision: str | tuple[str, ...] | None = ("c6d7e8f9a0b1", "b1b2c3d4e5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge the DLP and plugin provenance migration branches."""


def downgrade() -> None:
    """Merge migration is a no-op in both directions."""
