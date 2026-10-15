"""Enforce event position uniqueness for runs without a workspace.

Revision ID: 7a6e5d4c3b20
Revises: daebf1a2b3c4
"""

import sqlalchemy as sa
from alembic import op

revision = "7a6e5d4c3b20"
down_revision = "daebf1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Repair unscoped duplicate positions before installing the partial index."""
    conn = op.get_bind()
    table = sa.Table("platform_events", sa.MetaData(), autoload_with=conn)
    columns = [table.c.run_issuer_domain, table.c.run_ref_id, table.c.source_sequence]
    duplicates = conn.execute(
        sa.select(*columns).where(table.c.workspace_id.is_(None)).group_by(*columns).having(sa.func.count() > 1)
    ).all()
    for issuer, run_id, sequence in duplicates:
        position = sa.and_(
            table.c.workspace_id.is_(None),
            table.c.run_issuer_domain == issuer,
            table.c.run_ref_id == run_id,
            table.c.source_sequence == sequence,
        )
        rows = conn.execute(
            sa.select(table.c.id, table.c.event_id)
            .where(position)
            .order_by(table.c.created_at.asc().nulls_last(), table.c.id)
        ).all()
        conn.execute(table.delete().where(position, table.c.id != rows[0].id))
        print(
            f"[platform-events-fencing] unscoped duplicate position repaired: "
            f"run={issuer}/{run_id} seq={sequence} kept_event_row={rows[0].id} "
            f"removed_event_ids={[row.event_id for row in rows[1:]]}"
        )
    op.create_index(
        "uq_platform_events_unscoped_position",
        "platform_events",
        ["run_issuer_domain", "run_ref_id", "source_sequence"],
        unique=True,
        postgresql_where=sa.text("workspace_id IS NULL"),
        sqlite_where=sa.text("workspace_id IS NULL"),
    )


def downgrade() -> None:
    """Drop only the unscoped position index."""
    op.drop_index("uq_platform_events_unscoped_position", table_name="platform_events")
