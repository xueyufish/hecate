"""Isolated databases and authoritative leases for accepted-decision tests."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from hecate_durable.contracts.durable import IdempotencyKey
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.lease import task_dispatch_lease_key
from hecate_runtime.accepted_decision import DecisionOwnership, RunRef
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


class DecisionClock:
    """Controllable time for the existing LeaseManager, not another lease registry."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 120) -> None:
        """Expire existing leases deterministically."""
        self.now += timedelta(seconds=seconds)


@contextmanager
def decision_database(tmp_path, postgres_url: str = ""):
    """Create and clean only a fresh test-owned database/schema."""
    clock = DecisionClock()
    admin = None
    if postgres_url:
        postgres_url = (
            make_url(postgres_url).update_query_dict({"connect_timeout": "5"}).render_as_string(hide_password=False)
        )
    schema = None
    if postgres_url:
        admin = create_engine(postgres_url)
        schema = "decision_review_" + uuid.uuid4().hex
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = (
            make_url(postgres_url)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    else:
        url = f"sqlite:///{tmp_path / 'decision.db'}"
    backend = SqlDurableStore(url, lease_clock=clock)
    backend.create_schema()
    try:
        yield backend, clock, url
    finally:
        backend.dispose()
        if admin is not None:
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def prepare_decision_run(backend, run_ref: RunRef, holder: str = "worker") -> DecisionOwnership:
    """Register the authoritative Task/Run link and obtain a real live lease."""
    backend.submit_task(
        key=IdempotencyKey(
            key=f"{run_ref.issuer_domain}:{run_ref.run_id}", subject="app", workspace="ws", request_digest="a" * 64
        ),
        task_ref=BackendRef(RefKind.TASK, run_ref.issuer_domain, "task-" + run_ref.run_id),
        run_ref=BackendRef(RefKind.RUN, run_ref.issuer_domain, run_ref.run_id),
        input_payload={},
    )
    handle = backend.leases.acquire(
        task_dispatch_lease_key(run_ref.issuer_domain, "task-" + run_ref.run_id), holder, 60
    )
    assert handle is not None
    return DecisionOwnership(handle.lease_key, handle.fencing_token, handle.holder)
