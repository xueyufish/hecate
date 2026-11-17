"""REST write account constraint end to end on the real platform Controller.

A controller_loop task whose frozen version declares a ``rest_write``
profile is the only path that wires a gateway REST executor into the
production tool assembly. Every case here submits through the
authenticated task REST routes on loopback TCP, runs the production
Controller, ``ToolRegistry`` and ``RestToolExecutor`` against a real
loopback HTTP receiver, and judges the outcome from the receiver's own
request log — a refusal that never left for the network shows as a zero
request increment, before any upstream effect.

Covered: the allowed account receives exactly one POST with the target's
brokered credentials and no client-forged identity header; the other
hosted account, a missing account, a path-escape account value and a
query/body account override all refuse before send (zero increments on
every receiver); a rebound or renamed target and a tool moved to another
workspace refuse; a redirect gets no follow-up request; and a write the
receiver accepted but whose receipt was lost parks the task in
reconciliation without any blind retry.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_runtime.eventstore import InMemoryEventStore
from sqlalchemy.exc import OperationalError

from hecate.models.agent_version import AgentVersionModel
from hecate.models.gateway_target import GatewayTargetCreateSchema, GatewayTargetKind
from hecate.tools.gateway.converter import project_target_tools
from tests.scenarios.baseline import controller_host_platform as platform_driver
from tests.scenarios.baseline import controller_host_trusted_field_platform as tdriver
from tests.scenarios.baseline import controller_host_trusted_field_spec as tspec

TERMINAL = platform_driver.TERMINAL_STATES
WS = platform_driver.WS
WS2 = uuid.UUID("00000000-0000-0000-0000-0000000000d0")
ACCOUNT_A = "account-a"
ACCOUNT_B = "account-b"
TOOL = "crm__create_contact"


def _declaration(receiver: WriteReceiver, target_id: uuid.UUID) -> dict[str, Any]:
    return {
        "version": 1,
        "tool": TOOL,
        "target": "crm",
        "target_id": str(target_id),
        "base_url": receiver.url + "/v2",
        "path": "/accounts/{account_id}/contacts",
        "account_param": "account_id",
        "allowed_accounts": [ACCOUNT_A],
        "body_params": ["note"],
    }


class WriteReceiver(ThreadingHTTPServer):
    """One loopback business receiver with per-account request/write logs.

    ``mode`` switches the POST behavior: ``accept`` records and answers;
    ``forbid`` answers 403 without recording a write; ``lose_receipt``
    records the write and then drops the connection without a response;
    ``redirect`` answers 307 toward ``redirect_target``.
    """

    def __init__(self, *, mode: str = "accept") -> None:
        self.requests: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.mode = mode
        self.redirect_target = ""
        super().__init__(("127.0.0.1", 0), _WriteHandler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def serve(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread


class _WriteHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:
        """Keep synthetic credential headers out of test logs."""

    def _drain(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        record = {"path": self.path, "headers": dict(self.headers), "body": body.decode("utf-8", "replace")}
        self.server.requests.append(record)
        return record

    def do_POST(self) -> None:  # noqa: N802 - HTTP server callback
        server = self.server
        record = self._drain()
        if server.mode == "redirect":
            self.send_response(307)
            self.send_header("Location", server.redirect_target)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        parts = urlsplit(self.path)
        path_account = parts.path.split("/accounts/", 1)[-1].split("/", 1)[0]
        account = parse_qs(parts.query).get("account", [path_account])[0]
        if server.mode == "lose_receipt":
            server.writes.append({**record, "account": account})
            # The effect landed; the receipt never will.
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if server.mode == "forbid":
            payload = b'{"error": "unknown account"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        server.writes.append({**record, "account": account})
        payload = json.dumps({"status": "ok", "update_id": "REST-1"}).encode()
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextlib.asynccontextmanager
async def _receivers(*modes: str):
    servers = [WriteReceiver(mode=mode) for mode in modes]
    threads = [server.serve() for server in servers]
    try:
        yield servers
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()


async def _register_crm(db: Any, receiver: WriteReceiver, *, workspace_id: Any = WS) -> uuid.UUID:
    """Register the target and project its operations (the real path)."""

    from hecate.tools.gateway.targets import GatewayTargetService

    target = await GatewayTargetService(db).create_target(
        GatewayTargetCreateSchema(
            name="crm",
            kind=GatewayTargetKind.REST,
            workspace_id=workspace_id,
            base_url=receiver.url + "/v2",
            credentials={"headers": {"X-Api-Key": "synthetic-test-key", "X-HeCate-Actor": "credential-forged"}},
            spec={
                "openapi": "3.0.0",
                "paths": {
                    "/accounts/{account_id}/contacts": {
                        "post": {
                            "operationId": "create_contact",
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"note": {"type": "string"}},
                                        }
                                    }
                                }
                            },
                            "parameters": [
                                {
                                    "name": "account_id",
                                    "in": "path",
                                    "required": True,
                                    "schema": {"type": "string", "enum": [ACCOUNT_A, ACCOUNT_B]},
                                }
                            ],
                        }
                    },
                    "/redirect": {"post": {"operationId": "redirect_contact"}},
                },
            },
        )
    )
    report = await project_target_tools(db, target)
    assert TOOL in report.imported and "crm__redirect_contact" in report.imported
    await db.commit()
    return target.id


class _RoundProvider:
    """Deterministic provider playing (tool, arguments) rounds, then answering."""

    name = "deterministic-injection"

    def __init__(self, rounds: list[list[tuple[str, dict[str, Any]]]], *, before_first_call: Any = None) -> None:
        self.rounds = rounds
        self.seen = 0
        self.before_first_call = before_first_call

    async def complete(self, *, messages: list[dict], tools: list[dict]) -> dict[str, Any]:
        if self.seen == 0 and self.before_first_call is not None:
            await self.before_first_call()
        results = [tspec._tool_result(m.get("content")) for m in messages if m.get("role") == "tool"]
        if self.seen >= len(self.rounds):
            return self._answer("done")
        if any(result.get("status") == "ok" for result in results):
            return self._answer("done")
        # A refused write ends the task: answer once the refusal is visible.
        if any(result.get("status") == "rest_write_refused" for result in results):
            return self._answer("refused")
        round = self.rounds[self.seen]
        self.seen += 1
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": f"call-rest-{self.seen}-{index}",
                    "type": "function",
                    "function": {"name": tool, "arguments": args},
                }
                for index, (tool, args) in enumerate(round)
            ],
            "usage": None,
            "provider_model": self.name,
        }

    def _answer(self, text: str) -> dict[str, Any]:
        return {
            "content": json.dumps({"answer": text}),
            "tool_calls": [],
            "usage": None,
            "provider_model": self.name,
        }


class _RealAssemblyPatch:
    """Patch the model and event-store seams; keep the real tool assembly.

    ``build_tool_registry`` stays real: the REST executor appears only
    when the frozen declaration opts in, exactly as production wires it.
    """

    def __init__(self, provider: Any) -> None:
        self.gateway = platform_driver._RecordingGateway(provider)
        self._event_store = InMemoryEventStore()
        self._saved: dict[str, Any] = {}

    def patch(self) -> None:
        import hecate_llm.service as llm_service_module

        import hecate.core.composition.entry_assembly as entry_assembly

        self._saved = {
            "get_shared_event_store": entry_assembly.get_shared_event_store,
            "llm_service": llm_service_module.llm_service,
        }
        entry_assembly.get_shared_event_store = lambda: self._event_store
        llm_service_module.llm_service = self.gateway

    def unpatch(self) -> None:
        import hecate_llm.service as llm_service_module

        import hecate.core.composition.entry_assembly as entry_assembly

        entry_assembly.get_shared_event_store = self._saved["get_shared_event_store"]
        llm_service_module.llm_service = self._saved["llm_service"]


async def _run_case(
    tmp_path: Path,
    receiver: WriteReceiver,
    rounds: list[list[tuple[str, dict[str, Any]]]],
    *,
    with_profile: bool = True,
    mutate_db: Any = None,
    before_first_call: Any = None,
    env_label: str = "rest-write",
) -> tuple[Any, dict[str, Any], str | None, list[dict[str, Any]]]:
    """One task through the real assembly: seed, submit over REST, drive."""

    environment = platform_driver.PlatformEnvironment(tmp_path, f"{env_label}-{uuid.uuid4().hex[:8]}")

    async def before_model() -> None:
        async with environment.session_factory() as db:
            await before_first_call(db)

    patch = _RealAssemblyPatch(
        _RoundProvider(rounds, before_first_call=before_model if before_first_call is not None else None)
    )
    patch.patch()
    try:
        from sqlalchemy import select

        agent_id = await environment._seed(
            with_approval_rule=False,
            persona="rest-write verification",
            config_extra={"tools": [TOOL]},
        )
        async with environment.session_factory() as db:
            target_id = await _register_crm(db, receiver)
            snapshot = (
                await db.execute(select(AgentVersionModel).where(AgentVersionModel.agent_id == agent_id))
            ).scalar_one()
            if with_profile:
                snapshot.config_snapshot = {
                    **snapshot.config_snapshot,
                    "rest_write": _declaration(receiver, target_id),
                }
            await db.commit()
            if mutate_db is not None:
                await mutate_db(db)  # the mutator drives the AsyncSession itself
        worker = environment.make_worker()
        async with tdriver._serve_task_routes(environment, worker) as (client, relay):
            response = await client.post(
                "/api/tasks",
                json={
                    "goal": "rest write account verification",
                    "agent_id": str(agent_id),
                    "input": {
                        "messages": [{"role": "user", "content": "create the contact"}],
                        "controller_loop": True,
                    },
                    "result_delivery": "rest_inbox",
                },
            )
            response.raise_for_status()
            refs = response.json()
            task_uuid = uuid.UUID(refs["task_id"])
            rest_run_id = refs["run_id"]
            store = environment.store
            task_ref = BackendRef(RefKind.TASK, "hecate", str(task_uuid))
            deadline = asyncio.get_running_loop().time() + 90
            record = None
            while asyncio.get_running_loop().time() < deadline:
                record = await asyncio.to_thread(store.get_task_state, task_ref)
                if record.lifecycle_state.value in TERMINAL:
                    break
                with contextlib.suppress(OperationalError):
                    await worker.reconcile()
                await relay.pump_once()
                await asyncio.sleep(0.05)
            assert record is not None and record.lifecycle_state.value in TERMINAL, (record or {}).extra
            inbox_kind = None
            if record.lifecycle_state.value == "succeeded":
                async with asyncio.timeout(30):
                    inbox_kind = await _read_inbox_kind(client, relay, task_uuid, rest_run_id)
            elif record.lifecycle_state.value == "reconciliation_required":
                await environment.make_worker().reconcile()
            ledger = await asyncio.to_thread(store.list_run_actions, BackendRef(RefKind.RUN, "hecate", rest_run_id))
            return record, (record.extra or {}), inbox_kind, ledger
    finally:
        patch.unpatch()
        await environment.close()


async def _read_inbox_kind(client, relay, task_uuid: uuid.UUID, run_id: str) -> str:
    while True:
        await relay.pump_once()
        response = await client.get(f"/api/tasks/{task_uuid}/result-inbox")
        response.raise_for_status()
        for notice in response.json()["notifications"]:
            if notice["task_id"] == str(task_uuid):
                assert notice["run_id"] == run_id
                return notice["type"]
        await asyncio.sleep(0.05)


async def test_the_allowed_account_receives_exactly_one_post(tmp_path: Path) -> None:
    async with _receivers("accept") as (receiver,):
        record, extra, inbox_kind, ledger = await _run_case(
            tmp_path, receiver, [[(TOOL, {"account_id": ACCOUNT_A, "note": ACCOUNT_B})]]
        )
    assert record.lifecycle_state.value == "succeeded"
    assert inbox_kind == "result_ready"
    result = (extra or {}).get("result") or {}
    assert result.get("status") == "succeeded"
    # Exactly one POST reached the receiver, carrying the brokered
    # credentials and no client-forged identity header.
    assert len(receiver.requests) == 1
    headers = httpx.Headers(receiver.requests[0]["headers"])
    assert headers["x-api-key"] == "synthetic-test-key"
    assert headers.get("x-hecate-actor") is None
    assert receiver.requests[0]["path"] == "/v2/accounts/account-a/contacts"
    assert json.loads(receiver.requests[0]["body"]) == {"note": ACCOUNT_B}
    assert len(receiver.writes) == 1 and receiver.writes[0]["account"] == ACCOUNT_A
    # The action ledger recorded the executed REST write.
    write_actions = [a for a in ledger if (a.get("intent") or {}).get("action_name") == TOOL]
    assert len(write_actions) == 1
    assert write_actions[0]["last_outcome"]["outcome"] == "succeeded"
    landed_payload = write_actions[0]["result_payload"]
    if isinstance(landed_payload, str):
        landed_payload = json.loads(landed_payload)
    assert landed_payload["update_id"] == "REST-1"


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({"account_id": ACCOUNT_B, "note": "x"}, "account_denied"),
        ({}, "account_missing"),
        ({"account_id": "../admin", "note": "x"}, "account_denied"),
        ({"account_id": ACCOUNT_A, "note": {"account_id": ACCOUNT_B}}, "account_override_attempt"),
        ({"account_id": ACCOUNT_A, "account": ACCOUNT_B}, "account_override_attempt"),
    ],
    ids=["other-account", "missing-account", "path-escape", "body-override", "query-override"],
)
async def test_refused_writes_never_reach_any_receiver(tmp_path: Path, arguments: dict, reason: str) -> None:
    async with _receivers("accept", "accept") as (primary, mirror):
        record, extra, _inbox, ledger = await _run_case(tmp_path, primary, [[(TOOL, arguments)]])
    assert record.lifecycle_state.value == "succeeded"  # the model answers after the refusal
    result = (extra or {}).get("result") or {}
    assert result.get("status") == "succeeded"
    # Zero requests left for the network: the refusal is the action's
    # recorded outcome, before any business effect on any receiver.
    assert primary.requests == [] and primary.writes == []
    assert mirror.requests == [] and mirror.writes == []
    write_actions = [a for a in ledger if (a.get("intent") or {}).get("action_name") == TOOL]
    assert len(write_actions) == 1
    payload = write_actions[0]["result_payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["status"] == "rest_write_refused"
    assert payload["reason"] == reason


@pytest.mark.parametrize("change", ["rename", "base_path", "operation"])
async def test_a_rebound_target_refuses_before_send(tmp_path: Path, change: str) -> None:
    async with _receivers("accept", "accept") as (primary, mirror):

        async def rebind(db: Any) -> None:
            from sqlalchemy import select, update

            from hecate.models.gateway_target import GatewayTargetModel
            from hecate.models.tool import ToolModel

            if change == "operation":
                tool = (await db.execute(select(ToolModel).where(ToolModel.name == TOOL))).scalar_one()
                parameters = dict(tool.parameters)
                parameters["x-gateway"] = {
                    **parameters["x-gateway"],
                    "path": "/accounts/account-b/delegated/{account_id}/contacts",
                }
                tool.parameters = parameters
            else:
                fields = (
                    {"name": "crm-moved"}
                    if change == "rename"
                    else {"base_url": primary.url + "/v2/accounts/account-b"}
                )
                await db.execute(update(GatewayTargetModel).where(GatewayTargetModel.name == "crm").values(**fields))
            await db.commit()

        record, extra, _inbox, ledger = await _run_case(
            tmp_path, primary, [[(TOOL, {"account_id": ACCOUNT_A})]], mutate_db=rebind
        )
    assert record.lifecycle_state.value == "failed"
    assert ((extra or {}).get("result") or {}).get("error_category") == "rest_write_invalid"
    assert primary.requests == [] and mirror.requests == []
    assert all((a.get("intent") or {}).get("action_name") != TOOL for a in ledger)


async def test_target_drift_during_the_model_call_refuses_at_execution(tmp_path: Path) -> None:
    async with _receivers("accept", "accept") as (primary, mirror):

        async def declare_success_schema(db: Any) -> None:
            from sqlalchemy import update

            from hecate.models.tool import ToolModel

            await db.execute(
                update(ToolModel).where(ToolModel.name == TOOL).values(returns={"required": ["update_id"]})
            )
            await db.commit()

        async def rebind(db: Any) -> None:
            from sqlalchemy import update

            from hecate.models.gateway_target import GatewayTargetModel

            await db.execute(
                update(GatewayTargetModel).where(GatewayTargetModel.name == "crm").values(base_url=mirror.url + "/v2")
            )
            await db.commit()

        _, _, _, ledger = await _run_case(
            tmp_path,
            primary,
            [[(TOOL, {"account_id": ACCOUNT_A})]],
            mutate_db=declare_success_schema,
            before_first_call=rebind,
        )
    assert primary.requests == [] and mirror.requests == []
    refusal = ledger[0]["result_payload"]
    assert refusal["status"] == "rest_write_refused" and refusal["reason"] == "target_rebound"


async def test_the_write_ignores_an_ambient_http_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with _receivers("accept", "accept") as (primary, mirror):

        async def enable_proxy(_db: Any) -> None:
            # The task client is already connected when the model starts.
            monkeypatch.setenv("HTTP_PROXY", mirror.url)
            monkeypatch.setenv("NO_PROXY", "")

        record, _, _, _ = await _run_case(
            tmp_path, primary, [[(TOOL, {"account_id": ACCOUNT_A})]], before_first_call=enable_proxy
        )
    assert record.lifecycle_state.value == "succeeded"
    assert len(primary.requests) == 1 and mirror.requests == []


async def test_a_controller_without_the_profile_cannot_send_rest_writes(tmp_path: Path) -> None:
    async with _receivers("accept") as (receiver,):
        await _run_case(tmp_path, receiver, [[(TOOL, {"account_id": ACCOUNT_A})]], with_profile=False)
    assert receiver.requests == [] and receiver.writes == []


async def test_a_tool_moved_to_another_workspace_refuses(tmp_path: Path) -> None:
    async with _receivers("accept") as (receiver,):

        async def move_tool(db: Any) -> None:
            from sqlalchemy import update

            from hecate.models.tool import ToolModel

            await db.execute(update(ToolModel).where(ToolModel.name == TOOL).values(workspace_id=WS2))
            await db.commit()
            _ = WS2

        record, extra, _inbox, ledger = await _run_case(
            tmp_path, receiver, [[(TOOL, {"account_id": ACCOUNT_A})]], mutate_db=move_tool
        )
    assert record.lifecycle_state.value == "failed"
    assert ((extra or {}).get("result") or {}).get("error_category") == "rest_write_invalid"
    assert receiver.requests == []


async def test_a_redirect_gets_no_followup_request(tmp_path: Path) -> None:
    async with _receivers("redirect", "accept") as (primary, mirror):
        primary.redirect_target = mirror.url + "/v2/accounts/account-a/contacts"
        record, extra, _inbox, ledger = await _run_case(tmp_path, primary, [[(TOOL, {"account_id": ACCOUNT_A})]])
    # The task parks for reconciliation (the redirect is a structured
    # unknown outcome) and the second receiver saw nothing.
    assert record.lifecycle_state.value == "reconciliation_required"
    assert len(primary.requests) == 1
    assert mirror.requests == []
    write_actions = [a for a in ledger if (a.get("intent") or {}).get("action_name") == TOOL]
    assert write_actions and write_actions[0]["pending_reconciliation"] is True


async def test_a_lost_receipt_reconciles_without_a_retry(tmp_path: Path) -> None:
    async with _receivers("lose_receipt") as (receiver,):
        record, extra, _inbox, ledger = await _run_case(tmp_path, receiver, [[(TOOL, {"account_id": ACCOUNT_A})]])
    # The business effect landed exactly once; the missing receipt parks
    # the task for reconciliation and no retry ever re-sends the POST.
    assert record.lifecycle_state.value == "reconciliation_required"
    assert ((extra or {}).get("reconciliation_reason")) is not None
    assert len(receiver.requests) == 1 and len(receiver.writes) == 1
    write_actions = [a for a in ledger if (a.get("intent") or {}).get("action_name") == TOOL]
    assert len(write_actions) == 1 and write_actions[0]["pending_reconciliation"] is True
