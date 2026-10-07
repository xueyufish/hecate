"""Host-side managed channel (step6/7 managed runner enrollment).

``ManagedChannel`` is the optional overlay binding the runner to the
platform control plane. Everything here is additive: when
``RunnerConfig.control_plane`` is absent the host never constructs this
module and its standalone behavior is byte-for-byte unchanged.

Responsibilities:

- **Enrollment** — prove material possession of the trust secret (HMAC
  challenge) and register the host; then exchange the answer for a channel
  credential. Admission and managed-new-runs stay with the platform's
  operator flow — registration never grants scheduling rights.
- **Pull loop** — fetch deliveries after the local cursor; each response
  carries a fresh authorization lease, verified by :class:`LeaseGate`
  (issuer/audience/expiry + nonce consumption). A delivery is accepted via
  the durable store's idempotent ``submit_task`` — a redelivered row
  returns the original local task/run references and never re-executes.
- **Event upload** — push the local durable event log after the upload
  cursor; the platform deduplicates per event_id, so replays are harmless.
  Upload happens before new deliveries are requested (projection first).
- **Disconnect semantics** — every channel failure degrades to "keep
  serving locally, retry later". The lease expiring stops NEW protected
  actions (the lease gate); it never switches the host to local
  self-authorization, and local evidence retention is unaffected.

The channel speaks plain HTTP/JSON against the platform's ``/managed``
API — no platform imports, keeping the runner wheel closure clean.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from hecate_durable.contracts.credentials import (
    Claims,
    CredentialError,
    verify_lease,
)
from hecate_durable.contracts.credentials import (
    solve_challenge as issue_challenge_answer,
)
from hecate_durable.contracts.durable import IdempotencyKey, canonical_request_digest
from hecate_durable.contracts.references import BackendRef, RefKind
from hecate_durable.storage.lease import LeaseHandle, StaleFenceError
from hecate_durable.storage.store import SqlDurableStore

if TYPE_CHECKING:
    from .durable import DurableRuntime
    from .engine import ExecutionEngine

logger = logging.getLogger(__name__)

# Local reference convention for accepted deliveries (step6a): the local
# task/run ids are derived from the platform delivery row id under one
# issuer domain, making the managed receive queue a partition of the host's
# durable ledger that both recovery entries can route on.
MANAGED_ISSUER = "managed-host"


def managed_principal_for(trust_root: str) -> str:
    """The managed execution principal derived from the enrolled trust root."""

    return f"managed:{trust_root}"


def managed_task_ref(delivery_row_id: str) -> BackendRef:
    """The local task reference an accepted delivery maps to (idempotent)."""

    return BackendRef(RefKind.TASK, MANAGED_ISSUER, f"managed-{delivery_row_id}")


def managed_run_ref(delivery_row_id: str) -> BackendRef:
    """The local run reference an accepted delivery maps to (idempotent)."""

    return BackendRef(RefKind.RUN, MANAGED_ISSUER, f"managed-run-{delivery_row_id}")


class LeaseGate:
    """Host-side authorization gate for managed protected actions.

    Verifies the platform-issued lease (signature, expiry, deployment
    binding) and consumes its nonce locally: a replayed or already-used
    lease cannot re-arm actions. When the current lease is expired or
    absent, new protected actions are refused — the gate has no path to
    local self-authorization.
    """

    def __init__(
        self,
        secret: bytes,
        *,
        deployment_domain: str,
        issuer_domain: str | None = None,
        host_id: str | None = None,
        workspace_id: str | None = None,
        clock=None,
    ) -> None:
        self._secret = secret
        self._deployment_domain = deployment_domain
        self._issuer_domain = issuer_domain
        self._host_id = host_id
        self._workspace_id = workspace_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._consumed: set[str] = set()
        self._current: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    def update(self, lease: dict[str, Any]) -> None:
        """Install a freshly pulled lease (replacing the expired one)."""

        self._current = dict(lease)

    async def check(self) -> Claims:
        """Validate the current lease and consume its nonce; raises on refusal."""

        async with self._lock:
            lease = self._current
            if lease is None:
                raise CredentialError("no authorization lease; managed actions are stopped")
            claims = verify_lease(
                lease,
                self._secret,
                deployment_domain=self._deployment_domain,
                now=self._clock(),
            )
            self._check_identity(claims)
            if claims.nonce in self._consumed:
                raise CredentialError("lease nonce already consumed (replay rejected)")
            self._consumed.add(claims.nonce)
            return claims

    @property
    def active(self) -> bool:
        lease = self._current
        if lease is None:
            return False
        try:
            claims = verify_lease(lease, self._secret, deployment_domain=self._deployment_domain, now=self._clock())
            self._check_identity(claims)
            return True
        except CredentialError:
            return False

    def _check_identity(self, claims: Claims) -> None:
        for actual, expected, label in (
            (claims.iss, self._issuer_domain, "issuer"),
            (claims.sub, self._host_id, "host"),
            (claims.tenant, self._workspace_id, "workspace"),
        ):
            if expected is not None and actual != expected:
                raise CredentialError(f"lease {label} does not match the enrolled host")


@dataclass
class ManagedStats:
    """Observable channel counters (health/report surface)."""

    pulls: int = 0
    deliveries_accepted: int = 0
    deliveries_duplicate: int = 0
    events_uploaded: int = 0
    upload_failures: int = 0
    lease_gate_refusals: int = 0
    command_effects_uploaded: int = 0
    command_failures: int = 0


class ManagedChannel:
    """The host's control-plane client loop (optional; overlay only)."""

    def __init__(
        self,
        *,
        base_url: str,
        workspace_id: str,
        trust_root: str,
        host_id: str,
        secret: bytes,
        store: SqlDurableStore,
        issuer_domain: str,
        lease_ttl_seconds: float = 120.0,
        poll_interval_seconds: float = 5.0,
        upload_batch: int = 100,
        data_domains: tuple[str, ...] = (),
        lease_gate: LeaseGate | None = None,
        transport=None,
        clock=None,
        execution_definition: str | None = None,
        command_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._workspace_id = workspace_id
        self._trust_root = trust_root
        self._host_id = host_id
        self._secret = secret
        self._store = store
        self._issuer_domain = issuer_domain
        self._lease_ttl = lease_ttl_seconds
        self._poll_interval = poll_interval_seconds
        self._upload_batch = upload_batch
        # The managed execution identity stamped into every accepted
        # delivery's persisted input; principal derives from the trust root
        # the platform verified, domains are the operator-declared scope
        # (empty = deny-by-default at the existing domain check).
        self._managed_principal = managed_principal_for(trust_root)
        self._managed_domains = tuple(data_domains)
        self._execution_definition = execution_definition
        self._command_handler = command_handler
        self._transport = transport
        self._clock = clock or (lambda: datetime.now(UTC))
        # The gate is shared with the execution engine when the assembly
        # passes one in: pulls update it, protected dispatches check it.
        self._gate = lease_gate or LeaseGate(
            secret,
            deployment_domain=trust_root,
            issuer_domain=issuer_domain,
            host_id=host_id,
            workspace_id=workspace_id,
            clock=clock,
        )
        self._credential: dict[str, Any] | None = None
        self._delivery_cursor: str | None = None
        self._event_cursors: dict[tuple[str, str], int] = {}
        self._upload_exhausted = False
        self.stats = ManagedStats()
        self._loop_task: asyncio.Task[None] | None = None

    # -- public API ------------------------------------------------------------

    @property
    def gate(self) -> LeaseGate:
        return self._gate

    @property
    def poll_interval(self) -> float:
        return self._poll_interval

    async def register(self) -> bool:
        """Prove possession + register; returns True when the channel admits pulls.

        Admission (managed new runs) is the platform operator's decision —
        a pending enrollment registers successfully but pulls return no
        deliveries (403 on the host routes until admitted). Re-registration
        on every boot is the reconnect path: an already-enrolled host gets
        the platform's duplicate-registration rejection, which is tolerated
        — possession is (re)proven by the credential exchange that follows.
        """

        nonce = self._new_nonce()
        answer = issue_challenge_answer(self._secret, nonce)
        await self._request(
            "POST",
            "/managed/enrollments",
            payload={
                "workspace_id": self._workspace_id,
                "host_identity_ref": {
                    "issuer_domain": self._issuer_domain,
                    "id": self._host_id,
                    "name": self._trust_root,
                },
                "trust_root_ref": {
                    "issuer_domain": self._issuer_domain,
                    "id": f"root-{self._host_id}",
                    "name": self._trust_root,
                },
                "installed_versions": {
                    "contracts": ["0.1"],
                    "capabilities": {"managed_channel": "pull"},
                    "challenge": {"nonce": nonce, "answer": answer},
                },
            },
            auth=False,
            tolerate=(409, 422),  # already registered / pending review is fine
        )
        credential = await self._exchange_credential()
        return credential is not None

    async def run_forever(self) -> None:
        """Pull/upload loop; every failure degrades to retry-later."""

        while True:
            try:
                await self.upload_events()
            except Exception:  # noqa: BLE001 — the loop outlives a bad cycle
                self.stats.upload_failures += 1
                logger.exception("managed event upload failed; will retry")
            try:
                await self.pull_once()
            except Exception:  # noqa: BLE001
                logger.exception("managed pull failed; will retry")
            await asyncio.sleep(self._poll_interval)

    def start(self) -> None:
        self._loop_task = asyncio.get_running_loop().create_task(self.run_forever())

    def set_command_handler(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]],
    ) -> None:
        """Bind the host's durable command executor after engine assembly."""

        self._command_handler = handler

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task

    # -- channel steps -----------------------------------------------------------

    async def pull_once(self) -> int:
        """One pull cycle; returns the number of deliveries accepted."""

        params = f"?cursor={self._delivery_cursor}" if self._delivery_cursor else ""
        payload = await self._request("GET", f"/managed/host/pull{params}", auth=True)
        if payload is None:
            return 0
        self.stats.pulls += 1
        lease = payload.get("lease")
        if lease:
            self._gate.update(lease)
        accepted = 0
        for delivery in payload.get("deliveries", []):
            outcome = await self._accept_delivery(delivery)
            if outcome == "accepted":
                accepted += 1
                self.stats.deliveries_accepted += 1
            elif outcome == "duplicate":
                self.stats.deliveries_duplicate += 1
        for command in payload.get("commands", []):
            try:
                await self._apply_command(command)
            except Exception:  # noqa: BLE001 — retry from the durable platform outbox
                self.stats.command_failures += 1
                logger.exception("managed command processing failed")
        self._delivery_cursor = payload.get("next_cursor") or self._delivery_cursor
        return accepted

    async def _apply_command(self, command: dict[str, Any]) -> None:
        """Apply a platform command through the host's durable command ledger."""

        if self._command_handler is None:
            return
        effect = await self._command_handler(command)
        if effect is None:
            return
        result = await self._request(
            "POST",
            "/managed/host/commands/effect",
            payload=effect,
            auth=True,
        )
        if result is not None:
            self.stats.command_effects_uploaded += 1

    async def _accept_delivery(self, delivery: dict[str, Any]) -> str:
        """Idempotently map one delivery to a local task/run; never re-executes.

        The durable store's ``submit_task`` arbitration is the accept record:
        the same delivery id returns the original local task/run, so a
        redelivered row (lost accept response) answers without executing.
        The idempotency digest covers the delivery content only — the host
        identity stamp below is provenance, not part of conflict detection,
        so a config change between redeliveries cannot fork the mapping.
        """

        delivery_row_id = str(delivery["delivery_row_id"])
        local_task = managed_task_ref(delivery_row_id)
        local_run = managed_run_ref(delivery_row_id)
        delivery_input = dict(delivery.get("input_payload") or {})
        # Accept-time identity stamp: recovery and execution resolve THIS
        # recorded identity (verified channel + operator-configured scope),
        # never caller-supplied claims; `resume_managed` re-checks it
        # against the current config and stops into reconciliation on drift.
        persisted_input = {
            **{key: value for key, value in delivery_input.items() if not key.startswith("_")},
            "_host_identity": {"principal": self._managed_principal, "domains": list(self._managed_domains)},
            "_host_definition": self._execution_definition,
        }

        def _accept() -> tuple[bool, BackendRef, BackendRef]:
            # Redelivery detection is durable, not in-memory: an existing
            # task row for this delivery means a previous accept landed and
            # the row is answered without executing again.
            existing = self._store.get_task_state(local_task)
            replayed = existing is not None
            association = self._store.submit_task(
                key=IdempotencyKey(
                    key=f"managed-delivery:{delivery_row_id}",
                    subject=self._host_id,
                    workspace=self._workspace_id,
                    request_digest=canonical_request_digest(
                        {
                            "delivery_row_id": delivery_row_id,
                            "input_payload": delivery_input,
                        }
                    ),
                ),
                task_ref=local_task,
                run_ref=local_run,
                input_payload=persisted_input,
            )
            return replayed, association.task_ref, association.run_ref

        replayed, task_ref_out, run_ref_out = await asyncio.to_thread(_accept)

        acknowledged = await self._request(
            "POST",
            "/managed/host/accept",
            payload={
                "delivery_row_id": delivery_row_id,
                "local_task_ref": task_ref_out.to_dict(),
                "local_run_ref": run_ref_out.to_dict(),
            },
            auth=True,
            tolerate=(409,),  # a conflicting mapping is a platform-side bug; logged below
        )
        if acknowledged is None:
            logger.warning("delivery %s accept not confirmed; redelivery will reconcile", delivery_row_id)
        if replayed:
            return "duplicate"
        # Acceptance is a queued intent, not proof that execution started.
        return "accepted"

    async def upload_events(self) -> int:
        """Push local durable events after the upload cursor (dedup upstream)."""

        run_rows = await asyncio.to_thread(self._store.list_tasks)
        uploaded = 0
        for record in run_rows:
            if record.task_ref.issuer_domain != MANAGED_ISSUER:
                continue
            runs = await asyncio.to_thread(self._store.event_runs_for_task, record.task_ref)
            for run_ref in runs:
                association = await self.associate_attempt(record.task_ref, run_ref)
                if association is None:
                    continue
                stream_key = (run_ref.issuer_domain, run_ref.id)
                page = await asyncio.to_thread(
                    self._store.read_events,
                    run_ref,
                    cursor=self._event_cursors.get(stream_key, 0),
                    limit=self._upload_batch,
                )
                envelopes = [event.to_dict() for event in page.events if event.kind.value == "event"]
                if not envelopes:
                    continue
                if self._gate.active is False and not self._credential:
                    return uploaded
                result = await self._request(
                    "POST",
                    "/managed/host/events",
                    payload={"local_task_ref": record.task_ref.to_dict(), "envelopes": envelopes},
                    auth=True,
                    tolerate=(403, 422),
                )
                if result is None:
                    continue
                count = int(result.get("projected", 0))
                uploaded += count
                self._event_cursors[stream_key] = page.next_cursor
                self.stats.events_uploaded += count
        return uploaded

    async def associate_attempt(self, task_ref: BackendRef, run_ref: BackendRef) -> dict[str, Any] | None:
        """Register a host-local attempt before execution facts are projected."""

        return await self._request(
            "POST",
            "/managed/host/attempts",
            payload={"local_task_ref": task_ref.to_dict(), "local_run_ref": run_ref.to_dict()},
            auth=True,
        )

    # -- transport ---------------------------------------------------------------

    def _new_nonce(self) -> str:
        import secrets as secrets_mod

        return f"n-{secrets_mod.token_hex(16)}"

    async def _exchange_credential(self) -> dict[str, Any] | None:
        nonce = self._new_nonce()
        answer = issue_challenge_answer(self._secret, nonce)
        payload = await self._request(
            "POST",
            "/managed/host/credential",
            payload={
                "workspace_id": self._workspace_id,
                "trust_root": self._trust_root,
                "nonce": nonce,
                "answer": answer,
                "host_id": self._host_id,
            },
            auth=False,
        )
        if payload is None:
            return None
        self._credential = payload.get("credential")
        return self._credential

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        auth: bool = True,
        tolerate: tuple[int, ...] = (),
    ) -> dict[str, Any] | None:
        """One channel call; None = tolerated failure (loop degrades, never crashes)."""

        import httpx

        headers: dict[str, str] = {}
        if auth:
            if self._credential is None:
                credential = await self._exchange_credential()
                if credential is None:
                    return None
            headers["Authorization"] = "ManagedBearer " + json.dumps(self._credential)
        url = self._base_url + path
        try:
            if self._transport is not None:
                client = httpx.AsyncClient(transport=self._transport, base_url=self._base_url)
            else:
                client = httpx.AsyncClient(base_url=self._base_url)
            async with client:
                response = await client.request(method, url, json=payload, headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            logger.warning("managed channel %s %s failed: %s", method, path, exc)
            return None
        if response.status_code in tolerate:
            logger.info("managed channel %s %s tolerated %d", method, path, response.status_code)
            return None
        if response.status_code >= 400:
            logger.warning(
                "managed channel %s %s rejected: %d %s", method, path, response.status_code, response.text[:300]
            )
            return None
        if not response.content:
            return {}
        return response.json()


@dataclass(frozen=True)
class ManagedIdentity:
    """The host's managed execution identity (accept stamp ↔ engine check)."""

    principal: str
    domains: tuple[str, ...]


async def apply_managed_command(
    item: dict[str, Any],
    *,
    durable: DurableRuntime,
    engine: ExecutionEngine,
) -> dict[str, Any] | None:
    """Apply a managed command through the host's durable ledger."""

    from hecate_durable.contracts.durable import (
        CommandState,
        ControlCommandKind,
        ControlCommandRecord,
        TaskLifecycleState,
    )
    from hecate_durable.contracts.references import BackendRef

    command = item["command"]
    local_task_ref = BackendRef.from_dict(item["local_task_ref"])
    local_run_ref = BackendRef.from_dict(item["local_run_ref"])

    def settled_effect(state: CommandState, successor_run_ref: BackendRef | None = None) -> dict[str, Any]:
        return {
            "command_id": command["command_id"],
            "state": state.value,
            "local_task_ref": local_task_ref.to_dict(),
            "local_run_ref": local_run_ref.to_dict(),
            "successor_run_ref": successor_run_ref.to_dict() if successor_run_ref is not None else None,
        }

    previous = await asyncio.to_thread(durable.store.get, command["command_id"])
    if previous is not None and previous.state in (
        CommandState.APPLIED,
        CommandState.REJECTED,
        CommandState.EXPIRED,
    ):
        effect_run = previous.extra.get("effect_run_ref")
        successor = BackendRef.from_dict(effect_run) if isinstance(effect_run, dict) else None
        if successor == local_run_ref:
            successor = None
        return settled_effect(previous.state, successor)

    expires_at = command.get("expires_at")
    if expires_at is not None:
        deadline = datetime.fromisoformat(expires_at)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        if deadline <= datetime.now(UTC):
            if previous is not None:
                expired = await asyncio.to_thread(durable.store.transition, command["command_id"], CommandState.EXPIRED)
            else:
                expired = await asyncio.to_thread(
                    durable.store.record,
                    ControlCommandRecord(
                        command_id=command["command_id"],
                        kind=ControlCommandKind(command["kind"]),
                        issuer=command["issuer"],
                        task_ref=local_task_ref,
                        run_ref=local_run_ref,
                        issued_at=command["issued_at"],
                        expires_at=expires_at,
                        state=CommandState.EXPIRED,
                        detail_ns=command.get("detail_ns") or {},
                        payload=command.get("payload") or {},
                        payload_schema_ref=command.get("payload_schema_ref"),
                    ),
                )
            return settled_effect(expired.state)

    kind = ControlCommandKind(command["kind"])
    if kind in (ControlCommandKind.PROVIDE_INPUT, ControlCommandKind.RESUME):
        input_payload = command.get("payload") or {}
        if kind is ControlCommandKind.RESUME:
            input_payload = None
        receipt, _reason = await asyncio.to_thread(
            durable.apply_wake,
            command_id=command["command_id"],
            kind=kind.value,
            issuer=command["issuer"],
            task_ref=local_task_ref,
            run_ref=local_run_ref,
            wait_token=str((command.get("detail_ns") or {}).get("wait_token") or ""),
            input_payload=input_payload,
            expires_at=expires_at,
            input_validator=engine.validate_run_input,
        )
        current_run = await asyncio.to_thread(durable.store.run_for_task, local_task_ref)
        successor = current_run if current_run != local_run_ref else None
        if receipt.state in (CommandState.APPLIED, CommandState.REJECTED, CommandState.EXPIRED):
            return settled_effect(receipt.state, successor)
        return None

    if kind is ControlCommandKind.CANCEL:
        record = await asyncio.to_thread(
            durable.record_cancel,
            command_id=command["command_id"],
            issuer=command["issuer"],
            task_ref=local_task_ref,
            run_ref=local_run_ref,
        )
        if record.state in (CommandState.APPLIED, CommandState.REJECTED, CommandState.EXPIRED):
            return settled_effect(record.state)
        state_record = await asyncio.to_thread(durable.store.get_task_state, local_task_ref)
        if state_record is None or state_record.lifecycle_state in (
            TaskLifecycleState.SUCCEEDED,
            TaskLifecycleState.FAILED,
            TaskLifecycleState.CANCELLED,
            TaskLifecycleState.RECONCILIATION_REQUIRED,
        ):
            rejected = await asyncio.to_thread(durable.store.transition, command["command_id"], CommandState.REJECTED)
            return settled_effect(rejected.state)
        if state_record.lifecycle_state in (
            TaskLifecycleState.QUEUED,
            TaskLifecycleState.WAITING_INPUT,
            TaskLifecycleState.WAITING_APPROVAL,
        ):
            try:
                await asyncio.to_thread(
                    durable.store.apply_task_state,
                    local_task_ref,
                    TaskLifecycleState.CANCELLED,
                    expected_revision=state_record.revision,
                    applied_command_id=command["command_id"],
                )
            except ValueError:
                if engine.request_cancel(local_run_ref.id, command_id=command["command_id"]):
                    return None
                latest = await asyncio.to_thread(durable.store.get, command["command_id"])
                return (
                    settled_effect(latest.state)
                    if latest is not None and latest.state in (CommandState.APPLIED, CommandState.REJECTED)
                    else None
                )
            applied = await asyncio.to_thread(durable.store.get, command["command_id"])
            return settled_effect(applied.state if applied is not None else CommandState.APPLIED)
        engine.request_cancel(local_run_ref.id, command_id=command["command_id"])
        return None

    rejected = await asyncio.to_thread(
        durable.store.record,
        ControlCommandRecord(
            command_id=command["command_id"],
            kind=kind,
            issuer=command["issuer"],
            task_ref=local_task_ref,
            run_ref=local_run_ref,
            issued_at=command["issued_at"],
            expires_at=expires_at,
            state=CommandState.REJECTED,
            payload=command.get("payload") or {},
            payload_schema_ref=command.get("payload_schema_ref"),
            detail_ns=command.get("detail_ns") or {},
        ),
    )
    return settled_effect(rejected.state)


class ManagedExecutionScheduler:
    """Serial dispatch of accepted managed tasks through the engine.

    The engine's serial slot is the only execution path: ``drain_once``
    starts at most one accepted (QUEUED/RUNNING) managed task per tick and
    the slot refuses concurrent work. Tasks whose durable state leaves the
    resumable set (executed to terminal, parked in reconciliation) are
    simply skipped; a task that cannot be resumed while the slot is free is
    remembered so one bad row cannot starve the queue.
    """

    def __init__(
        self,
        *,
        store: SqlDurableStore,
        engine: ExecutionEngine,
        associate_attempt: Callable[[BackendRef, BackendRef], Awaitable[dict[str, Any] | None]] | None = None,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self._store = store
        self._engine = engine
        self._associate_attempt = associate_attempt
        self._poll_interval = poll_interval_seconds
        self._unresumable: set[str] = set()
        self._loop_task: asyncio.Task[None] | None = None

    async def drain_once(self) -> bool:
        """Start at most one accepted managed task; True when one started."""

        if self._engine.closing:
            return False
        from hecate_durable.contracts.durable import TaskLifecycleState

        resumable = self._store.list_tasks(states={TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING})
        for record in resumable:
            if record.task_ref.issuer_domain != MANAGED_ISSUER or record.task_ref.id in self._unresumable:
                continue
            run_ref = self._store.run_for_task(record.task_ref)
            if run_ref is None:
                self._unresumable.add(record.task_ref.id)
                continue
            if self._associate_attempt is not None:
                association = await self._associate_attempt(record.task_ref, run_ref)
                if association is None:
                    # Do not execute a successor until the platform has a
                    # durable mapping for its facts and subsequent events.
                    continue
            try:
                run_id = self._engine.resume_managed(record.task_ref, run_ref)
            except Exception as exc:
                from .engine import EvidenceUnavailableError

                if not isinstance(exc, EvidenceUnavailableError):
                    raise
                # Local evidence unwritable refuses NEW protected work; the
                # accepted task is retained and retried on a later tick.
                logger.warning("managed scheduler: evidence unavailable; task %s retained", record.task_ref.id)
                return False
            if run_id is not None:
                return True
            if not self._engine.busy:
                # Slot free + refusal means persistent rejection (e.g. the
                # task converged inside the entry check); retrying would
                # only spin, so remember it and move on.
                self._unresumable.add(record.task_ref.id)
        return False

    async def run_forever(self) -> None:
        while True:
            try:
                started = await self.drain_once()
            except Exception:  # noqa: BLE001 — the loop outlives a bad cycle
                logger.exception("managed scheduler cycle failed; will retry")
                started = False
            await asyncio.sleep(0.05 if started else self._poll_interval)

    def start(self) -> None:
        self._loop_task = asyncio.get_running_loop().create_task(self.run_forever())

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task


async def record_lease_fenced_outcome(store: SqlDurableStore, handle: LeaseHandle, action_key: str) -> None:
    """Guard helper: a fenced write under an expired dispatch lease raises."""

    current = store.leases.current_token(f"dispatch:{handle.lease_key}")
    if current != handle.fencing_token:
        raise StaleFenceError(handle.lease_key, handle.fencing_token, current if current is not None else -1)
