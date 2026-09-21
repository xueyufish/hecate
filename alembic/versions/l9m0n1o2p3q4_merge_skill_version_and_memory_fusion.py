"""merge skill versioning + memory fusion heads (5.9d + #160)

Revision ID: l9m0n1o2p3q4
Revises: f23b89d4c399, k9l0m1n2o3p4
Create Date: 2026-09-21

Empty merge revision that re-unifies the alembic head after two
independent feature branches landed in parallel:

- ``f23b89d4c399`` — feat(memory): fusion ranking + importance scoring
- ``k9l0m1n2o3p4`` — feat(skills): skill versioning (5.9d)

Both operate on disjoint tables (memory_* vs skills / skill_versions),
so this merge has no DDL of its own — it just collapses the chain
back to a single head so ``alembic upgrade head`` is unambiguous on
fresh databases.
"""

from __future__ import annotations

revision: str = "l9m0n1o2p3q4"
down_revision: tuple[str, str] = ("f23b89d4c399", "k9l0m1n2o3p4")
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
