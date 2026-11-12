"""Managed event upload: explicit confirmation, local gaps and per-run reports.

The channel talks to a scripted platform over ``httpx.MockTransport`` and
reads a real file-backed durable store, so cursor positions are observed
through the batches the platform receives — never through private state
alone. Local gaps are produced the way the durable log reports them: a
missing row inside a run's observed sequence range.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest_runner import write_profile
from hecate_durable.contracts.durable import IdempotencyKey, TaskLifecycleState
from hecate_durable.contracts.references import BackendRef
from hecate_durable.storage import SqlDurableStore
from hecate_durable.storage.models import EventRow
from hecate_runner import __main__ as cli
from hecate_runner.managed import (
    UPLOAD_GAP_EVIDENCE_KIND,
    ManagedChannel,
    managed_run_ref,
    managed_task_ref,
)
from sqlalchemy import delete


class ScriptedPlatform:
    """Answers the attempts/events routes and records every uploaded batch.

    Unscripted event uploads get the platform's real answer shape, with
    duplicates counted by event id like the projection's dedup index.
    """

    def __init__(self) -> None:
        self.batches: list[dict[str, Any]] = []
        self.answers: list[httpx.Response] = []
        self.unavailable_tasks: set[str] = set()
        self.stored: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path == "/managed/host/attempts":
            return httpx.Response(200, json={"local_run_ref": body["local_run_ref"], "platform_run_ref": {}})
        if request.url.path != "/managed/host/events":
            return httpx.Response(404)
        envelopes = body["envelopes"]
        task_id = body["local_task_ref"]["id"]
        self.batches.append(
            {
                "task": task_id,
                "run": envelopes[0]["run_ref"]["id"],
                "sequences": [envelope["source_sequence"] for envelope in envelopes],
                "event_ids": [envelope["event_id"] for envelope in envelopes],
            }
        )
        if task_id in self.unavailable_tasks:
            return httpx.Response(503)
        if self.answers:
            return self.answers.pop(0)
        fresh = [envelope["event_id"] for envelope in envelopes if envelope["event_id"] not in self.stored]
        self.stored.update(fresh)
        return httpx.Response(200, json={"projected": len(fresh), "skipped_duplicates": len(envelopes) - len(fresh)})

    def sequences_for(self, run_id: str) -> list[list[int]]:
        return [batch["sequences"] for batch in self.batches if batch["run"] == run_id]


@pytest.fixture
def host_store(tmp_path: Path):
    store = SqlDurableStore(f"sqlite:///{tmp_path / 'host.db'}")
    store.create_schema()
    yield store
    store.dispose()


def _channel(store: SqlDurableStore, platform: ScriptedPlatform, *, batch: int, recorder=None) -> ManagedChannel:
    channel = ManagedChannel(
        base_url="http://platform.test",
        workspace_id="ws",
        trust_root="root",
        host_id="host",
        secret=b"secret",
        issuer_domain="issuer",
        store=store,
        upload_batch=batch,
        transport=httpx.MockTransport(platform),
        gap_recorder=recorder,
    )
    channel._credential = {"test": "verified-by-transport"}
    return channel


def _managed_stream(store: SqlDurableStore, delivery: str, *, notes: int = 0) -> tuple[BackendRef, BackendRef]:
    """A managed task whose run stream holds ``3 + notes`` contiguous events."""

    task_ref, run_ref = managed_task_ref(delivery), managed_run_ref(delivery)
    store.submit_task(
        key=IdempotencyKey(key=f"managed-delivery:{delivery}", subject="host", workspace="ws", request_digest=delivery),
        task_ref=task_ref,
        run_ref=run_ref,
        input_payload={"goal": "upload"},
    )
    store.apply_task_state(task_ref, TaskLifecycleState.RUNNING)
    for index in range(notes):
        store.emit_event(task_ref, run_ref, event_type="progress_note", payload={"index": index})
    store.apply_task_state(task_ref, TaskLifecycleState.SUCCEEDED)
    return task_ref, run_ref


def _drop_events(store: SqlDurableStore, run_ref: BackendRef, sequences: set[int]) -> None:
    """Lose local rows inside the observed range (the durable log reports a gap)."""

    with store.session_factory() as session, session.begin():
        session.execute(
            delete(EventRow).where(
                EventRow.run_issuer == run_ref.issuer_domain,
                EventRow.run_id == run_ref.id,
                EventRow.source_sequence.in_(sequences),
            )
        )


def _stream(channel: ManagedChannel, run_ref: BackendRef) -> dict[str, Any]:
    return next(item for item in channel.upload_report()["streams"] if item["run_ref"] == run_ref.to_dict())


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(httpx.Response(503), id="unavailable"),
        pytest.param(httpx.Response(422, json={"detail": {"error": {"code": "invalid-request"}}}), id="rejected"),
        pytest.param(httpx.Response(200), id="empty-body"),
        pytest.param(httpx.Response(200, json={}), id="empty-object"),
        pytest.param(httpx.Response(200, json={"projected": 3}), id="missing-duplicates"),
        pytest.param(httpx.Response(200, json={"projected": 1, "skipped_duplicates": 1}), id="partial-count"),
        pytest.param(httpx.Response(200, json={"projected": 4, "skipped_duplicates": 0}), id="over-count"),
        pytest.param(httpx.Response(200, json={"projected": True, "skipped_duplicates": 2}), id="boolean-count"),
        pytest.param(httpx.Response(200, json={"projected": "3", "skipped_duplicates": 0}), id="string-count"),
        pytest.param(httpx.Response(200, json={"projected": -1, "skipped_duplicates": 4}), id="negative-count"),
        pytest.param(httpx.Response(200, json=[3, 0]), id="non-object"),
        pytest.param(httpx.Response(200, content=b"accepted"), id="not-json"),
        pytest.param(httpx.Response(302, json={"projected": 3, "skipped_duplicates": 0}), id="redirect-with-counts"),
    ],
)
async def test_only_a_complete_confirmation_advances_the_stream(host_store, answer) -> None:
    platform = ScriptedPlatform()
    _, run_ref = _managed_stream(host_store, "d1", notes=4)  # seven events
    channel = _channel(host_store, platform, batch=3)

    platform.answers.append(answer)
    assert await channel.upload_events() == 0
    assert channel.stats.upload_batches_acknowledged == 0
    assert channel.stats.upload_batches_failed + channel.stats.upload_batches_unconfirmed == 1
    assert _stream(channel, run_ref)["behind"] is True

    # The same position is resent; the platform already holds nothing from
    # the unconfirmed attempt here, so the resend projects all three.
    assert await channel.upload_events() == 3
    assert platform.sequences_for(run_ref.id) == [[1, 2, 3], [1, 2, 3]]
    assert platform.batches[0]["event_ids"] == platform.batches[1]["event_ids"]

    # Each later batch starts right after the confirmed position.
    assert await channel.upload_events() == 3
    assert await channel.upload_events() == 1
    assert platform.sequences_for(run_ref.id)[2:] == [[4, 5, 6], [7]]
    assert _stream(channel, run_ref)["state"] == "in_sync"
    assert await channel.upload_events() == 0
    assert len(platform.batches) == 4


async def test_lost_confirmation_resends_and_an_all_duplicate_answer_advances(host_store) -> None:
    platform = ScriptedPlatform()
    _, run_ref = _managed_stream(host_store, "d1", notes=4)
    channel = _channel(host_store, platform, batch=3)

    original = platform.__call__

    def commit_then_lose(request: httpx.Request) -> httpx.Response:
        response = original(request)  # the platform stores the batch...
        if request.url.path == "/managed/host/events" and len(platform.batches) == 1:
            return httpx.Response(503)  # ...and the answer never reaches the host
        return response

    channel._transport = httpx.MockTransport(commit_then_lose)
    assert await channel.upload_events() == 0
    assert channel.stats.upload_batches_failed == 1

    assert await channel.upload_events() == 0  # all duplicates: nothing new projected
    assert channel.stats.events_acknowledged_duplicate == 3
    assert channel.stats.upload_batches_acknowledged == 1
    assert await channel.upload_events() == 3
    assert platform.sequences_for(run_ref.id) == [[1, 2, 3], [1, 2, 3], [4, 5, 6]]


async def test_retry_keeps_batch_boundary_when_local_events_arrive(host_store) -> None:
    """An incomplete confirmation retries the original short batch first."""
    platform = ScriptedPlatform()
    task_ref, run_ref = _managed_stream(host_store, "d1")
    channel = _channel(host_store, platform, batch=5)
    platform.answers.append(httpx.Response(503))
    assert await channel.upload_events() == 0
    host_store.emit_event(task_ref, run_ref, event_type="progress_note", payload={"index": 1})
    assert await channel.upload_events() == 3
    assert _stream(channel, run_ref)["behind"] is True
    assert await channel.upload_events() == 1
    assert platform.sequences_for(run_ref.id) == [[1, 2, 3], [1, 2, 3], [4]]


async def test_malformed_rejection_does_not_stop_other_runs(host_store) -> None:
    """A malformed error envelope cannot interrupt an independent upload."""
    platform = ScriptedPlatform()
    _, first = _managed_stream(host_store, "d1")
    _, second = _managed_stream(host_store, "d2")
    channel = _channel(host_store, platform, batch=3)
    platform.answers.append(httpx.Response(403, json={"detail": {"error": None}}))
    assert await channel.upload_events() == 3
    assert _stream(channel, first)["acknowledged_sequence"] == 0
    assert _stream(channel, second)["acknowledged_sequence"] == 3


async def test_gap_only_page_holds_the_stream_and_records_the_range_once(host_store) -> None:
    platform = ScriptedPlatform()
    recorded: list[dict[str, Any]] = []
    task_ref, run_ref = _managed_stream(host_store, "d1", notes=2)  # five events
    _drop_events(host_store, run_ref, {2, 3})
    channel = _channel(host_store, platform, batch=1, recorder=recorded.append)

    assert await channel.upload_events() == 1  # sequence 1 precedes the gap
    for _ in range(3):
        assert await channel.upload_events() == 0  # a page holding only the gap marker

    assert platform.sequences_for(run_ref.id) == [[1]]
    status = _stream(channel, run_ref)
    assert status == {
        "task_ref": task_ref.to_dict(),
        "run_ref": run_ref.to_dict(),
        "state": "reconciliation_required",
        "acknowledged_sequence": 1,
        "behind": False,
        "gap": {"from_sequence": 2, "to_sequence": 3},
    }
    assert recorded == [
        {"task_ref": task_ref.to_dict(), "run_ref": run_ref.to_dict(), "from_sequence": 2, "to_sequence": 3}
    ]
    assert channel.stats.upload_gaps_detected == 1
    # The interrupted evidence stays local and unsent.
    page = host_store.read_events(run_ref, cursor=1, limit=10)
    assert [event.kind.value for event in page.events] == ["gap", "event", "event"]
    assert [event.source_sequence for event in page.events[1:]] == [4, 5]


async def test_mixed_page_uploads_only_the_prefix_before_a_gap(host_store) -> None:
    platform = ScriptedPlatform()
    _, run_ref = _managed_stream(host_store, "d1", notes=2)
    _drop_events(host_store, run_ref, {3})
    channel = _channel(host_store, platform, batch=10)

    assert await channel.upload_events() == 2
    assert await channel.upload_events() == 0
    assert platform.sequences_for(run_ref.id) == [[1, 2]]
    assert _stream(channel, run_ref)["acknowledged_sequence"] == 2
    assert _stream(channel, run_ref)["gap"] == {"from_sequence": 3, "to_sequence": 3}


async def test_gap_at_start_of_unconfirmed_batch_is_not_uploadable_backlog(host_store) -> None:
    """A pending batch whose first event is missing is held for reconciliation."""
    platform = ScriptedPlatform()
    _, run_ref = _managed_stream(host_store, "d1", notes=2)
    channel = _channel(host_store, platform, batch=3)
    platform.answers.append(httpx.Response(503))
    assert await channel.upload_events() == 0
    _drop_events(host_store, run_ref, {1, 2})

    assert await channel.upload_events() == 0
    assert platform.sequences_for(run_ref.id) == [[1, 2, 3]]
    assert channel.upload_summary()["streams_behind"] == 0
    assert channel.upload_summary()["streams_with_gap"] == 1


async def test_platform_behind_and_local_gap_are_reported_apart(host_store) -> None:
    platform = ScriptedPlatform()
    _, gapped = _managed_stream(host_store, "gapped", notes=1)
    healthy_task, healthy = _managed_stream(host_store, "healthy", notes=1)
    _drop_events(host_store, gapped, {2})
    platform.unavailable_tasks.add(healthy_task.id)
    channel = _channel(host_store, platform, batch=2)

    await channel.upload_events()
    await channel.upload_events()
    report = channel.upload_report()
    assert report["streams_behind"] == 1 and report["streams_with_gap"] == 1
    assert _stream(channel, healthy)["state"] == "behind"
    assert _stream(channel, gapped)["state"] == "reconciliation_required"
    assert channel.upload_summary() == {
        "streams": 2,
        "streams_behind": 1,
        "streams_with_gap": 1,
        "batches_acknowledged": 1,
        "batches_failed": 2,
        "batches_unconfirmed": 0,
        "gaps_detected": 1,
    }
    assert "managed-" not in json.dumps(channel.upload_summary())

    # The platform recovers: the healthy run catches up from its first event
    # and the gapped run stays held.
    platform.unavailable_tasks.clear()
    while _stream(channel, healthy)["state"] != "in_sync":
        await channel.upload_events()
    assert platform.sequences_for(healthy.id)[-2:] == [[1, 2], [3, 4]]
    assert platform.sequences_for(gapped.id) == [[1]]
    assert channel.upload_report()["streams_behind"] == 0
    assert channel.upload_report()["streams_with_gap"] == 1


async def test_gap_recording_failure_retries_without_blocking_uploads(host_store) -> None:
    platform = ScriptedPlatform()
    _, gapped = _managed_stream(host_store, "gapped")
    _, healthy = _managed_stream(host_store, "healthy")
    _drop_events(host_store, gapped, {2})
    attempts: list[dict[str, Any]] = []

    def flaky_recorder(detail: dict[str, Any]) -> None:
        attempts.append(detail)
        if len(attempts) == 1:
            raise OSError("evidence store unwritable")

    channel = _channel(host_store, platform, batch=5, recorder=flaky_recorder)
    await channel.upload_events()
    assert platform.sequences_for(healthy.id) == [[1, 2, 3]]
    await channel.upload_events()
    await channel.upload_events()
    assert len(attempts) == 2  # retried once, then recorded
    assert channel.stats.upload_gaps_detected == 1


async def test_cli_records_gaps_as_host_evidence_and_publishes_the_summary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RUNNER_SHUTDOWN_TOKEN", "shutdown")
    monkeypatch.setenv("RUNNER_LEASE_SECRET", "lease-secret")
    directory = write_profile(
        tmp_path, durable={"database_url": f"sqlite:///{tmp_path / 'host.db'}", "workspace": "ws"}
    )
    config_path = directory / "runner.json"
    config = json.loads(config_path.read_text())
    config["control_plane"] = {
        "base_url": "http://platform.test",
        "workspace_id": "00000000-0000-0000-0000-000000000001",
        "trust_root": "root",
        "host_id": "host",
        "issuer_domain": "issuer",
        "secret_ref": "env:RUNNER_LEASE_SECRET",
    }
    config_path.write_text(json.dumps(config))
    captured: dict[str, Any] = {}
    original_channel = cli.ManagedChannel

    def capture_channel(**kwargs):
        captured["channel"] = original_channel(**kwargs)
        return captured["channel"]

    class AssembledError(Exception):
        pass

    def capture_server(profile, engine, evidence, **kwargs):
        captured.update(evidence=evidence, durable=kwargs["durable"], managed_upload=kwargs.get("managed_upload"))
        raise AssembledError

    monkeypatch.setattr(cli, "ManagedChannel", capture_channel)
    monkeypatch.setattr(cli, "RunnerServer", capture_server)
    with pytest.raises(AssembledError):
        cli.main(["--profile", str(directory), "--business-api", "http://business.test"])
    try:
        channel = captured["channel"]
        assert captured["managed_upload"] == channel.upload_summary
        task_ref, run_ref = _managed_stream(captured["durable"].store, "d1")
        detail = {"task_ref": task_ref.to_dict(), "run_ref": run_ref.to_dict(), "from_sequence": 2, "to_sequence": 2}
        channel._gap_recorder(detail)
        [record] = captured["evidence"].query(kind=UPLOAD_GAP_EVIDENCE_KIND)
        assert (record.principal, record.ref, record.outcome) == ("host", run_ref.id, "gap")
        assert record.detail == detail
    finally:
        captured["durable"].store.dispose()
