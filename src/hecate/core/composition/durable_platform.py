"""Composition binding for the durable-execution seams (step6 platform track).

Single process-wide switch point between the seam implementations:

- ``stub`` (default) — the Phase 0 InMemory doubles. Suitable for
  development and tests; carries no durability guarantee.
- ``postgres`` — the durable-execution-core ``SqlDurableStore`` bound to all
  three seams. Store, command recorder, and action ledger share one engine
  and transaction domain, which makes the plan's "critical state + outbox in
  the same transaction" rule the default path instead of an adapter
  discipline; ``ledger_source="core"`` reflects that the ledger is a
  production implementation, and the reconciliation API labels it honestly.

The former platform PostgreSQL adapters over the worktree-B tables
(``task_lifecycle_states`` / ``task_submissions`` / ``control_commands``)
were retired when the core store landed: the durable tables are the single
authoritative home for task lifecycle, commands, and the action ledger, and
the platform contributes only attribution (``workspace_id``) and the
outbox-to-read-model relay. This module also hosts the sync-engine helpers
the worker and relay need — the async application URL maps onto its sync
driver here so the seam's synchronous API can run beside the async ORM.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from hecate.core.config import settings
from hecate.execution.durable import ActionLedger, ControlCommandRecorder, DurableTaskStore

STUB_BACKEND = "stub"
POSTGRES_BACKEND = "postgres"

_SYNC_DRIVER_MAP = {
    "postgresql+asyncpg": "postgresql+psycopg",
    "sqlite+aiosqlite": "sqlite",
    "postgresql": "postgresql+psycopg",
    "postgresql+psycopg": "postgresql+psycopg",
    "sqlite": "sqlite",
}


def to_sync_database_url(url: str) -> str:
    """Map an async (or sync) database URL onto its sync driver form.

    PostgreSQL maps to the psycopg (v3) driver — the same package the
    ``redis`` extra already declares — because asyncpg has no sync API.
    Engine creation raises a clear error when psycopg is not installed;
    the stub binding keeps driver-less installs fully functional.
    """

    scheme = url.split("://", 1)[0]
    mapped = _SYNC_DRIVER_MAP.get(scheme, scheme)
    if "://" in url:
        return mapped + "://" + url.split("://", 1)[1]
    return mapped


def create_sync_engine(url: str) -> Engine:
    """Create the durable store's synchronous engine.

    In-memory SQLite uses ``StaticPool`` so every short session sees the
    same underlying connection (otherwise each session would get a fresh
    empty database).
    """

    sync_url = to_sync_database_url(url)
    if sync_url.startswith("sqlite") and ("://:" in sync_url or sync_url.endswith("://")):
        return create_engine(
            sync_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if sync_url.startswith("postgresql"):
        try:
            import psycopg  # noqa: F401
        except ImportError as err:
            raise ImportError(
                "the postgres durable binding requires the psycopg driver; "
                "install it with: uv pip install 'psycopg[binary]>=3.1.0'"
            ) from err
    return create_engine(sync_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@dataclass(frozen=True)
class DurableSuite:
    """The bound seam implementations plus provenance metadata."""

    store: DurableTaskStore
    recorder: ControlCommandRecorder
    ledger: ActionLedger
    backend: str
    ledger_source: str


_lock = threading.Lock()
_suite: DurableSuite | None = None


def _build_suite() -> DurableSuite:
    backend = getattr(settings, "HECATE_DURABLE_BACKEND", STUB_BACKEND) or STUB_BACKEND
    if backend == POSTGRES_BACKEND:
        from hecate_durable.storage import SqlDurableStore

        store = SqlDurableStore(to_sync_database_url(settings.DATABASE_URL), source="platform")
        return DurableSuite(
            store=store,
            recorder=store,
            ledger=store,
            backend=backend,
            ledger_source="core",
        )
    if backend != STUB_BACKEND:
        raise ValueError(f"unknown HECATE_DURABLE_BACKEND {backend!r}; expected 'stub' or 'postgres'")
    from hecate.execution.stub_durable import (
        InMemoryActionLedger,
        InMemoryControlCommandRecorder,
        InMemoryDurableTaskStore,
    )

    return DurableSuite(
        store=InMemoryDurableTaskStore(),
        recorder=InMemoryControlCommandRecorder(),
        ledger=InMemoryActionLedger(),
        backend=STUB_BACKEND,
        ledger_source="stub",
    )


def get_durable_suite() -> DurableSuite:
    """Return the process-wide durable seam binding (lazily built)."""

    global _suite
    with _lock:
        if _suite is None:
            _suite = _build_suite()
        return _suite


def set_durable_suite(suite: DurableSuite | None) -> None:
    """Override or clear the binding (test seam; not for production use)."""

    global _suite
    with _lock:
        _suite = suite
