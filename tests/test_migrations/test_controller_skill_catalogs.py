"""Real DDL defaults and retained Controller Skill recovery bindings."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from hecate.models.skill import ControllerSkillCatalogModel


def _revision():
    path = Path(__file__).resolve().parents[2] / "alembic/versions/022_controller_skill_catalogs.py"
    spec = importlib.util.spec_from_file_location("controller_skill_revision", path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


async def test_catalog_ddl_supplies_timestamps_and_unique_task(db_session, monkeypatch):
    revision = _revision()

    def run(session):
        connection = session.connection()
        operations = Operations(MigrationContext.configure(connection))
        monkeypatch.setattr(
            revision,
            "op",
            SimpleNamespace(
                f=lambda name: "review_" + name,
                create_table=lambda name, *args, **kwargs: operations.create_table("review_" + name, *args, **kwargs),
            ),
        )
        revision.upgrade()
        table = sa.Table("review_controller_skill_catalogs", sa.MetaData(), autoload_with=connection)
        values = dict(
            id=uuid.uuid4().hex,
            task_id=uuid.uuid4().hex,
            workspace_id=uuid.uuid4().hex,
            agent_id=uuid.uuid4().hex,
            catalog_rendered="",
            advertised_names=[],
            pins={},
            unversioned_snapshots={},
            budgets={},
            content_sha256="a" * 64,
        )
        connection.execute(table.insert().values(**values))
        row = connection.execute(sa.select(table)).mappings().one()
        assert row["created_at"] is not None and row["updated_at"] is not None
        assert row["deleted"] is False
        with pytest.raises(sa.exc.IntegrityError), connection.begin_nested():
            connection.execute(table.insert().values(**{**values, "id": uuid.uuid4().hex}))

    await db_session.run_sync(run)


@pytest.mark.parametrize("evidence", ["empty", "live", "deleted"])
async def test_downgrade_retains_frozen_catalogs(db_session, monkeypatch, evidence):
    if evidence != "empty":
        db_session.add(
            ControllerSkillCatalogModel(
                task_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                catalog_rendered="",
                advertised_names=[],
                pins={},
                unversioned_snapshots={},
                budgets={},
                content_sha256="a" * 64,
                deleted=evidence == "deleted",
            )
        )
        await db_session.flush()
    revision = _revision()
    drops = []

    def run(session):
        monkeypatch.setattr(revision, "op", SimpleNamespace(get_bind=session.connection, drop_table=drops.append))
        if evidence == "empty":
            revision.downgrade()
        else:
            with pytest.raises(RuntimeError, match="retained"):
                revision.downgrade()

    await db_session.run_sync(run)
    assert drops == (["controller_skill_catalogs"] if evidence == "empty" else [])
