"""The observe-only Slack event entry: boundaries proven end to end.

Every test uses synthetic signatures over loopback (real TCP for the
delivery-matrix tests, in-process ASGI for the unit-shaped negatives)
against a real database session. The assertions prove the isolation
contract: the entry never enqueues on the IM bus, never touches Task or
Run state, and never calls an adapter — the only thing it can write is
its own ledger. The existing Slack chat webhook is asserted unchanged in
its own suite (``test_channels_webhook.py``, ``test_slack_channel.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hecate.channel.api.v1.slack_events import (
    DISPOSITION_DUPLICATE,
    DISPOSITION_OBSERVED,
    purge_expired_observations,
)
from hecate.channel.api.v1.slack_events import (
    router as slack_events_router,
)
from hecate.channel.slack_signature import SIGNATURE_REPLAY_WINDOW_SECONDS, verify_slack_signature
from hecate.core.config import settings
from hecate.core.database import get_db
from hecate.enterprise.auth.token import create_access_token
from hecate.models.run import RunModel
from hecate.models.slack_events import SlackEventObservationModel
from hecate.models.task import TaskModel
from hecate.models.user import UserModel
from hecate.models.workspace import WorkspaceModel
from hecate.models.workspace_member import WorkspaceMemberModel, WorkspaceRole

WORKSPACE_ID = uuid.uuid4()
OTHER_WORKSPACE = uuid.uuid4()
ORG_ID = uuid.uuid4()
APP_ID = "A0OBSERVED"
TEAM_ID = "T0OBSERVED"
CHANNEL_ID = "C0OBSERVED"
SECRET = "synthetic-signing-secret"


def _slack_sign(secret: str, body: bytes, *, timestamp: int | None = None) -> dict[str, str]:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(secret.encode("utf-8"), b"v0:" + ts.encode("utf-8") + b":" + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Signature": f"v0={digest}", "X-Slack-Request-Timestamp": ts}


def _app_mention(
    event_id: str | None = None, *, envelope: dict[str, Any] | None = None, **event_overrides: Any
) -> dict[str, Any]:
    event = {
        "type": "app_mention",
        "channel": CHANNEL_ID,
        "user": "U0HUMAN",
        "text": "<@U0BOT> hello there secret-content",
        "ts": "1700000000.000100",
        **event_overrides,
    }
    payload = {
        "type": "event_callback",
        "api_app_id": APP_ID,
        "team_id": TEAM_ID,
        "event_id": event_id or f"Ev{uuid.uuid4().hex}",
        "event": event,
    }
    if envelope:
        payload.update(envelope)
    return payload


class RecordingBus:
    """A spy bus: the observation entry must never touch one."""

    def __init__(self) -> None:
        self.enqueued: list[Any] = []

    async def enqueue(self, **kwargs: Any) -> None:
        self.enqueued.append(kwargs)


@pytest.fixture
def observation_settings(monkeypatch: pytest.MonkeyPatch):
    """Enable the entry with the synthetic profile; returns a mutator."""

    def _set(**overrides: Any) -> None:
        for key, value in overrides.items():
            monkeypatch.setattr(settings, key, value)

    _set(
        SLACK_EVENT_OBSERVATION_ENABLED=True,
        SLACK_EVENT_OBSERVATION_SIGNING_SECRET=SecretStr(SECRET),
        SLACK_EVENT_OBSERVATION_APP_ID=APP_ID,
        SLACK_EVENT_OBSERVATION_TEAM_ID=TEAM_ID,
        SLACK_EVENT_OBSERVATION_CHANNEL_ID=CHANNEL_ID,
        SLACK_EVENT_OBSERVATION_WORKSPACE_ID=str(WORKSPACE_ID),
    )
    return _set


@pytest.fixture
async def ledger(tmp_path):
    """A file SQLite database for TCP, concurrent sessions and reopen tests."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    try:
        async with engine.begin() as conn:
            for model in (WorkspaceModel, UserModel, WorkspaceMemberModel, SlackEventObservationModel):
                await conn.run_sync(model.__table__.create)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await _seed_workspaces(factory)
        yield factory
    finally:
        await engine.dispose()


async def _seed_workspaces(factory):
    async with factory() as db:
        for workspace in (WORKSPACE_ID, OTHER_WORKSPACE):
            db.add(WorkspaceModel(id=workspace, org_id=ORG_ID, name="Observation test", slug=str(workspace)))
        await db.commit()


async def _operator_token(factory, workspace, role):
    user = uuid.uuid4()
    async with factory() as db:
        db.add(UserModel(id=user, email=f"{user}@example.test", hashed_password="unused"))
        db.add(WorkspaceMemberModel(user_id=user, workspace_id=workspace, role=role))
        await db.commit()
    return create_access_token(user, ORG_ID, workspace, role.value)


@pytest.fixture
async def client(observation_settings, ledger):
    """An app carrying ONLY the observation router plus the spy bus."""
    app = FastAPI()
    app.include_router(slack_events_router)

    async def _override_db():
        async with ledger() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    bus = RecordingBus()
    app.state.im_message_bus = bus
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, bus, ledger, app


async def _rows(ledger) -> list[SlackEventObservationModel]:
    async with ledger() as session:
        result = await session.execute(
            select(SlackEventObservationModel).order_by(SlackEventObservationModel.created_at)
        )
        return list(result.scalars().all())


async def _post(http: AsyncClient, payload: dict[str, Any], *, secret: str = SECRET, **header_overrides: Any):
    body = json.dumps(payload).encode("utf-8")
    headers = _slack_sign(secret, body) | header_overrides
    return await http.post("/v1/slack-events", content=body, headers=headers)


# --- the shared signature verifier -------------------------------------------


def test_signature_verifier_rejects_expired_and_forged() -> None:
    body = b"{}"
    good = _slack_sign(SECRET, body)
    assert verify_slack_signature(SECRET, good, body)[0] == 200
    stale = _slack_sign(SECRET, body, timestamp=int(time.time()) - SIGNATURE_REPLAY_WINDOW_SECONDS - 10)
    assert verify_slack_signature(SECRET, stale, body)[0] == 401
    forged = _slack_sign("another-secret", body)
    assert verify_slack_signature(SECRET, forged, body)[0] == 401
    assert verify_slack_signature(SECRET, {}, body)[0] == 401
    assert verify_slack_signature(SECRET, good, b'{"changed":true}')[0] == 401
    assert verify_slack_signature(SECRET, good | {"X-Slack-Signature": "v0=\u00ff"}, body)[0] == 401


async def test_slack_adapter_delegates_to_the_shared_verifier() -> None:
    """One scheme, two consumers: the chat adapter applies the same check."""
    from hecate_channel_slack.channel import SlackChannel

    adapter = SlackChannel(bot_token="xoxb-synthetic", signing_secret=SECRET, token_verification_enabled=False)
    body = json.dumps({"type": "url_verification", "challenge": "c"}).encode("utf-8")
    assert (await adapter.verify_webhook(_slack_sign(SECRET, body), body))[0] == 200
    assert (await adapter.verify_webhook(_slack_sign("wrong", body), body))[0] == 401


# --- disabled by default and when switched off --------------------------------


async def test_disabled_entry_answers_404_and_writes_nothing(client, observation_settings):
    http, bus, ledger, app = client
    observation_settings(SLACK_EVENT_OBSERVATION_ENABLED=False)
    body = json.dumps(_app_mention()).encode("utf-8")
    resp = await http.post("/v1/slack-events", content=body, headers=_slack_sign(SECRET, body))
    assert resp.status_code == 404
    assert await _rows(ledger) == []
    assert bus.enqueued == []


async def test_missing_secret_config_is_a_misconfigured_503(client, observation_settings):
    http, bus, ledger, app = client
    observation_settings(SLACK_EVENT_OBSERVATION_SIGNING_SECRET=SecretStr(""))
    body = json.dumps(_app_mention()).encode("utf-8")
    resp = await http.post("/v1/slack-events", content=body, headers=_slack_sign(SECRET, body))
    assert resp.status_code == 503
    assert await _rows(ledger) == []
    assert bus.enqueued == []


# --- url_verification: challenge echoed, never ledgered -----------------------


async def test_url_verification_echoes_challenge_and_writes_nothing(client):
    http, bus, ledger, app = client
    payload = {
        "type": "url_verification",
        "token": "ignored-legacy-token",
        "challenge": "synthetic-challenge-token",
    }
    resp = await _post(http, payload)
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "synthetic-challenge-token"}
    assert await _rows(ledger) == []
    assert bus.enqueued == []


async def test_url_verification_refuses_other_apps_key(client):
    http, bus, ledger, app = client
    payload = {"type": "url_verification", "challenge": "c"}
    resp = await _post(http, payload, secret="synthetic-other-app-key")
    assert resp.status_code == 401
    assert await _rows(ledger) == []


# --- legal app_mention: observed, confirmed only after commit -----------------


async def test_legal_app_mention_is_observed_with_minimal_identity(client):
    http, bus, ledger, app = client
    event_id = f"Ev{uuid.uuid4().hex}"
    resp = await _post(http, _app_mention(event_id))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "observation": DISPOSITION_OBSERVED}
    (row,) = await _rows(ledger)
    assert row.disposition == DISPOSITION_OBSERVED
    assert row.event_id == event_id
    assert row.app_id == APP_ID and row.team_id == TEAM_ID and row.channel_id == CHANNEL_ID
    assert row.workspace_id == WORKSPACE_ID
    # The ledger holds identifiers only — never the message text.
    assert "secret-content" not in json.dumps(row.to_public_dict())
    assert bus.enqueued == []


async def test_reopened_sqlite_ledger_redelivery_is_a_duplicate(client, tmp_path):
    """A brand-new engine on the persisted ledger answers duplicate."""
    http, bus, ledger, app = client
    event_id = f"Ev{uuid.uuid4().hex}"
    first = await _post(http, _app_mention(event_id))
    assert first.json()["observation"] == DISPOSITION_OBSERVED

    from fastapi import FastAPI as App

    new_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    factory = async_sessionmaker(new_engine, expire_on_commit=False)
    restarted = App()
    restarted.include_router(slack_events_router)

    async def _override():
        async with factory() as session:
            yield session

    restarted.dependency_overrides[get_db] = _override
    restarted.state.im_message_bus = RecordingBus()
    async with AsyncClient(transport=ASGITransport(app=restarted), base_url="http://test") as http2:
        again = await _post(http2, _app_mention(event_id))
    assert again.json()["observation"] == DISPOSITION_DUPLICATE
    await new_engine.dispose()
    rows = await _rows(ledger)
    assert len(rows) == 1 and rows[0].disposition == DISPOSITION_OBSERVED
    assert bus.enqueued == []


# --- identity and shape negatives ---------------------------------------------


async def test_wrong_app_is_rejected_and_capped_recorded(client):
    http, bus, ledger, app = client
    resp = await _post(http, _app_mention(envelope={"api_app_id": "A0OTHER"}))
    assert resp.status_code == 403
    (row,) = await _rows(ledger)
    assert row.disposition == "rejected_foreign_source"
    assert bus.enqueued == []


@pytest.mark.parametrize(
    "envelope",
    [{"team_id": "T0OTHER"}, {"team": "T0OTHER"}, {"is_ext_shared_channel": True}],
)
async def test_wrong_team_is_rejected(client, envelope):
    http, _bus, ledger, _app = client
    resp = await _post(http, _app_mention(envelope=envelope))
    assert resp.status_code == 403
    assert (await _rows(ledger))[0].disposition == "rejected_foreign_source"


async def test_wrong_channel_is_rejected(client):
    http, bus, ledger, app = client
    resp = await _post(http, _app_mention(channel="C0OTHER"))
    assert resp.status_code == 403
    assert (await _rows(ledger))[0].disposition == "rejected_foreign_source"


async def test_non_app_mention_event_type_is_rejected(client):
    http, bus, ledger, app = client
    payload = _app_mention()
    payload["event"]["type"] = "message.im"
    resp = await _post(http, payload)
    assert resp.status_code == 403
    assert (await _rows(ledger))[0].disposition == "rejected_event_type"


async def test_bot_authored_mention_is_observed_and_dropped(client):
    http, bus, ledger, app = client
    payload = _app_mention(user="U0SELFBOT", bot_id="B0SELFBOT")
    resp = await _post(http, payload)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "observation": "rejected_bot_event"}
    assert (await _rows(ledger))[0].disposition == "rejected_bot_event"
    assert bus.enqueued == []


@pytest.mark.parametrize(
    "binding",
    [
        "",
        "bogus",
        # Well-formed but nonexistent: collection must be deterministic
        # across xdist workers, so this literal cannot be generated.
        "496ff0d8-a385-4856-8ac9-6585da14e809",
    ],
)
async def test_invalid_workspace_binding_refuses_callback(client, observation_settings, binding):
    http, bus, ledger, _app = client
    observation_settings(SLACK_EVENT_OBSERVATION_WORKSPACE_ID=binding)
    response = await _post(http, _app_mention())
    assert response.status_code == 503
    assert await _rows(ledger) == []
    assert bus.enqueued == []


async def test_refusal_cannot_claim_the_event_key_or_persist_payload_fields(client):
    http, _bus, ledger, _app = client
    event_id = "EvSameId"
    refused = await _post(http, _app_mention(event_id, envelope={"api_app_id": "foreign-content"}))
    assert refused.status_code == 403
    accepted = await _post(http, _app_mention(event_id))
    assert accepted.json()["observation"] == DISPOSITION_OBSERVED
    rows = {row.disposition: row for row in await _rows(ledger)}
    rejected = rows["rejected_foreign_source"]
    assert rejected.event_id != event_id
    assert not (rejected.app_id or rejected.team_id or rejected.channel_id or rejected.event_type)
    assert rows[DISPOSITION_OBSERVED].event_id == event_id


# --- bounds: body cap, refusal cap, malformed, signature negatives ------------


async def test_oversized_body_is_refused_before_signature(client):
    http, bus, ledger, app = client
    resp = await http.post(
        "/v1/slack-events",
        content=b"x" * 70000,
        headers={"content-length": "70000"},
    )
    assert resp.status_code == 413
    assert await _rows(ledger) == []


async def test_forged_and_expired_signatures_are_refused(client):
    http, bus, ledger, app = client
    body = json.dumps(_app_mention()).encode("utf-8")
    forged = await http.post("/v1/slack-events", content=body, headers=_slack_sign("other-secret", body))
    assert forged.status_code == 401
    stale = await http.post(
        "/v1/slack-events",
        content=body,
        headers=_slack_sign(SECRET, body, timestamp=int(time.time()) - SIGNATURE_REPLAY_WINDOW_SECONDS - 30),
    )
    assert stale.status_code == 401
    assert await _rows(ledger) == []
    assert bus.enqueued == []


async def test_malformed_body_is_recorded_and_refused(client):
    http, bus, ledger, app = client
    body = b"this is not json"
    resp = await http.post("/v1/slack-events", content=body, headers=_slack_sign(SECRET, body))
    assert resp.status_code == 400
    assert (await _rows(ledger))[0].disposition == "rejected_malformed"
    assert bus.enqueued == []


async def test_refusal_cap_stops_recording_but_not_answering(client, observation_settings):
    http, bus, ledger, app = client
    observation_settings(SLACK_EVENT_OBSERVATION_REJECT_LOG_LIMIT=3)
    results = await asyncio.gather(*(_post(http, _app_mention(envelope={"api_app_id": "A0OTHER"})) for _ in range(12)))
    assert {response.status_code for response in results} == {403}
    (row,) = await _rows(ledger)
    assert row.delivery_count == 3
    assert bus.enqueued == []


# --- zero side effects: the whole matrix asserts the isolation contract -------


async def test_concurrent_redelivery_lands_exactly_one_observed_row(client):
    """Two concurrent same-id deliveries: one observed, one duplicate."""
    http, bus, ledger, app = client
    event_id = f"Ev{uuid.uuid4().hex}"
    results = await asyncio.gather(
        _post(http, _app_mention(event_id)),
        _post(http, _app_mention(event_id)),
    )
    observations = sorted(resp.json().get("observation") for resp in results)
    assert observations == [DISPOSITION_DUPLICATE, DISPOSITION_OBSERVED]
    rows = await _rows(ledger)
    assert len(rows) == 1 and rows[0].disposition == DISPOSITION_OBSERVED
    assert bus.enqueued == []


@pytest.mark.parametrize("error_type", [IntegrityError, OperationalError])
async def test_ledger_write_failure_refuses_confirmation(client, monkeypatch, caplog, error_type):
    """An unrelated constraint violation or failed commit is retryable."""
    http, bus, ledger, app = client

    async def _exploding_db():
        async with ledger() as session:

            async def fail():
                raise error_type("synthetic SQL", {"private": "secret-content"}, RuntimeError("synthetic outage"))

            with monkeypatch.context() as patch:
                patch.setattr(session, "commit", fail)
                yield session

    app.dependency_overrides[get_db] = _exploding_db
    resp = await _post(http, _app_mention())
    assert resp.status_code == 500
    assert resp.json() == {"error": "ledger unavailable"}
    assert await _rows(ledger) == []
    assert "secret-content" not in caplog.text
    assert bus.enqueued == []


# --- retention ----------------------------------------------------------------


async def test_purge_expired_observations_removes_only_expired_rows(client, observation_settings):
    http, bus, ledger, app = client
    await _post(http, _app_mention())
    observation_settings(SLACK_EVENT_OBSERVATION_RETENTION_DAYS=30)
    async with ledger() as session:
        removed = await purge_expired_observations(session)
        assert removed == 0  # a fresh row is inside the window
        stale = SlackEventObservationModel(
            source="slack",
            event_id="Ev-stale",
            workspace_id=WORKSPACE_ID,
            disposition="observed",
            created_at=datetime.now(UTC) - timedelta(days=45),
            updated_at=datetime.now(UTC) - timedelta(days=45),
        )
        session.add(stale)
        await session.commit()
        removed = await purge_expired_observations(session, now=datetime.now(UTC))
        assert removed == 1
    remaining = await _rows(ledger)
    assert len(remaining) == 1
    assert remaining[0].event_id != "Ev-stale"
    assert bus.enqueued == []


# --- the read-only ops summary endpoint ---------------------------------------


async def test_summary_requires_live_bound_workspace_admin(client, observation_settings):
    http, bus, ledger, _app = client
    path = "/v1/slack-events/observations/summary"
    assert (await _post(http, _app_mention())).status_code == 200
    assert (await http.get(path)).status_code == 401

    admin = await _operator_token(ledger, WORKSPACE_ID, WorkspaceRole.ADMIN)
    viewer = await _operator_token(ledger, WORKSPACE_ID, WorkspaceRole.VIEWER)
    foreign = await _operator_token(ledger, OTHER_WORKSPACE, WorkspaceRole.ADMIN)
    assert (await http.get(path, headers={"Authorization": f"Bearer {viewer}"})).status_code == 403
    hidden = await http.get(path, headers={"Authorization": f"Bearer {foreign}"})
    assert hidden.status_code == 404
    assert str(WORKSPACE_ID) not in hidden.text

    async with ledger() as db:
        db.add(SlackEventObservationModel(workspace_id=OTHER_WORKSPACE, event_id="EvOther", disposition="observed"))
        await db.commit()
    observation_settings(SLACK_EVENT_OBSERVATION_ENABLED=False)
    headers = {"Authorization": f"Bearer {admin}"}
    response = await http.get(path, headers=headers)
    assert response.status_code == 200
    assert response.json()["counts"] == {DISPOSITION_OBSERVED: 1}
    assert response.json()["source"] == "slack" and response.json()["enabled"] is False
    assert response.json()["workspace_id"] == str(WORKSPACE_ID)
    async with ledger() as db:
        membership = await db.scalar(
            select(WorkspaceMemberModel).where(
                WorkspaceMemberModel.workspace_id == WORKSPACE_ID, WorkspaceMemberModel.role == WorkspaceRole.ADMIN
            )
        )
        assert membership is not None
        membership.deleted = True
        await db.commit()
    assert (await http.get(path, headers=headers)).status_code == 401
    assert bus.enqueued == []


# --- loopback TCP: the entry behaves identically over a real socket ----------


@pytest.mark.timeout(120)
async def test_delivery_matrix_over_loopback_tcp(client, observation_settings):
    """Exercise signature, streaming bounds and out-of-order events over TCP."""
    from tests.scenarios.tools.managed_platform import serve_over_tcp, stop_tcp_server

    _, bus, ledger, app = client
    server, serving, base_url = await serve_over_tcp(app)
    try:
        async with AsyncClient(base_url=base_url, timeout=15) as tcp:

            async def oversized():
                yield b"x" * 4097

            observation_settings(SLACK_EVENT_OBSERVATION_MAX_BODY_BYTES=4096)
            response = await tcp.post("/v1/slack-events", content=oversized())
            assert response.status_code == 413
            old = time.time() - 60
            newer = _app_mention("EvNewer", envelope={"event_time": int(time.time())})
            older = _app_mention("EvOlder", envelope={"event_time": int(old)})
            for payload in (newer, older):
                response = await _post(tcp, payload)
                assert response.json() == {"ok": True, "observation": DISPOSITION_OBSERVED}
            assert (await _post(tcp, newer)).json()["observation"] == DISPOSITION_DUPLICATE
            assert (await _post(tcp, _app_mention(envelope={"api_app_id": "A0OTHER"}))).status_code == 403

            observation_settings(SLACK_EVENT_OBSERVATION_ENABLED=False)
            assert (await _post(tcp, _app_mention())).status_code == 404
    finally:
        await stop_tcp_server(server, serving)
    rows = await _rows(ledger)
    assert {row.event_id for row in rows if row.disposition == DISPOSITION_OBSERVED} == {"EvNewer", "EvOlder"}
    assert len(rows) == 3
    assert bus.enqueued == []


@pytest.mark.timeout(120)
async def test_postgres_migrated_entry_concurrency_and_restart(observation_settings, monkeypatch, tmp_path):
    """Exercise the mounted platform app against an independently migrated PostgreSQL database."""
    url = os.getenv("HECATE_SLACK_OBSERVATION_TEST_POSTGRES")
    if not url:
        pytest.skip("set HECATE_SLACK_OBSERVATION_TEST_POSTGRES to a migrated scratch PostgreSQL database")

    import hecate.core.middleware.audit as audit
    from hecate.main import app as platform_app
    from tests.scenarios.tools.managed_platform import serve_over_tcp, stop_tcp_server

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    bus = RecordingBus()
    audit_queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    async def test_db():
        async with factory() as session:
            yield session

    monkeypatch.setitem(platform_app.dependency_overrides, get_db, test_db)
    monkeypatch.setattr(platform_app.state, "im_message_bus", bus, raising=False)
    monkeypatch.setattr(audit, "_audit_queue", audit_queue)
    observation_settings(SLACK_EVENT_OBSERVATION_REJECT_LOG_LIMIT=3)
    event_id = f"Ev{uuid.uuid4().hex}"
    try:
        await _seed_workspaces(factory)
        payload = _app_mention(event_id)
        server, serving, base_url = await serve_over_tcp(platform_app)
        try:
            async with AsyncClient(base_url=base_url, timeout=15) as http:
                foreign = _app_mention(event_id, envelope={"api_app_id": "A0OTHER"})
                mixed = await asyncio.gather(
                    *(_post(http, payload) for _ in range(8)), *(_post(http, foreign) for _ in range(8))
                )
                assert [response.status_code for response in mixed] == [200] * 8 + [403] * 8
                dispositions = [response.json()["observation"] for response in mixed[:8]]
                assert dispositions.count(DISPOSITION_OBSERVED) == 1
                assert dispositions.count(DISPOSITION_DUPLICATE) == 7
        finally:
            await stop_tcp_server(server, serving)

        await engine.dispose()
        ready_path = tmp_path / "ready"
        child = """
import asyncio
import sys
from pathlib import Path

from hecate.main import app
from tests.scenarios.tools.managed_platform import serve_over_tcp

async def main():
    _, serving, url = await serve_over_tcp(app)
    ready = Path(sys.argv[1])
    temp = ready.with_suffix(".tmp")
    temp.write_text(url, encoding="utf-8")
    temp.replace(ready)
    await serving

asyncio.run(main())
"""
        with (
            (tmp_path / "postgres-slack-restart.log").open("w", encoding="utf-8") as log,
            subprocess.Popen(
                [sys.executable, "-c", child, str(ready_path)],
                env={
                    **os.environ,
                    "DATABASE_URL": url,
                    "SLACK_EVENT_OBSERVATION_ENABLED": "true",
                    "SLACK_EVENT_OBSERVATION_SIGNING_SECRET": SECRET,
                    "SLACK_EVENT_OBSERVATION_APP_ID": APP_ID,
                    "SLACK_EVENT_OBSERVATION_TEAM_ID": TEAM_ID,
                    "SLACK_EVENT_OBSERVATION_CHANNEL_ID": CHANNEL_ID,
                    "SLACK_EVENT_OBSERVATION_WORKSPACE_ID": str(WORKSPACE_ID),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
            ) as process,
        ):
            try:
                async with asyncio.timeout(35):
                    while not ready_path.exists():
                        assert process.poll() is None, "restarted platform exited before bind"
                        await asyncio.sleep(0.05)
                async with AsyncClient(base_url=ready_path.read_text(encoding="utf-8"), timeout=15) as http:
                    assert (await _post(http, payload)).json()["observation"] == DISPOSITION_DUPLICATE
            finally:
                if process.poll() is None:
                    process.terminate()

        async with factory() as session:
            by_disposition = dict(
                (
                    await session.execute(
                        select(SlackEventObservationModel.disposition, SlackEventObservationModel.delivery_count).where(
                            SlackEventObservationModel.workspace_id == WORKSPACE_ID
                        )
                    )
                ).all()
            )
            assert by_disposition == {DISPOSITION_OBSERVED: 1, "rejected_foreign_source": 3}
            assert await session.scalar(select(func.count()).select_from(TaskModel)) == 0
            assert await session.scalar(select(func.count()).select_from(RunModel)) == 0
        assert bus.enqueued == []
        assert audit_queue.empty()
    finally:
        await engine.dispose()
