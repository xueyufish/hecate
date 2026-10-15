"""Position uniqueness for the platform event stream (managed slice).

Revision ID: daebf1a2b3c4
Revises: c9dae0f1b2a3
"""

import sqlalchemy as sa
from alembic import op

revision = "daebf1a2b3c4"
down_revision = "c9dae0f1b2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Repair same-position duplicates, then enforce one row per position.

    The repair keeps the earliest-created row (UUID breaks timestamp ties) of each
    duplicate group and removes the rest; removed event ids are printed into
    the migration log so the repair is auditable. With a deterministic
    keep-rule every duplicate group is resolvable — a failure here means the
    constraint creation itself failed, and the migration aborts before
    claiming enforcement.
    """

    conn = op.get_bind()
    table = sa.Table("platform_events", sa.MetaData(), autoload_with=conn)
    duplicates = conn.execute(
        sa.text(
            """
            SELECT workspace_id, run_issuer_domain, run_ref_id, source_sequence
            FROM platform_events
            GROUP BY workspace_id, run_issuer_domain, run_ref_id, source_sequence
            HAVING count(*) > 1
            """
        )
    ).fetchall()
    for ws, issuer, run_id, sequence in duplicates:
        position = sa.and_(
            table.c.workspace_id.is_not_distinct_from(ws),
            table.c.run_issuer_domain == issuer,
            table.c.run_ref_id == run_id,
            table.c.source_sequence == sequence,
        )
        keep = conn.execute(
            sa.select(table.c.id).where(position).order_by(table.c.created_at.asc().nulls_last(), table.c.id).limit(1)
        ).scalar_one()
        removed = conn.execute(sa.select(table.c.event_id).where(position, table.c.id != keep)).fetchall()
        conn.execute(table.delete().where(position, table.c.id != keep))
        print(
            f"[platform-events-fencing] duplicate position repaired: "
            f"workspace={ws} run={issuer}/{run_id} seq={sequence} "
            f"kept_event_row={keep} removed_event_ids={[row[0] for row in removed]}"
        )
    with op.batch_alter_table("platform_events") as batch:
        batch.create_unique_constraint(
            "uq_platform_events_run_position",
            ["workspace_id", "run_issuer_domain", "run_ref_id", "source_sequence"],
        )


def downgrade() -> None:
    """Drop the position constraint; arrival-order labels stay as stored."""

    with op.batch_alter_table("platform_events") as batch:
        batch.drop_constraint("uq_platform_events_run_position")
