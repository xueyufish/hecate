"""Position-uniqueness migration: duplicate repair before enforcement.

The pre-constraint schema can hold same-position duplicates (the silent
max+1 co-location the fencing closes). The migration repairs them with a
deterministic keep-rule (earliest inserted row) and only then creates
``uq_platform_events_run_position``. Runs on SQLite: the pre-migration
table shape is constructed in the test, so no PostgreSQL service is needed
(the full-chain drift gate stays PG-gated in CI).
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError

REPO_ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = REPO_ROOT / "alembic" / "versions" / "daebf1a2b3c4_platform_event_position_uniqueness.py"


def _load_revision():
    spec = importlib.util.spec_from_file_location("daebf1a2b3c4_platform_event_position_uniqueness", REVISION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pre_migration_table() -> sa.Table:
    """The platform_events shape BEFORE the position constraint existed."""

    return sa.Table(
        "platform_events",
        sa.MetaData(),
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("deleted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("event_id", sa.String(512), nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("task_issuer_domain", sa.String(255), nullable=False),
        sa.Column("task_ref_id", sa.String(512), nullable=False),
        sa.Column("run_issuer_domain", sa.String(255), nullable=False),
        sa.Column("run_ref_id", sa.String(512), nullable=False),
        sa.Column("source_sequence", sa.Integer, nullable=False),
        sa.Column("occurred_at", sa.Text, nullable=False),
        sa.Column("received_at", sa.Text, nullable=False),
        sa.Column("payload_schema_ref", sa.Text, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("correlation_id", sa.String(512), nullable=True),
        sa.Column("causation_id", sa.String(512), nullable=True),
        sa.Column("actor_kind", sa.String(32), nullable=True),
        sa.Column("actor_id", sa.String(255), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("envelope", sa.JSON, nullable=False),
        sa.Column("workspace_id", sa.Uuid, nullable=True),
    )


def _insert(table: sa.Table, conn, event_id: str, sequence: int) -> int:
    result = conn.execute(
        table.insert().values(
            event_id=event_id,
            contract_version="0.1",
            kind="event",
            task_issuer_domain="iss",
            task_ref_id="task-1",
            run_issuer_domain="iss",
            run_ref_id="run-1",
            source_sequence=sequence,
            occurred_at="2026-10-14T00:00:00+00:00",
            received_at="2026-10-14T00:00:00+00:00",
            payload={},
            envelope={},
            workspace_id=uuid.UUID(int=1),
        )
    )
    return result.inserted_primary_key[0]


@pytest.fixture
def upgrade_binding(tmp_path):
    """A SQLite database holding the pre-migration schema; yields (conn, op)."""

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'events.db'}")
    table = _pre_migration_table()
    table.create(engine)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        yield conn, Operations(ctx), table
    engine.dispose()


def test_duplicate_positions_are_repaired_before_constraint(upgrade_binding):
    conn, ops, table = upgrade_binding
    keep_id = _insert(table, conn, "evt-keep", 3)
    drop_id = _insert(table, conn, "evt-drop", 3)
    _insert(table, conn, "evt-other", 4)
    conn.commit()

    module = _load_revision()
    module.op = ops
    module.upgrade()
    conn.commit()

    rows = conn.execute(sa.select(table.c.event_id, table.c.source_sequence).order_by(table.c.id)).fetchall()
    assert rows == [("evt-keep", 3), ("evt-other", 4)]

    # Enforcement is real: a same-position insert now collides.
    with pytest.raises(IntegrityError):
        conn.execute(
            table.insert().values(
                event_id="evt-collider",
                contract_version="0.1",
                kind="event",
                task_issuer_domain="iss",
                task_ref_id="task-1",
                run_issuer_domain="iss",
                run_ref_id="run-1",
                source_sequence=3,
                occurred_at="2026-10-14T00:00:00+00:00",
                received_at="2026-10-14T00:00:00+00:00",
                payload={},
                envelope={},
                workspace_id=uuid.UUID(int=1),
            )
        )
        conn.commit()
    conn.rollback()
    assert keep_id < drop_id  # the deterministic rule kept the earliest row


def test_clean_table_gains_constraint_without_repair(upgrade_binding):
    conn, ops, table = upgrade_binding
    _insert(table, conn, "evt-a", 0)
    _insert(table, conn, "evt-b", 1)
    conn.commit()

    module = _load_revision()
    module.op = ops
    module.upgrade()
    conn.commit()

    rows = conn.execute(sa.select(table.c.event_id).order_by(table.c.id)).fetchall()
    assert [row[0] for row in rows] == ["evt-a", "evt-b"]
