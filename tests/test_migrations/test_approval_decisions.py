"""Approval decision DDL defaults, recovery indexes and retained audit facts."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from hecate.models.approval import ApprovalDecisionModel


def _revision():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/021_approval_decisions.py"
    spec = importlib.util.spec_from_file_location("approval_decision_revision", path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


async def test_upgrade_defaults_allow_operation_insert(db_session, monkeypatch):
    """Exercise real migration DDL without dropping the shared fixture schema."""
    revision = _revision()

    def run(session):
        connection = session.connection()
        operations = Operations(MigrationContext.configure(connection))
        # Only the table/index names differ; all column definitions and
        # constraints come from the shipped migration, not ORM create_all.
        monkeypatch.setattr(
            revision,
            "op",
            SimpleNamespace(
                f=lambda name: "review_" + name,
                create_table=lambda name, *args, **kwargs: operations.create_table("review_" + name, *args, **kwargs),
                create_index=lambda name, table, columns, **kwargs: operations.create_index(
                    "review_" + name, "review_" + table, columns, **kwargs
                ),
            ),
        )
        revision.upgrade()
        table = sa.Table("review_approval_decisions", sa.MetaData(), autoload_with=connection)
        values = dict(
            id=uuid.uuid4().hex,
            operation_id="migration-op",
            approval_ref="ref",
            task_id=uuid.uuid4().hex,
            workspace_id=uuid.uuid4().hex,
            decision="approve",
            decided_by="admin",
            request_digest="a" * 64,
            wait_token="token",
            expected_revision=1,
        )
        connection.execute(table.insert().values(**values))
        row = connection.execute(sa.select(table)).mappings().one()
        assert row["created_at"] is not None and row["updated_at"] is not None
        assert row["deleted"] is False
        indexes = sa.inspect(connection).get_indexes(table.name)
        assert any(index["column_names"] == ["resume_command_id", "deleted", "updated_at", "id"] for index in indexes)
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(table.insert().values(**{**values, "id": uuid.uuid4().hex}))

    await db_session.run_sync(run)


@pytest.mark.parametrize("evidence", ["empty", "live", "deleted"])
async def test_downgrade_retains_decision_audit(db_session, monkeypatch, evidence):
    if evidence != "empty":
        db_session.add(
            ApprovalDecisionModel(
                operation_id="audit",
                approval_ref="ref",
                task_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                decision="reject",
                reason="audit",
                decided_by="admin",
                request_digest="a" * 64,
                wait_token="token",
                expected_revision=1,
                deleted=evidence == "deleted",
            )
        )
        await db_session.flush()
    revision = _revision()
    drops = []

    def run(session):
        monkeypatch.setattr(
            revision,
            "op",
            SimpleNamespace(
                get_bind=session.connection,
                f=lambda name: name,
                drop_index=lambda name, **kwargs: drops.append(name),
                drop_table=drops.append,
            ),
        )
        if evidence == "empty":
            revision.downgrade()
        else:
            with pytest.raises(RuntimeError, match="must be retained"):
                revision.downgrade()

    await db_session.run_sync(run)
    assert drops == (
        ["ix_approval_decisions_resume_pending", "ix_approval_decisions_approval_ref", "approval_decisions"]
        if evidence == "empty"
        else []
    )
