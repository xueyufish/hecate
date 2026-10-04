"""Composition binding for the durable-execution seams (step6 platform track).

Single process-wide switch point between the seam implementations:

- ``stub`` (default) — the Phase 0 InMemory doubles. Suitable for
  development and tests; carries no durability guarantee.
- ``postgres`` — this change's platform adapters over the platform tables
  (``PostgresDurableTaskStore`` / ``PostgresControlCommandRecorder``).
  The action ledger stays on the InMemory stub until the
  ``durable-execution-core`` worktree lands its production ledger; the
  binding reports that provenance so the reconciliation API can label
  ledger-backed sections honestly.

When the durable-execution-core implementation merges, its suite
replaces the members here — the API and service layers only ever see the
seam ABCs, so no consumer changes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from hecate.core.config import settings
from hecate.execution.durable import ActionLedger, ControlCommandRecorder, DurableTaskStore
from hecate.execution.platform_durable import PlatformDurableFactory
from hecate.execution.stub_durable import InMemoryActionLedger

STUB_BACKEND = "stub"
POSTGRES_BACKEND = "postgres"


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
        factory = PlatformDurableFactory(settings.DATABASE_URL)
        return DurableSuite(
            store=factory.task_store(),
            recorder=factory.command_recorder(),
            ledger=InMemoryActionLedger(),
            backend=backend,
            # Ledger stays on the stub until durable-execution-core lands.
            ledger_source="stub",
        )
    if backend != STUB_BACKEND:
        raise ValueError(f"unknown HECATE_DURABLE_BACKEND {backend!r}; expected 'stub' or 'postgres'")
    from hecate.execution.stub_durable import InMemoryControlCommandRecorder, InMemoryDurableTaskStore

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
