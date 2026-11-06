"""Support pending live-supplement input revisions on the durable history.

Revision ID: 023_task_live_input_delivery
Revises: 022_controller_skill_catalogs

Mirrors ``hecate_durable.storage.models.InputRevisionRow`` for the
platform-managed deployment path. Expand-only: existing wake revisions are
untouched. ``effective_turn`` becomes nullable so a live supplement
(urn:hecate:task-live-input/1) can persist between receipt and its binding
at the next not-yet-started decision boundary, and ``settled_reason``
records the explicit not-applied settlement of an unconsumed requirement
when the task reached a terminal state.
"""

import sqlalchemy as sa
from alembic import op

revision = "023_task_live_input_delivery"
down_revision = "022_controller_skill_catalogs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make the causal boundary bindable-in-place and record settlements."""
    with op.batch_alter_table("durable_input_revision") as batch:
        batch.alter_column("effective_turn", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("settled_reason", sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Input history is authoritative state; dropping settlement loses facts."""
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM durable_input_revision WHERE settled_reason IS NOT NULL LIMIT 1"))
        .first()
        is not None
    ):
        raise RuntimeError("Cannot downgrade: settled live-input history must be retained")
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM durable_input_revision WHERE effective_turn IS NULL LIMIT 1"))
        .first()
        is not None
    ):
        raise RuntimeError("Cannot downgrade: pending live-input revisions must settle first")
    with op.batch_alter_table("durable_input_revision") as batch:
        batch.drop_column("settled_reason")
        batch.alter_column("effective_turn", existing_type=sa.Integer(), nullable=False)
