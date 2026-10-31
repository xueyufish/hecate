"""Runner host driver for the Controller two-host baseline.

Drives the real runner controller-loop assembly: ``ExecutionEngine`` over a
durable ``SqlDurableStore`` with the declared ``controller_loop`` binding.
The model is the profile's declared HTTP endpoint — a local server that
forwards to the provider under test and converts its reply into the runner
endpoint protocol (content/tool_calls/usage); the provider, not the
collector, selects every action. Business tools reach the same synthetic
data through the runner's business-API dispatch. Approval waits park through
the real ``ApprovalWaitSignal`` path and resume through ``apply_wake`` +
``engine.resume`` after a full host rebuild over the same persisted profile
and SQLite state.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hecate_runtime.manifest import sha256_hex

from tests.scenarios.baseline import controller_host_spec as spec

PRINCIPAL = "app-reader"
DOMAINS = ("domain_a",)
APPROVAL_REF = "approval-controller-host-baseline-1"
CASE_TIME_BUDGET_SECONDS = 120.0

READ_ENTRY_CONTENT = json.dumps({"kind": "entry", "tools": [spec.READ_TOOL, spec.WRITE_TOOL]}).encode()
WRITE_TOOL_SCHEMA_BYTES = json.dumps(spec.TOOL_SCHEMAS[spec.WRITE_TOOL]).encode()


def _write_profile(profile_dir: Path, *, write_permission: str, database_url: str) -> None:
    """Materialize a valid controller-loop profile for one case."""

    entry_name = "agents/summary/main.json"
    (profile_dir / "files" / "agents/summary").mkdir(parents=True, exist_ok=True)
    (profile_dir / "files" / entry_name).write_bytes(READ_ENTRY_CONTENT)
    schema_name = "tools/submit_inventory_update.schema.json"
    (profile_dir / "files" / "tools").mkdir(parents=True, exist_ok=True)
    (profile_dir / "files" / schema_name).write_bytes(WRITE_TOOL_SCHEMA_BYTES)
    files = [
        {"path": entry_name, "sha256": sha256_hex(READ_ENTRY_CONTENT), "size": len(READ_ENTRY_CONTENT)},
        {"path": schema_name, "sha256": sha256_hex(WRITE_TOOL_SCHEMA_BYTES), "size": len(WRITE_TOOL_SCHEMA_BYTES)},
    ]
    manifest = {
        "manifest_version": "1",
        "contract_version": "0.1",
        "backend_type": "pregel",
        "backend_compat_version": "0.1",
        "entry": entry_name,
        "files": files,
        "tools": [
            {
                "name": spec.WRITE_TOOL,
                "schema_ref": schema_name,
                "permission": write_permission,
            }
        ],
    }
    (profile_dir / "agent-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    config = {
        "host": "127.0.0.1",
        "port": 0,
        "evidence_dir": "evidence",
        "model": {"backend": "stub"},
        "tool_allowlist": [spec.READ_TOOL, spec.WRITE_TOOL],
        "shutdown_token_ref": "env:RUNNER_SHUTDOWN_TOKEN",
        "durable": {"database_url": database_url, "workspace": "baseline-ws"},
        "controller_loop": True,
    }
    (profile_dir / "runner.json").write_text(json.dumps(config), encoding="utf-8")
    (profile_dir / "identity.json").write_text(
        json.dumps(
            {
                "identities": [
                    {
                        "principal": PRINCIPAL,
                        "role": "read_only",
                        "domains": ["domain_a"],
                        "credential": "file:secrets/app-reader-token",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (profile_dir / "secrets").mkdir(exist_ok=True)
    (profile_dir / "secrets/app-reader-token").write_text("reader-secret-token", encoding="utf-8")


class _BaselineBusinessApi:
    """Synthetic business boundary behind the runner's dispatch mapping."""

    def __init__(self, case: str) -> None:
        self.case = case
        self.calls: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []
        self.side_effects = 0
        self.write_attempts = 0
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()

    async def __call__(self, tool_name: str, arguments: dict, principal: str, domains: list[str]) -> dict:
        self.calls.append({"tool": tool_name, "arguments": dict(arguments), "principal": principal})
        if tool_name == spec.READ_TOOL:
            self.read_started.set()
            if self.case == "cancelled":
                # Hold the read in flight so the collector's cancel command
                # lands mid-loop through the real cooperative channel.
                await self.release_read.wait()
            if self.case == "unknown_outcome":
                self.receipts.append(
                    {"tool": tool_name, "arguments": dict(arguments), "status": "outcome_unknown", "content": None}
                )
                return {"status": "unknown", "detail": "synthetic lost read receipt"}
            self.receipts.append(
                {"tool": tool_name, "arguments": dict(arguments), "status": "succeeded", "content": dict(spec.MATERIAL)}
            )
            return {"status": "ok", "result": dict(spec.MATERIAL)}
        if tool_name == spec.WRITE_TOOL:
            self.write_attempts += 1
            self.side_effects += 1
            self.receipts.append(
                {
                    "tool": tool_name,
                    "arguments": dict(arguments),
                    "status": "succeeded",
                    "content": {"status": "ok", "update_id": spec.RECEIPT_ID},
                }
            )
            return {"status": "ok", "result": {"status": "ok", "update_id": spec.RECEIPT_ID}}
        return {"status": "unsupported", "detail": "unknown tool"}


class _EndpointModelServer:
    """Local HTTP model endpoint forwarding to the provider under test.

    The server translates between the runner's endpoint protocol and the
    provider only — it never chooses content or tool calls.
    """

    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.phase = "initial"
        self.rounds: list[dict[str, Any]] = []
        self._handlers: set[asyncio.Task] = set()
        self._server: asyncio.AbstractServer | None = None
        self.url: str = ""

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/invoke"
        return self.url

    async def stop(self) -> None:
        server = self._server
        if self._server is not None:
            self._server.close()
            self._server = None
        handlers = tuple(self._handlers)
        for task in handlers:
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        if server is not None:
            await server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        handler = asyncio.current_task()
        self._handlers.add(handler)
        try:
            await reader.readline()
            headers: dict[str, str] = {}
            while True:
                header = await reader.readline()
                if header in (b"\r\n", b"\n", b""):
                    break
                name, _, value = header.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            request = json.loads(body.decode("utf-8"))
            round_record: dict[str, Any] = {
                "index": len(self.rounds),
                "phase": self.phase,
                "input_messages": request.get("messages") or [],
                "tools_enabled": bool(request.get("tools")),
                "input_tools": request.get("tools") or [],
            }
            started = time.monotonic()
            try:
                reply = await self.provider.complete(
                    messages=request.get("messages") or [], tools=request.get("tools") or []
                )
            except Exception as exc:  # noqa: BLE001 — the failure is the observation
                self.rounds.append(
                    {
                        **round_record,
                        "tool_calls": [],
                        "final": False,
                        "content": None,
                        "usage": None,
                        "error_type": type(exc).__name__,
                        "latency_ms": (time.monotonic() - started) * 1000,
                    }
                )
                raise
            self.rounds.append(
                {
                    **round_record,
                    "tool_calls": reply.get("tool_calls") or [],
                    "final": not reply.get("tool_calls"),
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "content": reply.get("content"),
                    "usage": reply.get("usage"),
                    "provider_model": reply.get("provider_model"),
                }
            )
            answer: dict[str, Any] = {"content": reply.get("content"), "tool_calls": reply.get("tool_calls") or []}
            if reply.get("usage"):
                answer["usage"] = reply["usage"]
            payload = json.dumps(answer, default=str).encode("utf-8")
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except Exception:  # noqa: BLE001, S110 — connection-level failures end the exchange
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self._handlers.discard(handler)


class RunnerEnvironment:
    """One runner assembly: durable store + engine (no HTTP surface)."""

    def __init__(self, profile_dir: Path, api: _BaselineBusinessApi) -> None:
        from hecate_durable.storage import SqlDurableStore
        from hecate_runner.durable import DurableRuntime
        from hecate_runner.engine import ExecutionEngine
        from hecate_runner.evidence import EvidenceStore
        from hecate_runner.profile import load_profile

        self.profile = load_profile(profile_dir)
        self.evidence = EvidenceStore(self.profile.config.evidence_dir)
        self.store = SqlDurableStore(self.profile.config.durable.database_url)
        self.store.create_schema()
        self.durable = DurableRuntime(self.store, workspace=self.profile.config.durable.workspace)
        self.api = api
        from hecate_runner.local_approvals import DurableLocalApprovalLedger, make_local_approval_gate

        self.approvals = DurableLocalApprovalLedger(self.store.session_factory, approvers=("baseline-approver",))
        self.engine = ExecutionEngine(
            self.profile,
            self.evidence,
            api,
            durable=self.durable,
            approval_gate=make_local_approval_gate(self.store.session_factory, approvers=("baseline-approver",)),
        )

    async def close(self) -> None:
        await self.engine.close()
        self.store.dispose()


def _run_input() -> dict[str, Any]:
    return {
        "input": {"prompt": spec.USER_PROMPT},
        "controller": {"max_turns": 8},
        "output_schema": spec.OUTPUT_SCHEMA,
        "harness": {"sections": [{"type": "persona", "content": spec.SYSTEM_PROMPT}]},
    }


async def _wait_terminal(environment: RunnerEnvironment, run_id: str, timeout: float) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        state = environment.engine.get_state(run_id)
        if state is not None and state.status in {"succeeded", "failed", "cancelled", "reconciliation_required"}:
            return state
        await asyncio.sleep(0.05)
    state = environment.engine.get_state(run_id)
    raise TimeoutError(f"run never reached a terminal state; last={state.status if state else None}")


def _observation(
    environment: RunnerEnvironment,
    server: _EndpointModelServer,
    api: _BaselineBusinessApi,
    run_id: str,
    started: float,
) -> dict[str, Any]:
    state = environment.engine.get_state(run_id)
    actions = environment.durable.store.list_run_actions(state.run_ref) if state is not None else []
    return {
        "run_id": run_id,
        "task_id": str(state.task_ref.id) if state is not None and state.task_ref is not None else None,
        "run_ref": str(state.run_ref.id) if state is not None and state.run_ref is not None else None,
        "rounds": list(server.rounds),
        "tool_calls": list(api.calls),
        "tool_receipts": list(api.receipts),
        "ledger_actions": [
            {
                "action_key": action.get("action_key") or (action.get("intent") or {}).get("action_key"),
                "arguments_digest": (action.get("intent") or {}).get("arguments_digest"),
                "side_effect_class": (action.get("intent") or {}).get("side_effect_class"),
                "pending_reconciliation": bool(action.get("pending_reconciliation")),
                "active_claim": bool(action.get("active_claim")),
            }
            for action in actions
        ],
        "events": [
            {"type": item.get("type"), "text": item.get("text")}
            for item in (state.events if state is not None else [])
            if isinstance(item, dict)
        ],
        "latency_ms_total": (time.monotonic() - started) * 1000,
    }


async def run_runner_case(case: str, provider: Any, root: Path) -> dict[str, Any]:
    """Run one baseline case through the real runner controller assembly."""

    os.environ.setdefault("RUNNER_SHUTDOWN_TOKEN", "baseline-shutdown-token")
    from hecate_governance.usage import BudgetGate
    from hecate_runner.budget import DurableBudgetLedger

    profile_dir = root / f"runner-profile-{case}-{uuid.uuid4().hex[:8]}"
    write_permission = "approval_required" if case in ("approval_resume", "cancelled") else "write"
    _write_profile(profile_dir, write_permission=write_permission, database_url=f"sqlite:///{profile_dir / 'host.db'}")
    api = _BaselineBusinessApi(case)
    server = _EndpointModelServer(provider)
    started = time.monotonic()
    observation: dict[str, Any] = {}
    try:
        async with asyncio.timeout(CASE_TIME_BUDGET_SECONDS):
            await server.start()
            config = json.loads((profile_dir / "runner.json").read_text(encoding="utf-8"))
            config["model"] = {"backend": "endpoint", "endpoint": server.url}
            (profile_dir / "runner.json").write_text(json.dumps(config), encoding="utf-8")

            environment = RunnerEnvironment(profile_dir, api)
            if case == "budget_exhausted":
                # The durable model-call budget ledger refuses the reservation
                # before the first model request leaves the host.
                environment.engine._budget_gate = BudgetGate(
                    DurableBudgetLedger(environment.store.session_factory, limit=1)
                )
            initial_closed = False
            try:
                run_id, _state = await environment.engine.submit(PRINCIPAL, _run_input(), DOMAINS)
                if case == "cancelled":
                    read_wait = asyncio.create_task(api.read_started.wait())
                    execution = tuple(environment.engine._tasks)
                    try:
                        await asyncio.wait((read_wait, *execution), return_when=asyncio.FIRST_COMPLETED)
                        if not api.read_started.is_set():
                            raise RuntimeError("cancel scenario ended before the read action started")
                    finally:
                        read_wait.cancel()
                        await asyncio.gather(read_wait, return_exceptions=True)
                    cancelled = environment.engine.request_cancel(run_id)
                    observation["cancel_receipt"] = f"requested={cancelled}"
                    api.release_read.set()
                    final = await _wait_terminal(environment, run_id, CASE_TIME_BUDGET_SECONDS)
                elif case in ("approval_resume",):
                    parked = await _wait_parked(environment, run_id)
                    task_ref = parked.task_ref
                    record = environment.durable.store.get_task_state(task_ref)
                    wait = (record.extra or {}).get("wait") or {}
                    observation["approval"] = {
                        "parked": parked.status == "waiting_approval",
                        "wait_token": wait.get("wait_token"),
                        "approval_ref": APPROVAL_REF,
                        "write_attempts_before_park": api.write_attempts,
                        "decisions": [{"approved": True, "result": "wake grant applied"}],
                    }
                    observation.update(_observation(environment, server, api, run_id, started))
                    pre_run_ref = observation.get("run_ref")
                    pre_task_id = observation.get("task_id")

                    # Host rebuild: close the engine, dispose the store, and
                    # reassemble a brand-new runner over the same profile and
                    # persisted SQLite state, then wake + resume the SAME run.
                    server.phase = "post_rebuild"
                    await environment.close()
                    initial_closed = True
                    rebuilt = RunnerEnvironment(profile_dir, api)
                    try:
                        from hecate_governance.action import target_resources_for
                        from hecate_governance.approval import ApprovalBinding, ApprovalJudgment
                        from hecate_governance.principal import ActionPrincipal, PrincipalSource
                        from hecate_runtime.action_ledger import tool_arguments_digest

                        contract = wait.get("contract_ref") or {}
                        arguments = contract.get("arguments") or spec.EXPECTED_WRITE
                        principal = ActionPrincipal.from_verified_claims(
                            {"sub": PRINCIPAL},
                            source=PrincipalSource.RUNNER_LOCAL,
                            domains=DOMAINS,
                        )
                        await rebuilt.approvals.save(
                            ApprovalBinding(
                                approval_ref=APPROVAL_REF,
                                principal_fingerprint=principal.fingerprint(),
                                tool_name=spec.WRITE_TOOL,
                                arguments_digest=tool_arguments_digest(arguments),
                                target_resources=target_resources_for(arguments),
                                domains=DOMAINS,
                                task_scope=task_ref.id,
                                issued_at=datetime.now(UTC),
                                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                                judgment=ApprovalJudgment.APPROVED,
                                decided_by="baseline-approver",
                                reason="baseline synthetic approver decision",
                            )
                        )
                        receipt, reason = rebuilt.durable.apply_wake(
                            command_id=f"wake-{uuid.uuid4()}",
                            kind="resume",
                            issuer=PRINCIPAL,
                            task_ref=task_ref,
                            wait_token=wait["wait_token"],
                            approval_ref=APPROVAL_REF,
                            keep_run_linkage=True,
                        )
                        assert reason is None, f"wake refused: {reason}"
                        assert receipt.state.value == "applied", receipt.state.value
                        resumed = rebuilt.engine.resume(task_ref, rebuilt.durable.store.run_for_task(task_ref), {})
                        assert resumed == run_id, f"resume re-homed the run: {resumed}"
                        final = await _wait_terminal(rebuilt, run_id, CASE_TIME_BUDGET_SECONDS)
                        observation.update(_observation(rebuilt, server, api, run_id, started))
                        binding = await rebuilt.approvals.get(APPROVAL_REF)
                        observation["approval"]["authority_consumed"] = bool(
                            binding and binding.consumed_by_invocation_id == contract.get("invocation_id")
                        )
                        observation["rebuild"] = {
                            "performed": True,
                            "scope": "engine + durable store reassembled over the persisted profile sqlite state",
                            "same_run": observation.get("run_ref") == pre_run_ref
                            and observation.get("task_id") == pre_task_id,
                            "run_id": run_id,
                        }
                        post_resume_rounds = [r for r in observation["rounds"] if r.get("phase") == "post_rebuild"]
                        observation["post_resume_model_input_has_receipt"] = any(
                            spec.RECEIPT_ID in json.dumps(round.get("input_messages"), default=str)
                            for round in post_resume_rounds
                        )
                        observation["post_resume_model_input_has_read_receipt"] = any(
                            spec.has_read_receipt(round.get("input_messages") or []) for round in post_resume_rounds
                        )
                    finally:
                        await rebuilt.close()
                else:
                    final = await environment.engine.wait_for(run_id, timeout=CASE_TIME_BUDGET_SECONDS)
                if case != "approval_resume":
                    observation.update(_observation(environment, server, api, run_id, started))
                    observation["approval"] = None
                    observation["rebuild"] = {"performed": False}
                    observation["post_resume_model_input_has_receipt"] = None

                observation["task_status"] = final.status
                result = final.result or {}
                observation["final_content"] = result.get("content")
                observation["error_category"] = result.get("error_category")
                observation["error"] = final.error or result.get("error")
                observation["reconciliation_reason"] = (
                    "action outcome is not established" if final.needs_reconciliation else None
                )
                observation["model_calls"] = len(server.rounds)
                observation["write_attempts"] = api.write_attempts
                observation["side_effect_count"] = api.side_effects
                observation["usage"] = _usage(server.rounds)
                observation["collection_error"] = None
            finally:
                if not initial_closed:
                    await environment.close()
    except Exception as exc:  # noqa: BLE001 — the collection failure is the record
        observation = {
            "run_id": None,
            "task_id": None,
            "run_ref": None,
            "task_status": None,
            "final_content": None,
            "error_category": None,
            "error": None,
            "reconciliation_reason": None,
            "model_calls": len(server.rounds),
            "rounds": list(server.rounds),
            "tool_calls": list(api.calls),
            "tool_receipts": list(api.receipts),
            "ledger_actions": [],
            "events": [],
            "write_attempts": api.write_attempts,
            "side_effect_count": api.side_effects,
            "approval": None,
            "rebuild": {"performed": False},
            "post_resume_model_input_has_receipt": None,
            "latency_ms_total": (time.monotonic() - started) * 1000,
            "usage": _usage(server.rounds),
            "collection_error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        await server.stop()
    return observation


async def _wait_parked(environment: RunnerEnvironment, run_id: str) -> Any:
    deadline = asyncio.get_running_loop().time() + CASE_TIME_BUDGET_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        state = environment.engine.get_state(run_id)
        if state is not None and state.status in {"waiting_approval", "succeeded", "failed", "cancelled"}:
            if state.status != "waiting_approval":
                raise AssertionError(f"run reached {state.status} instead of parking for approval")
            return state
        await asyncio.sleep(0.05)
    raise TimeoutError("run never parked for approval")


def _usage(rounds: list[dict[str, Any]]) -> dict[str, Any]:
    totals = [
        round["usage"].get("total_tokens")
        for round in rounds
        if isinstance(round.get("usage"), dict) and isinstance(round["usage"].get("total_tokens"), int)
    ]
    measured = bool(rounds) and len(totals) == len(rounds)
    return {
        "source": "reported" if measured else "unverified",
        "token_total": sum(totals) if measured else None,
        "cost_amount": None,
        "currency": "USD",
        "price_version": "unverified",
    }
