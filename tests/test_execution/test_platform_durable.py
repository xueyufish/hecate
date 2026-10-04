"""Platform durable adapter and composition binding tests (step6 track B)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine

from hecate.core.composition.durable_platform import (
    DurableSuite,
    get_durable_suite,
    set_durable_suite,
)
from hecate.execution.platform_durable import (
    PlatformDurableFactory,
    create_sync_engine,
    to_sync_database_url,
)
from hecate.execution.stub_durable import (
    InMemoryActionLedger,
    InMemoryControlCommandRecorder,
    InMemoryDurableTaskStore,
)


def test_sync_url_mapping() -> None:
    assert to_sync_database_url("postgresql+asyncpg://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert to_sync_database_url("sqlite+aiosqlite:///tmp/x.db") == "sqlite:///tmp/x.db"
    assert to_sync_database_url("sqlite+aiosqlite://") == "sqlite://"
    # Already-sync forms pass through unchanged.
    assert to_sync_database_url("postgresql://h/db") == "postgresql+psycopg://h/db"


def test_factory_is_lazy_and_cached() -> None:
    factory = PlatformDurableFactory("sqlite://")
    engine = factory.engine()
    assert isinstance(engine, Engine)
    assert factory.engine() is engine


def test_factory_create_all_builds_seam_tables() -> None:
    factory = PlatformDurableFactory("sqlite://")
    factory.create_all()
    store = factory.task_store()
    # A trivial write proves the tables exist and commit.
    from hecate.contracts.execution.durable import TaskLifecycleState
    from hecate.contracts.execution.references import task_ref

    record = store.apply_task_state(task_ref("hecate", "t-1"), TaskLifecycleState.QUEUED)
    assert store.get_task_state(task_ref("hecate", "t-1")) == record


def test_default_binding_is_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "HECATE_DURABLE_BACKEND", "stub", raising=False)
    set_durable_suite(None)
    try:
        suite = get_durable_suite()
        assert isinstance(suite.store, InMemoryDurableTaskStore)
        assert isinstance(suite.recorder, InMemoryControlCommandRecorder)
        assert isinstance(suite.ledger, InMemoryActionLedger)
        assert suite.backend == "stub"
        assert suite.ledger_source == "stub"
        # Lazy singleton: second call returns the same binding.
        assert get_durable_suite() is suite
    finally:
        set_durable_suite(None)


def test_postgres_binding_uses_platform_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "HECATE_DURABLE_BACKEND", "postgres", raising=False)
    monkeypatch.setattr(settings, "DATABASE_URL", "sqlite+aiosqlite://", raising=False)
    set_durable_suite(None)
    try:
        suite = get_durable_suite()
        assert suite.backend == "postgres"
        assert suite.ledger_source == "stub"
        assert not isinstance(suite.store, InMemoryDurableTaskStore)
    finally:
        set_durable_suite(None)


def test_unknown_backend_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from hecate.core.config import settings

    monkeypatch.setattr(settings, "HECATE_DURABLE_BACKEND", "temporal", raising=False)
    set_durable_suite(None)
    try:
        with pytest.raises(ValueError, match="HECATE_DURABLE_BACKEND"):
            get_durable_suite()
    finally:
        set_durable_suite(None)


def test_injected_suite_override() -> None:
    injected = DurableSuite(
        store=InMemoryDurableTaskStore(),
        recorder=InMemoryControlCommandRecorder(),
        ledger=InMemoryActionLedger(),
        backend="stub",
        ledger_source="stub",
    )
    set_durable_suite(injected)
    try:
        assert get_durable_suite() is injected
    finally:
        set_durable_suite(None)


def test_sqlite_in_memory_engine_uses_static_pool() -> None:
    engine = create_sync_engine("sqlite://")
    from sqlalchemy.pool import StaticPool

    assert isinstance(engine.pool, StaticPool)
