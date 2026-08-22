"""Fresh-database migration smoke + schema/model drift gate (PostgreSQL).

Runs the full alembic chain against a throwaway PostgreSQL database and
compares the resulting schema with the ORM metadata. Unit tests build
their schema via ``Base.metadata.create_all``, which by construction
cannot observe migration drift — this module is the suite's only guard
against the schema/ORM drift bug class (missing tables, missing
columns, broken migration graph) that broke fresh deployments before
PR #87.

PostgreSQL is required because production migrations include
hand-written constraint operations that only the PostgreSQL dialect
supports. The test skips when no local PostgreSQL is reachable and
runs in CI via the postgres service container.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from hecate.core.database import Base

REPO_ROOT = Path(__file__).resolve().parents[2]

ADMIN_URL = os.environ.get("DRIFT_ADMIN_URL", "postgresql://hecate:hecate@localhost:5432/postgres")
DRIFT_DB = "hecate_drift_test"
DRIFT_ASYNC_URL = os.environ.get("DRIFT_DATABASE_URL", f"postgresql+asyncpg://hecate:hecate@localhost:5432/{DRIFT_DB}")


def _pg_available() -> bool:
    async def probe() -> bool:
        import asyncpg

        try:
            conn = await asyncio.wait_for(asyncpg.connect(ADMIN_URL), timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(probe())


async def _recreate_drift_db() -> None:
    import asyncpg

    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", DRIFT_DB)
        await conn.execute(f'DROP DATABASE IF EXISTS "{DRIFT_DB}"')  # noqa: S608 — DRIFT_DB is a module constant
        await conn.execute(f'CREATE DATABASE "{DRIFT_DB}"')  # noqa: S608 — DDL cannot be parameterized
    finally:
        await conn.close()


async def _drop_drift_db() -> None:
    import asyncpg

    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", DRIFT_DB)
        await conn.execute(f'DROP DATABASE IF EXISTS "{DRIFT_DB}"')  # noqa: S608 — DRIFT_DB is a module constant
    finally:
        await conn.close()


async def _inspect_migrated_schema() -> dict[str, set[str]]:
    engine = create_async_engine(DRIFT_ASYNC_URL)
    try:

        def _inspect_sync(conn):
            from sqlalchemy import inspect

            inspector = inspect(conn)
            return {
                table: {column["name"] for column in inspector.get_columns(table)}
                for table in inspector.get_table_names()
            }

        async with engine.connect() as conn:
            return await conn.run_sync(_inspect_sync)
    finally:
        await engine.dispose()


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not reachable; set DRIFT_ADMIN_URL or start docker compose")
def test_alembic_upgrade_head_matches_models(monkeypatch):
    # Import the app so its production import chain registers every model on
    # Base.metadata before the comparison below. (Function-level on purpose:
    # top-level placement trips differing isort verdicts between ruff 0.11
    # and 0.15.)
    import hecate.main  # noqa: F401

    asyncio.run(_recreate_drift_db())

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    monkeypatch.setenv("DATABASE_URL", DRIFT_ASYNC_URL)
    try:
        command.upgrade(cfg, "head")
        migrated_columns = asyncio.run(_inspect_migrated_schema())
    finally:
        asyncio.run(_drop_drift_db())

    # Tables that exist in models but are intentionally absent from the
    # migration chain — historically created by older migrations, dropped
    # by a later cleanup migration (13.4a-7 / C2 for ``checkpoints``).
    # They survive in Base.metadata because tests still construct the
    # ORM class (e.g. GC tests inspect them); we accept the table-less
    # migration path for these and the model lives on as a thin wrapper
    # pointing at no underlying relation.
    tables_dropped_by_cleanup: set[str] = {"checkpoints"}

    drift: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name in tables_dropped_by_cleanup:
            continue
        migrated = migrated_columns.get(table.name)
        if migrated is None:
            drift.append(f"table '{table.name}' exists in models but no migration creates it")
            continue
        for column in table.columns:
            if column.name not in migrated:
                drift.append(f"column '{table.name}.{column.name}' exists in models but no migration adds it")

    assert not drift, "Schema/ORM drift detected (run alembic revision to close the gap):\n" + "\n".join(drift)
