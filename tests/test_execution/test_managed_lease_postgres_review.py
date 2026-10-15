"""Opt-in PostgreSQL integration checks for managed lease review repairs.

HECATE_REVIEW_POSTGRES_URL must point at an explicitly provisioned test
database. Each test owns and removes only its randomly named schema.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from datetime import datetime

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.execution.governance_events import PlatformEventService
from hecate.models.platform_event import PlatformEventModel
from tests.test_execution.test_event_position_fencing import WS, _envelope
from tests.test_migrations.test_platform_event_position_repair import (
    REVISION_PATH,
    _load_revision,
    _pre_migration_table,
)

POSTGRES_URL = os.environ.get("HECATE_REVIEW_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="requires isolated HECATE_REVIEW_POSTGRES_URL")


@pytest.fixture
async def pg_review():
    admin = create_async_engine(POSTGRES_URL)
    schema = "hecate_review_" + uuid.uuid4().hex
    async with admin.begin() as conn:
        await conn.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(POSTGRES_URL, connect_args={"server_settings": {"search_path": schema}})
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def test_pg_duplicate_migrations_with_real_uuid_and_null_scope(pg_review):
    table = _pre_migration_table()

    def upgrade(sync):
        table.create(sync)
        for event_id, row_id, created, workspace in (
            ("earliest", uuid.UUID(int=(1 << 128) - 1), datetime(2020, 1, 1), uuid.UUID(int=1)),
            ("later", uuid.UUID(int=1), datetime(2021, 1, 1), uuid.UUID(int=1)),
            ("null-first", uuid.uuid4(), datetime(2020, 1, 1), None),
            ("null-second", uuid.uuid4(), datetime(2021, 1, 1), None),
        ):
            sync.execute(
                table.insert().values(
                    id=row_id,
                    created_at=created,
                    event_id=event_id,
                    contract_version="0.1",
                    kind="event",
                    task_issuer_domain="iss",
                    task_ref_id="task",
                    run_issuer_domain="iss",
                    run_ref_id="run",
                    source_sequence=0,
                    occurred_at="now",
                    received_at="now",
                    payload={},
                    envelope={},
                    workspace_id=workspace,
                )
            )
        operations = Operations(MigrationContext.configure(sync))
        old = _load_revision()
        old.op = operations
        old.upgrade()
        path = REVISION_PATH.with_name("7a6e5d4c3b20_unscoped_platform_event_positions.py")
        spec = importlib.util.spec_from_file_location("review_unscoped_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.op = operations
        module.upgrade()
        assert set(sync.execute(sa.select(table.c.event_id)).scalars()) == {"earliest", "null-first"}

    async with pg_review.begin() as conn:
        await conn.run_sync(upgrade)
    async with pg_review.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            async with conn.begin_nested():
                await conn.execute(table.update().where(table.c.event_id == "earliest").values(workspace_id=None))


async def test_pg_concurrent_allocation_retries_in_original_transaction(pg_review, monkeypatch):
    async with pg_review.begin() as conn:
        await conn.run_sync(PlatformEventModel.__table__.create)
    sessions = async_sessionmaker(pg_review, expire_on_commit=False)
    ready = asyncio.Event()
    allocators = []
    original = PlatformEventService.next_sequence

    async def synchronized(service, *args):
        position = await original(service, *args)
        if service not in allocators:
            allocators.append(service)
            if len(allocators) == 2:
                ready.set()
            await ready.wait()
        return position

    monkeypatch.setattr(PlatformEventService, "next_sequence", synchronized)

    async def append(event_id):
        async with sessions() as db:
            result = await PlatformEventService(db).append_resequenced(_envelope(event_id), workspace_id=WS)
            await db.commit()
            return result

    async with asyncio.timeout(30):
        first, second = await asyncio.gather(append("one"), append("two"))
    assert {first.source_sequence, second.source_sequence} == {0, 1}
    async with sessions() as db:
        assert set((await db.execute(sa.select(PlatformEventModel.event_id))).scalars()) == {"one", "two"}
