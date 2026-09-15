"""Migration data test: model_registry publish backfill (6.47).

Verifies the compatibility contract from the change design (D6): after the
publish-state migration, every pre-existing non-deleted registry row is
published (the /v1/models reference surface must not shrink across the
upgrade), while soft-deleted rows stay unpublished.

PostgreSQL is required (mirrors test_upgrade_drift.py); the test skips when
no local PostgreSQL is reachable and runs in CI via the postgres service.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]

ADMIN_URL = os.environ.get("DRIFT_ADMIN_URL", "postgresql://hecate:hecate@localhost:5432/postgres")
DRIFT_DB = "hecate_publish_backfill_test"
DRIFT_ASYNC_URL = os.environ.get("DRIFT_DATABASE_URL", f"postgresql+asyncpg://hecate:hecate@localhost:5432/{DRIFT_DB}")

#: The revision that precedes the publish-state migration.
PRE_PUBLISH_REVISION = "i5d6e7f8a9b0"


def _pg_available() -> bool:
    async def probe() -> bool:
        import asyncpg

        try:
            conn = await asyncio.wait_for(asyncpg.connect(ADMIN_URL), timeout=2)
        except Exception:
            return False
        else:
            await conn.close()
            return True

    return asyncio.run(probe())


async def _recreate_db() -> None:
    import asyncpg

    conn = await asyncpg.connect(ADMIN_URL)
    try:
        await conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", DRIFT_DB)  # noqa: S608 — constant db name
        await conn.execute(f'DROP DATABASE IF EXISTS "{DRIFT_DB}"')  # noqa: S608 — DDL cannot be parameterized
        await conn.execute(f'CREATE DATABASE "{DRIFT_DB}"')  # noqa: S608 — DDL cannot be parameterized
    finally:
        await conn.close()


def _pg_url() -> str:
    return DRIFT_ASYNC_URL.replace("postgresql+asyncpg", "postgresql")


def _upgrade(monkeypatch, target: str) -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    monkeypatch.setenv("DATABASE_URL", DRIFT_ASYNC_URL)
    command.upgrade(cfg, target)


@pytest.mark.skipif(not _pg_available(), reason="PostgreSQL not reachable; set DRIFT_ADMIN_URL or start docker compose")
def test_publish_backfill_preserves_reference_surface(monkeypatch):
    """Live rows existing before the migration must end up published."""
    asyncio.run(_recreate_db())

    _upgrade(monkeypatch, PRE_PUBLISH_REVISION)

    async def _seed() -> None:
        import asyncpg

        conn = await asyncpg.connect(_pg_url())
        try:
            await conn.execute(
                """
                INSERT INTO model_providers (id, name, display_name, api_key_encrypted, config, is_enabled, status)
                VALUES ($1, 'backfill-prov', 'Backfill Prov', 'enc', '{}', TRUE, 'active')
                """,
                "00000000-0000-0000-0000-000000000001",
            )
            # One live row and one soft-deleted row: only the live row is on
            # the reference surface, so only it must come back published.
            await conn.execute(
                """
                INSERT INTO model_registry
                    (id, provider_id, model_id, display_name, model_type,
                     capabilities, model_metadata, is_custom, is_enabled)
                VALUES
                    ($1, $3, 'backfill-live-model', 'Live', 'chat', '{}', '{}', FALSE, TRUE),
                    ($2, $3, 'backfill-dead-model', 'Dead', 'chat', '{}', '{}', FALSE, TRUE)
                """,
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
                "00000000-0000-0000-0000-000000000001",
            )
            await conn.execute(
                "UPDATE model_registry SET deleted = TRUE, deleted_at = NOW() WHERE model_id = 'backfill-dead-model'"
            )
        finally:
            await conn.close()

    asyncio.run(_seed())
    _upgrade(monkeypatch, "head")

    async def _fetch() -> dict[str, bool]:
        import asyncpg

        conn = await asyncpg.connect(_pg_url())
        try:
            rows = await conn.fetch("SELECT model_id, is_published FROM model_registry")
            return {r["model_id"]: r["is_published"] for r in rows}
        finally:
            await conn.close()

    states = asyncio.run(_fetch())
    assert states["backfill-live-model"] is True, "live row must be backfilled to published"
    assert states["backfill-dead-model"] is False, "soft-deleted rows are not on the reference surface"
