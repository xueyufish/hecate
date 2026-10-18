"""Real PostgreSQL races, process death/recovery and isolated migration checks."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from _decision_helpers import decision_database, prepare_decision_run
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
from hecate_durable.storage import SqlDurableStore
from hecate_runtime.accepted_decision import (
    FINAL_ANSWER_SCHEMA_VERSION,
    AcceptedAction,
    AcceptedDecisionRecord,
    DecisionConflictError,
    RunRef,
    canonical_decision_digest,
)
from hecate_runtime.decision_step import DecisionProposal, DecisionResponder, DecisionStep
from sqlalchemy import inspect, text

POSTGRES_URL = os.environ.get("DURABLE_TEST_POSTGRES_URL", "")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="Real PostgreSQL acceptance NOT run; skipping is not passing")


@pytest.fixture
def pg(tmp_path):
    with decision_database(tmp_path, POSTGRES_URL) as database:
        yield database


def _record(run, answer="a"):
    return AcceptedDecisionRecord(
        run, "step", {"config": "v1"}, {"answer": answer}, (AcceptedAction("key", "biz_tool", {"q": [1, 2]}),)
    )


async def test_final_answer_persists_and_recovers_from_another_connection(pg):
    backend, _, url = pg
    run = RunRef("pg", "final-answer")
    owner = prepare_decision_run(backend, run)
    record = AcceptedDecisionRecord(
        run, "step", {"config": "v1"}, {"text": "the answer"}, (), schema_version=FINAL_ANSWER_SCHEMA_VERSION
    )
    committed = await SqlAcceptedDecisionStore(backend).commit(record, owner)
    assert committed.schema_version == FINAL_ANSWER_SCHEMA_VERSION
    assert committed.actions == ()
    assert committed.accepted_output == {"text": "the answer"}
    assert committed.content_digest == canonical_decision_digest(record)

    other_backend = SqlDurableStore(url)
    try:
        found = await SqlAcceptedDecisionStore(other_backend).lookup(run, "step")
        assert found == committed  # same text, digest, version and empty action set
        assert found.content_digest == canonical_decision_digest(found)
        assert found.accepted_output == {"text": "the answer"}
        with backend.engine.connect() as connection:
            row = connection.execute(
                text("SELECT schema_version, actions FROM durable_accepted_decision WHERE run_id = 'final-answer'")
            ).one()
        assert row.schema_version == FINAL_ANSWER_SCHEMA_VERSION
        assert row.actions == []
    finally:
        other_backend.dispose()


@pytest.mark.parametrize("same_content", [False, True])
async def test_concurrent_connections_arbitrate_one_version(pg, same_content):
    backend, clock, url = pg
    run = RunRef("pg", "race")
    owner = prepare_decision_run(backend, run)
    other_backend = SqlDurableStore(url, lease_clock=clock)
    barrier = threading.Barrier(2)

    class RacingStore(SqlAcceptedDecisionStore):
        def _commit_sync(self, record, ownership):
            barrier.wait(timeout=20)
            return super()._commit_sync(record, ownership)

    try:
        stores = [RacingStore(backend), RacingStore(other_backend)]
        results = await asyncio.gather(
            stores[0].commit(_record(run), owner),
            stores[1].commit(_record(run, "a" if same_content else "b"), owner),
            return_exceptions=True,
        )
        accepted = [result for result in results if isinstance(result, AcceptedDecisionRecord)]
        conflicts = [result for result in results if isinstance(result, DecisionConflictError)]
        assert len(accepted) == (2 if same_content else 1), results
        assert len(conflicts) == (0 if same_content else 1), results
        found = await stores[0].lookup(run, "step")
        assert found == await stores[1].lookup(run, "step")
        if same_content:
            assert accepted[0] == accepted[1]
        with backend.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM durable_accepted_decision")) == 1
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM durable_event_log "
                        "WHERE envelope->'payload'->>'event_type' = 'agent_decision_accepted'"
                    )
                )
                == 1
            )
    finally:
        other_backend.dispose()


async def test_component_concurrent_candidates_do_not_replace_or_retry(pg):
    backend, _, _ = pg
    run = RunRef("pg", "component-race")
    owner = prepare_decision_run(backend, run)
    ready = asyncio.Event()
    calls = 0

    class RacingResponder(DecisionResponder):
        async def respond(self, run_ref, decision_step_id, input_refs):
            nonlocal calls
            calls += 1
            answer = calls
            if calls == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=10)
            return DecisionProposal({"answer": answer}, _record(run).actions)

    step = DecisionStep(SqlAcceptedDecisionStore(backend), RacingResponder())
    results = await asyncio.gather(
        *[
            step.execute(run_ref=run, decision_step_id="step", input_refs={"config": "v1"}, ownership=owner)
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert calls == 2
    assert sum(isinstance(result, DecisionConflictError) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1


def test_actual_writer_process_dies_then_another_process_recovers(pg):
    _, _, url = pg
    root = Path(__file__).resolve().parents[3]
    environment = os.environ.copy()
    environment["DECISION_REVIEW_URL"] = url
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(root / "packages/hecate-runtime/src"),
            str(root / "packages/hecate-durable/src"),
            str(Path(__file__).parent),
            environment.get("PYTHONPATH", ""),
        ]
    )
    code = """
import asyncio, json, os, sys
from _decision_helpers import DecisionClock, prepare_decision_run
from hecate_durable.storage import SqlDurableStore
from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
from hecate_runtime.accepted_decision import AcceptedAction, RunRef
from hecate_runtime.decision_step import DecisionProposal, DecisionResponder, DecisionStep
class Responder(DecisionResponder):
    def __init__(self): self.calls = 0
    async def respond(self, *args):
        self.calls += 1
        if sys.argv[1] == 'read': raise AssertionError('recovery must not call model')
        actions = (AcceptedAction('stable-action','biz_tool',{'nested':[1,{'q':2}]}),)
        return DecisionProposal({'answer':'original'}, actions)
async def main():
    store = SqlDurableStore(os.environ['DECISION_REVIEW_URL'], lease_clock=DecisionClock())
    run = RunRef('pg', 'real-process')
    owner = prepare_decision_run(store, run) if sys.argv[1] == 'write' else None
    responder = Responder()
    outcome = await DecisionStep(SqlAcceptedDecisionStore(store), responder).execute(
        run_ref=run, decision_step_id='step', input_refs={'config':'v1'}, ownership=owner)
    print(json.dumps({'record':outcome.record.to_dict(),'resumed':outcome.resumed,'calls':responder.calls}), flush=True)
asyncio.run(main())
os._exit(0)
"""

    def run_child(mode):
        result = subprocess.run(  # noqa: S603 - trusted interpreter, fixed test code, no shell
            [sys.executable, "-c", code, mode], env=environment, capture_output=True, text=True, timeout=45
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    written = run_child("write")
    recovered = run_child("read")
    assert written["record"] == recovered["record"]
    assert written["calls"] == 1 and not written["resumed"]
    assert recovered["calls"] == 0 and recovered["resumed"]


def test_migration_matches_metadata_and_refuses_destructive_downgrade(pg):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from hecate_durable.storage.models import AcceptedDecisionRow

    backend, _, _ = pg
    path = Path(__file__).resolve().parents[3] / "alembic/versions/b5c6d7e8f9a0_add_accepted_decision_table.py"
    spec = importlib.util.spec_from_file_location("decision_migration", path)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    with backend.engine.begin() as connection:
        # This schema belongs only to this test and contains no accepted decisions.
        AcceptedDecisionRow.__table__.drop(connection)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            revision.upgrade()
            connection.execute(
                AcceptedDecisionRow.__table__.insert().values(
                    id="preserved",
                    issuer_domain="pg",
                    run_id="migration",
                    decision_step_id="step",
                    schema_version=1,
                    input_refs={"config": "v1"},
                    accepted_output={"answer": "original"},
                    actions=[{"action_key": "stable", "tool": "biz", "arguments": {}, "side_effect_class": "unknown"}],
                    content_digest="a" * 64,
                    fence_holder="worker",
                    fence_token=1,
                    accepted_at="2026-01-01T00:00:00Z",
                )
            )
            with pytest.raises(NotImplementedError, match="durable facts"):
                revision.downgrade()
            assert connection.scalar(text("SELECT count(*) FROM durable_accepted_decision")) == 1
        columns = {column["name"]: column for column in inspect(connection).get_columns("durable_accepted_decision")}
        expected = AcceptedDecisionRow.__table__
        assert set(columns) == set(expected.columns.keys())
        for name, column in expected.columns.items():
            assert columns[name]["nullable"] == column.nullable
            assert str(columns[name]["type"]) == str(column.type)
        assert any(
            constraint["column_names"] == ["issuer_domain", "run_id", "decision_step_id"]
            for constraint in inspect(connection).get_unique_constraints("durable_accepted_decision")
        )
