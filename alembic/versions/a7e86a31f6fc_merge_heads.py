"""merge_heads

Revision ID: a7e86a31f6fc
Revises: 016_workflow_mode_and_versioning, b94c75d0c398
Create Date: 2026-06-16 01:06:55.420209

"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "a7e86a31f6fc"
down_revision: str | None = (
    "016_workflow_mode_and_versioning",
    "b94c75d0c398",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
