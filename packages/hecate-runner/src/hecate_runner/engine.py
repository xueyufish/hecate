"""Execution assembly: a minimal fixed graph per profile.

The runner compiles one linear graph per manifest at startup:

    model node -> (one read-tool node per allowlisted tool) -> end

The model node produces the tool-selection payload; each tool node
dispatches to the business API with the server-verified principal and
domain scope. Execution is serial (``max_concurrency = 1``): a new run
is refused while another is in flight rather than queued. The preview
profile uses in-memory checkpoints; the durable profile persists attempts,
waiting facts, action receipts and checkpoints behind admission checks.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

import httpx
from hecate_durable.contracts.credentials import CredentialError
from hecate_durable.contracts.references import BackendRef
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_runtime.action_ledger import ActionLedgerHook, tool_arguments_digest
from hecate_runtime.checkpoint import InMemoryCheckpointStore
from hecate_runtime.execution_service import (
    CooperativeCancellationError,
    RuntimeEventDecision,
    RuntimeExecutionRequest,
    RuntimeScheduleCancelledError,
    runtime_execution_service,
)
from hecate_runtime.types import (
    ChannelDef,
    ChannelType,
    Edge,
    NodeConfig,
    NodeType,
    WorkerResult,
)
from hecate_runtime.types import (
    CompiledGraph as CompiledGraphT,
)
from hecate_runtime.worker import Worker
from jsonschema import Draft202012Validator

from .durable import DurableRuntime, gate_dispatch_async, is_decided_action, record_outcome_async
from .evidence import OUTCOME_FAILED, EvidenceStore
from .managed import MANAGED_ISSUER, LeaseGate, ManagedIdentity
from .profile import BUILTIN_TOOL_SCHEMAS, Profile

DURABLE_CAPABILITIES: dict[str, str] = {
    "durable_tasks": "supported: persistent task/action ledger (hecate-durable)",
    "long_task_recovery": (
        "supported: restart recovery via persisted checkpoints (same-attempt resume) with ledger-gated replay fallback"
    ),
    "write_tools": "supported: manifest-declared write tools through the action ledger",
}

logger = logging.getLogger(__name__)

# A consumed/not-yet-arrived lease is transient while the channel keeps
# pulling (one protected dispatch per lease): bounded wait for refresh.
_LEASE_REFRESH_WAIT_SECONDS = 5.0
_LEASE_REFRESH_POLL_SECONDS = 0.05

# Capabilities the preview profile does not provide; surfaced verbatim on
# /capabilities so absence is explicit, not a silent default.
UNSUPPORTED_CAPABILITIES: dict[str, str] = {
    "durable_tasks": "unsupported: persistence lands in step6",
    "background_retry": "unsupported: automatic retries are a step6/7 concern",
    # The durable profile overrides this one (persisted checkpoints).
    "long_task_recovery": "unsupported: checkpoint recovery requires the durable profile",
    "write_tools": "unsupported: preview profile serves read tools only",
    "approval_tools": "unsupported: approval gating lands in step7",
    "event_stream": "unsupported: use cursor-based /events polling in the preview",
    "pause": "unsupported: cooperative pause is a later-step capability",
}


class ModelNodeWorker(Worker):
    """Produces the tool-selection payload from the run input.

    Stub backend: deterministic selection of every allowlisted tool in
    manifest order, consuming the run input verbatim. Endpoint backend:
    calls the configured JSON endpoint and retains its text in graph events.
    Both modes execute a fixed tool plan; model-driven tool selection is
    outside this preview.
    """

    def __init__(self, profile: Profile) -> None:
        super().__init__()
        self._profile = profile

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        run_input = channel_snapshot.get("input") or {}
        prompt = run_input.get("prompt", (run_input.get("input") or {}).get("prompt", ""))
        config = self._profile.config
        content = ""
        if config.model_backend == "endpoint":
            headers = {}
            if config.model_auth_env:
                headers["Authorization"] = f"Bearer {os.environ[config.model_auth_env]}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    config.model_endpoint,
                    json={"prompt": prompt, "tools": list(config.tool_allowlist)},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
                    raise ValueError("model endpoint must return an object with string content")
                content = payload["content"]
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "plan": {
                    "tools": list(config.tool_allowlist),
                    "prompt": prompt,
                    "model_source": node_config.get("model_source", "stub"),
                    "content": content,
                },
            },
        )


class ReadToolNodeWorker(Worker):
    """Dispatches one allowlisted read tool against the business API."""

    def __init__(self, dispatch) -> None:
        super().__init__()
        self._dispatch = dispatch

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        tool_name = node_config["tool_name"]
        raw = (channel_snapshot.get("input") or {}).get("tool_arguments") or {}
        # Two accepted shapes: flat (the preview graph's single-tool node
        # consumes the arguments directly) or tool-name-namespaced.
        namespaced = raw.get(tool_name) if isinstance(raw.get(tool_name), dict) else None
        arguments = namespaced if namespaced is not None else raw
        outcome = await self._dispatch(arguments)
        return WorkerResult(node_id=node_id, channel_updates={"tool_results": {tool_name: outcome}})


@dataclass
class RunState:
    run_id: str
    status: str  # "running" | "succeeded" | "failed" | "cancelled" | "unknown" | task lifecycle
    principal: str
    domains: tuple[str, ...]
    events: list[dict] = field(default_factory=list)
    result_ref: str | None = None
    error: str | None = None
    cancel_requested: bool = False
    # Durable profile correlation (None in the preview).
    task_ref: BackendRef | None = None
    run_ref: BackendRef | None = None
    action_hook: ActionLedgerHook | None = None
    needs_reconciliation: bool = False
    replayed: bool = False
    cancel_command_id: str | None = None
    received_at: str | None = None
    idempotency_key: str | None = None
    # Provenance: True only for tasks driven through the managed scheduler;
    # their protected dispatches pass the lease gate (step6b).
    managed: bool = False
    # step6c: set at the dispatch boundary when the run must park into a
    # persistent wait (approval tool or input_required outcome); the graph
    # stops at the next event boundary and _execute persists the wait.
    wait_request: dict | None = None
    wait_token: str | None = None
    dispatch_denied: bool = False


class EvidenceUnavailableError(Exception):
    """The local evidence store cannot accept writes (SC06 gate)."""


class ExecutionEngine:
    """Serial, in-process execution of the compiled preview graph."""

    def __init__(
        self,
        profile: Profile,
        evidence: EvidenceStore,
        tool_dispatch,
        durable: DurableRuntime | None = None,
        managed_identity: ManagedIdentity | None = None,
        lease_gate: LeaseGate | None = None,
        dispatch_binding: str | None = None,
    ) -> None:
        # ``tool_dispatch`` signature: (tool_name, arguments, principal, domains).
        self._profile = profile
        self._evidence = evidence
        self._tool_dispatch = tool_dispatch
        self._durable = durable
        # Managed execution identity: present only in the managed assembly;
        # ``resume_managed`` refuses to drive managed tasks without it.
        self._managed_identity = managed_identity
        # The channel's live lease gate (updated on every pull); present
        # only in the managed assembly. None = no lease enforcement, which
        # is the standalone posture.
        self._lease_gate = lease_gate
        # Side-effect class per allowlisted tool from the manifest's declared
        # permission: read → readonly, write → conservative non-idempotent.
        self._side_effects: dict[str, ToolSideEffectClass] = {
            tool.name: (
                ToolSideEffectClass.READONLY if tool.permission == "read" else ToolSideEffectClass.NON_IDEMPOTENT_WRITE
            )
            for tool in profile.manifest.tools
            if tool.name in profile.config.tool_allowlist
        }
        # step6c: approval-declared tools park into a persistent wait instead
        # of dispatching; the wake command is the recorded decision.
        self._approval_tools = {
            tool.name
            for tool in profile.manifest.tools
            if tool.permission == "approval_required" and tool.name in profile.config.tool_allowlist
        }
        # step6d: the durable assembly persists execution checkpoints in the
        # host's own database so a restart can resume the SAME attempt from
        # the last superstep; the preview profile keeps the in-memory store.
        self._checkpoint_store = self._build_checkpoint_store()
        self._definition_digest = profile.execution_definition_digest(dispatch_binding)
        if self._durable is not None:
            self._durable.execution_definition = self._definition_digest
        self._lock = asyncio.Lock()
        self._runs: dict[str, RunState] = {}
        self._graph = self._compile_graph()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()
        self._closing = False
        self._resuming = False

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop runs are scheduled on (set by the server layer)."""

        self._loop = loop

    def _build_checkpoint_store(self):
        """Persistent checkpoints when durable, in-memory otherwise."""

        if self._durable is None:
            return InMemoryCheckpointStore()
        return self._durable.async_checkpoints

    @staticmethod
    def _session_for_task(task_ref: BackendRef) -> uuid.UUID:
        """The stable, task-level recovery session (all attempts share it)."""

        from .durable import DurableRuntime

        return DurableRuntime._task_session(task_ref)

    def _resume_from_checkpoint(self, task_ref: BackendRef, run_ref: BackendRef | None = None) -> bool:
        """True when this task's stable session has a persisted checkpoint."""

        if self._durable is None:
            return False
        return self._checkpoint_store.has_checkpoint_sync(self._checkpoint_session(task_ref, run_ref))

    def _checkpoint_session(self, task_ref: BackendRef, run_ref: BackendRef | None) -> uuid.UUID:
        """Use attempt isolation, with a safe legacy interrupted-run fallback."""
        legacy = self._session_for_task(task_ref)
        wait = (self._durable.store.get_task_state(task_ref).extra or {}).get("wait") or {}
        if not wait.get("consumed") and self._checkpoint_store.has_checkpoint_sync(legacy):
            return legacy
        run_ref = run_ref or self._durable.store.run_for_task(task_ref)
        if run_ref is None:
            return legacy
        return uuid.uuid5(uuid.NAMESPACE_URL, f"runner-attempt:{run_ref.issuer_domain}:{run_ref.id}")

    async def _dispatch_tool(self, state: RunState, tool_name: str, arguments: dict) -> dict:
        """Run one tool with the run's server-verified identity scope.

        In the durable profile every dispatch first passes the persistent
        ledger gate (recovery verdict + atomic claim) and, for protected
        tools, the evidence-writability gate; the real outcome is mirrored
        back to the ledger after the business call.
        """

        if state.cancel_requested:
            raise CooperativeCancellationError
        digest = tool_arguments_digest(arguments)
        if state.wait_request is not None:
            # A wait was already requested on this run: nothing further may
            # dispatch while the graph unwinds to the stop boundary.
            return {"status": "withheld", "detail": "run is entering a persistent wait"}
        effect = self._side_effects.get(tool_name, ToolSideEffectClass.UNKNOWN)
        persisted = self._durable.store.get_task_input(state.task_ref) or {} if self._durable and state.task_ref else {}
        origins = persisted.get("_action_origins") or {}
        action_run_id = origins.get(tool_name, state.run_id)
        if (
            state.managed
            and effect is not ToolSideEffectClass.READONLY
            and not (
                state.action_hook is not None
                and await is_decided_action(
                    state.action_hook,
                    run_id=action_run_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    side_effect_class=effect,
                )
            )
        ):
            # step6b action boundary: no current lease, no action intent —
            # a refusal must not masquerade as an execution fact in the
            # ledger, so this runs BEFORE the intent/claim gate. A decided
            # action replays from the ledger without a business call, so it
            # skips the gate: consuming the one-shot lease there would
            # starve the attempt's real new dispatch of authorization.
            refusal = await self._lease_refusal(state, tool_name, arguments)
            if refusal is not None:
                state.dispatch_denied = True
                return refusal
        if (
            tool_name in self._approval_tools
            and tool_name not in origins
            and not self._consume_wake_grant(state, tool_name, digest)
        ):
            # A wait is not an action claim: no business dispatch has begun.
            state.wait_request = {
                "wake_kind": "resume",
                "contract_ref": {"tool": tool_name, "arguments_digest": digest, "kind": "approval"},
            }
            outcome = {"status": "awaiting_approval", "detail": "parked in waiting_approval; resume to dispatch"}
            self.record_tool_result(state, tool_name, outcome)
            return outcome
        if state.action_hook is not None:
            withheld, claim_blocked = await gate_dispatch_async(
                state.action_hook,
                run_id=action_run_id,
                tool_name=tool_name,
                arguments=arguments,
                side_effect_class=effect,
            )
            blocked = withheld if withheld is not None else claim_blocked
            if blocked is not None:
                self._mark_reconciliation(state, blocked)
                self.record_tool_result(state, tool_name, blocked)
                return blocked
            if effect is not ToolSideEffectClass.READONLY:
                # SC06 local half: stop new protected actions when the local
                # evidence store cannot accept writes; the failure carries an
                # explicit category (unwritable vs over-capacity policy).
                try:
                    self._evidence.probe()
                except OSError as exc:
                    category = getattr(exc, "category", "unwritable")
                    self._evidence.record_gate_failure(category, f"local evidence gate: {exc}")
                    blocked = {
                        "status": "store_unavailable",
                        "detail": f"local evidence unavailable ({category}): {exc}",
                    }
                    self._mark_reconciliation(state, blocked)
                    self.record_tool_result(state, tool_name, blocked)
                    return blocked
        self._evidence.append("tool_dispatch", state.principal, state.run_id, "started", {"tool": tool_name})
        if arguments.get("domain") not in state.domains:
            outcome = {"status": "authorization", "detail": "requested domain is outside the trusted identity scope"}
        else:
            try:
                outcome = await self._tool_dispatch(tool_name, arguments, state.principal, list(state.domains))
            except Exception:
                if effect is not ToolSideEffectClass.READONLY:
                    state.needs_reconciliation = True
                raise
        status = outcome.get("status")
        if status == "authorization":
            state.dispatch_denied = True
        if status == "input_required" and not self._consume_wake_grant(state, tool_name, digest):
            # step6c: the business API requested more input (no side effect
            # performed per its contract); park instead of recording an
            # outcome — the wake's new attempt re-dispatches fresh with the
            # merged provided input, and the matching grant lets it through.
            state.wait_request = {
                "wake_kind": "provide_input",
                "contract_ref": {
                    "tool": tool_name,
                    "arguments_digest": digest,
                    "kind": "input",
                    "contract": outcome.get("contract"),
                },
            }
            self.record_tool_result(state, tool_name, outcome)
            return outcome
        evidence_outcome = "ok" if status == "ok" else "denied" if status == "authorization" else "failed"
        self._evidence.append(
            "tool_result", state.principal, state.run_id, evidence_outcome, {"tool": tool_name, "status": status}
        )
        if state.action_hook is not None:
            await record_outcome_async(
                state.action_hook,
                run_id=action_run_id,
                tool_name=tool_name,
                arguments=arguments,
                outcome=outcome,
            )
            if getattr(state.action_hook, "has_failed_outcomes", False) or status in (
                "unknown",
                "reconciliation_required",
                "store_unavailable",
            ):
                state.needs_reconciliation = True
        self.record_tool_result(state, tool_name, outcome)
        return outcome

    async def _lease_refusal(self, state: RunState, tool_name: str, arguments: dict) -> dict | None:
        """Lease-gate one managed protected dispatch; ``None`` = authorized.

        Verifies the CURRENT lease (signature, expiry, deployment binding,
        unconsumed nonce) and that the requested data domain is inside the
        lease's scope. Each lease authorizes exactly one protected
        dispatch, so a multi-action run consumes the current lease and the
        NEXT dispatch waits (bounded) for the channel's next pull to
        install a fresh one — "not yet refreshed" is transient, not a
        denial. After the wait budget the refusal is an explicit,
        evidenced denial with zero business side effects; nonce consumption
        on a scope denial is deliberate conservatism — a used lease
        authorizes nothing further.
        """

        requested_domain = arguments.get("domain")
        denial: dict | None = None
        deadline = time.monotonic() + _LEASE_REFRESH_WAIT_SECONDS
        while True:
            try:
                claims = await self._lease_gate.check() if self._lease_gate is not None else None
                if claims is None:
                    denial = {"status": "authorization", "detail": f"tool {tool_name} withheld: no managed lease gate"}
                elif requested_domain not in (claims.scope or []):
                    denial = {
                        "status": "authorization",
                        "detail": (
                            f"tool {tool_name} withheld: domain {requested_domain!r} is outside the current lease scope"
                        ),
                    }
                else:
                    return None
            except CredentialError as exc:
                denial = {"status": "authorization", "detail": f"tool {tool_name} withheld: {exc}"}
                if time.monotonic() < deadline:
                    await asyncio.sleep(_LEASE_REFRESH_POLL_SECONDS)
                    continue
            break
        self._evidence.append(
            "tool_result",
            state.principal,
            state.run_id,
            "denied",
            {"tool": tool_name, "status": "authorization", "reason": denial["detail"]},
        )
        self.record_tool_result(state, tool_name, denial)
        return denial

    def _consume_wake_grant(self, state: RunState, tool_name: str, digest: str) -> bool:
        """True when the persisted input carries a matching one-shot grant.

        The grant is stamped by the wake application (the recorded decision)
        and consumed by the first matching dispatch of the new attempt; a
        mismatched or missing grant re-parks the run.
        """

        granted = None
        if state.task_ref is not None:
            persisted = self._durable.store.get_task_input(state.task_ref) or {}
            granted = persisted.get("_wake_grant")
        return (
            isinstance(granted, dict) and granted.get("tool") == tool_name and granted.get("arguments_digest") == digest
        )

    def _mark_reconciliation(self, state: RunState, outcome: dict) -> None:
        """A withheld/blocked durable action keeps the task pending reconciliation."""

        if outcome.get("status") in {"needs_review", "reconciliation_required", "conflict", "store_unavailable"}:
            state.needs_reconciliation = True

    def _compile_graph(self) -> CompiledGraphT:
        allowlist = list(self._profile.config.tool_allowlist)
        model_source = self._profile.config.model_backend
        nodes = {
            "model": NodeConfig(
                id="model",
                type=NodeType.CONVERSATION,
                config={"model": model_source, "model_source": model_source},
            ),
        }
        edges = []
        previous = "model"
        for index, tool_name in enumerate(allowlist):
            node_id = f"tool_{index}"
            nodes[node_id] = NodeConfig(id=node_id, type=NodeType.TOOL_CALL, config={"tool_name": tool_name})
            edges.append(Edge(source=previous, target=node_id))
            previous = node_id
        edges.append(Edge(source=previous, target="__end__"))
        return CompiledGraphT(
            nodes=nodes,
            edges=edges,
            channels={
                "input": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
                "plan": ChannelDef(type=ChannelType.LAST_VALUE, default={}),
                "tool_results": ChannelDef(type=ChannelType.TOPIC, default=[]),
            },
            entry_point="model",
            name="runner-preview",
        )

    @property
    def busy(self) -> bool:
        return self._lock.locked() or self._resuming

    @property
    def closing(self) -> bool:
        """Whether shutdown has begun and new work must be refused."""
        return self._closing

    def validate_run_input(self, run_input: dict) -> None:
        """Validate every scheduled tool before accepting any execution."""
        if not isinstance(run_input, dict):
            raise ValueError("run request must be an object")
        if any(key.startswith("_") for key in run_input):
            raise ValueError("internal host fields cannot be supplied in a run request")
        if "input" in run_input and not isinstance(run_input["input"], dict):
            raise ValueError("input must be an object")
        raw = run_input.get("tool_arguments", {})
        if not isinstance(raw, dict):
            raise ValueError("tool_arguments must be an object")
        for name in self._profile.config.tool_allowlist:
            arguments = raw.get(name) if isinstance(raw.get(name), dict) else raw
            for schema in (BUILTIN_TOOL_SCHEMAS[name], self._profile.tool_schemas[name]):
                if next(Draft202012Validator(schema).iter_errors(arguments), None) is not None:
                    raise ValueError(f"invalid arguments for tool {name}")

    def capabilities(self) -> dict:
        supported = {
            "submit": "enforced",
            "read_events_cursor": "enforced",
            "cancel": "cooperative",
            "local_evidence": "enforced",
        }
        if self._durable is not None:
            return {**supported, **UNSUPPORTED_CAPABILITIES, **DURABLE_CAPABILITIES}
        return {**supported, **UNSUPPORTED_CAPABILITIES}

    def validate_durable_input(self, run_input: dict) -> None:
        """Validate trusted persisted input against the admitted execution definition."""
        if run_input.get("_host_definition") != self._definition_digest:
            raise ValueError("execution definition is missing or changed since admission")
        self.validate_run_input({key: value for key, value in run_input.items() if not key.startswith("_")})

    async def submit(
        self,
        principal: str,
        run_input: dict,
        domains: tuple[str, ...] = (),
        *,
        idempotency_key: str | None = None,
    ) -> tuple[str, RunState | None]:
        """Start a run; returns (run_id, None-in-flight-refusal) — serial queue.

        ``principal``/``domains`` come from the server-verified identity at
        the HTTP entry and travel with the run: tool dispatches carry exactly
        this scope, never any request-body self-reported claims. In the
        durable profile the submission registers under an idempotency key
        bound to that verified identity and the canonical request body:
        replays return the original run; a key rebound to a different body
        raises :class:`IdempotencyConflictError`.
        """

        if self._closing or self.busy:
            return "", None
        self.validate_run_input(run_input)
        if self._durable is not None:
            self._admit_protected(run_input)
            submission = self._durable.submit(
                principal=principal, run_input=run_input, idempotency_key=idempotency_key, domains=domains
            )
            run_ref = submission.association.run_ref
            task_ref = submission.association.task_ref
            if submission.replayed:
                # Same key + same body: never a second execution. Report the
                # task's durable state (post-restart runs resolve via the
                # same path because startup reconcile already re-drove them).
                state = self._runs.get(run_ref.id)
                if state is None:
                    state = self._durable_task_state(task_ref, principal, domains, run_ref)
                state.replayed = True
                return run_ref.id, state
        # Reserve admission before yielding to the execution task. Checking
        # the lock only in _execute silently queued concurrent submissions.
        await self._lock.acquire()
        if self._durable is not None:
            state = RunState(
                run_id=run_ref.id,
                status="running",
                principal=principal,
                domains=tuple(domains),
                task_ref=task_ref,
                run_ref=run_ref,
                received_at=self._durable.task_state(task_ref.id).recorded_at,
                idempotency_key=idempotency_key,
            )
            state.action_hook = self._durable.hook_for(task_ref, run_ref)
        else:
            run_id = uuid.uuid4().hex
            state = RunState(
                run_id=run_id,
                status="running",
                principal=principal,
                domains=tuple(domains),
                received_at=_now_iso(),
                idempotency_key=idempotency_key,
            )
        try:
            self._evidence.append(
                "submission", principal, state.run_id, "accepted", {"model_source": self._profile.config.model_backend}
            )
            self._runs[state.run_id] = state
            task = asyncio.create_task(self._execute(state.run_id, state, run_input))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except BaseException:
            self._lock.release()
            raise
        return state.run_id, state

    def _admit_protected(self, run_input: dict) -> None:
        """SC06 admission gate: refuse protected runs while evidence is unavailable.

        The failure carries an explicit category (unwritable vs over-capacity
        policy) and is recorded as an auditable gate denial.
        """

        if any(effect is not ToolSideEffectClass.READONLY for effect in self._side_effects.values()):
            try:
                self._evidence.probe()
            except OSError as exc:
                category = getattr(exc, "category", "unwritable")
                self._evidence.record_gate_failure(category, f"local evidence gate: {exc}")
                raise EvidenceUnavailableError(f"local evidence unavailable ({category}): {exc}") from exc

    def _durable_task_state(
        self, task_ref: BackendRef, principal: str, domains: tuple[str, ...], run_ref: BackendRef
    ) -> RunState:
        status, error, recorded_at = (
            self._durable.run_state(task_ref, run_ref) if self._durable else ("unknown", None, None)
        )
        association = self._durable.association_for_run(run_ref) if self._durable else None
        return RunState(
            run_id=run_ref.id,
            status=status,
            principal=principal,
            domains=tuple(domains),
            task_ref=task_ref,
            run_ref=run_ref,
            replayed=True,
            received_at=recorded_at,
            error=error,
            result_ref=f"runs/{run_ref.id}/artifacts" if status == "succeeded" else None,
            idempotency_key=association.key.key if association is not None else None,
        )

    def resume(self, task_ref: BackendRef, run_ref: BackendRef, run_input: dict) -> str | None:
        """Re-drive a non-terminal durable task after a restart (replay path).

        Returns the run id when dispatched, or ``None`` when the serial slot
        is busy (the caller retries on the next tick) — recovery decisions
        per action come from the ledger inside the normal dispatch gate.
        Managed-issuer tasks belong to :meth:`resume_managed` and are left
        untouched here so the two recovery entries can never fight over one
        task.
        """

        if self._durable is None or self._closing or self.busy:
            return None
        if task_ref.issuer_domain == MANAGED_ISSUER:
            return None
        from hecate_durable.contracts.durable import TaskLifecycleState

        record = self._durable.store.get_task_state(task_ref)
        if record is None or record.lifecycle_state not in {TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING}:
            return None
        # This snapshot is written by DurableRuntime from verified admission
        # context, never from the caller's role/domain claims.
        persisted_input = self._durable.store.get_task_input(task_ref) or {}
        trusted = persisted_input.get("_host_identity") or {}
        principal = trusted.get("principal")
        matches = [identity for identity in self._profile.identities if identity.principal == principal]
        domains = trusted.get("domains")
        if domains is None and len(matches) == 1:
            domains = list(matches[0].domains)
        if not isinstance(domains, list) or not any(set(domains).issubset(i.domains) for i in matches):
            self._durable.finish_run(task_ref, "unknown", needs_reconciliation=True)
            return None
        state = self._schedule_durable_resume(task_ref, run_ref, persisted_input, principal, tuple(domains))
        return state.run_id if state is not None else None

    def resume_managed(self, task_ref: BackendRef, run_ref: BackendRef) -> str | None:
        """Re-drive an accepted managed task after (re)start (step6a loop).

        The task's accept-time identity stamp (verified channel principal +
        operator-configured domains) must match the engine's current managed
        identity exactly — configuration drift stops the task into
        ``reconciliation_required`` instead of re-homing the execution.
        Returns the run id when dispatched, ``None`` when the slot is busy
        or the task did not resumable-check.
        """

        if self._durable is None or self._closing or self.busy:
            return None
        if self._managed_identity is None:
            raise ValueError("resume_managed requires the managed assembly (managed_identity)")
        if task_ref.issuer_domain != MANAGED_ISSUER:
            return None
        from hecate_durable.contracts.durable import TaskLifecycleState

        record = self._durable.store.get_task_state(task_ref)
        if record is None or record.lifecycle_state not in {TaskLifecycleState.QUEUED, TaskLifecycleState.RUNNING}:
            return None
        persisted_input = self._durable.store.get_task_input(task_ref) or {}
        trusted = persisted_input.get("_host_identity") or {}
        principal = trusted.get("principal")
        domains = trusted.get("domains")
        if (
            principal != self._managed_identity.principal
            or not isinstance(domains, list)
            or list(self._managed_identity.domains) != domains
        ):
            self._durable.finish_run(task_ref, "unknown", needs_reconciliation=True)
            return None
        state = self._schedule_durable_resume(task_ref, run_ref, persisted_input, principal, tuple(domains))
        if state is None:
            return None
        state.managed = True
        return state.run_id

    def _schedule_durable_resume(
        self,
        task_ref: BackendRef,
        run_ref: BackendRef,
        run_input: dict,
        principal: str,
        domains: tuple[str, ...],
    ) -> RunState | None:
        """Shared serial-slot dispatch tail for both recovery entries."""

        try:
            self.validate_durable_input(run_input)
        except ValueError:
            self._durable.finish_run(task_ref, "unknown", needs_reconciliation=True)
            return None
        self._admit_protected(run_input)
        state = RunState(
            run_id=run_ref.id,
            status="running",
            principal=principal,
            domains=tuple(domains),
            task_ref=task_ref,
            run_ref=run_ref,
        )
        state.action_hook = self._durable.hook_for(task_ref, run_ref)

        async def _runner() -> None:
            # The slot was free when resume checked (same loop turn, no await
            # between), so this acquire completes immediately; a same-turn
            # contender merely serializes behind the run in flight.
            # _execute's finally releases the slot, exactly like the submit
            # path — no double release here.
            await self._lock.acquire()
            self._resuming = False
            self._runs[state.run_id] = state
            await self._execute(state.run_id, state, run_input, resume=True)

        try:
            self._resuming = True
            task = asyncio.create_task(_runner())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        except BaseException:
            self._resuming = False
            raise
        return state

    def request_cancel(self, run_id: str, *, command_id: str | None = None) -> bool:
        """Request cancellation at the next tool boundary; never revoke a completed call."""
        state = self._runs.get(run_id)
        if state is None or state.status != "running":
            return False
        self._evidence.append("cancel", state.principal, run_id, "requested")
        state.cancel_requested = True
        state.cancel_command_id = command_id
        return True

    def begin_shutdown(self) -> None:
        """Stop admission immediately without promising durable recovery."""
        self._closing = True

    async def close(self) -> None:
        """Stop in-process tasks and retain an honest unknown-outcome record."""
        self.begin_shutdown()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for state in self._runs.values():
            if state.status == "running":
                try:
                    self._evidence.append(
                        "execution",
                        state.principal,
                        state.run_id,
                        OUTCOME_FAILED,
                        {
                            "status": "unknown",
                            "reason": "shutdown before execution task started",
                        },
                    )
                except OSError:
                    logger.exception("Could not record shutdown of run %s", state.run_id)
                state.status = "unknown"
                state.error = "runner shut down; outcome is not established"
        if self._lock.locked():
            self._lock.release()

    async def wait_for(self, run_id: str, timeout: float = 60.0) -> RunState:
        for _ in range(int(timeout / 0.05)):
            state = self._runs.get(run_id)
            if state is not None and state.status != "running":
                return state
            await asyncio.sleep(0.05)
        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(run_id)
        return state

    def wait_for_sync(self, run_id: str, timeout: float = 60.0) -> RunState:
        """Blocking variant for threads driving the server from outside the loop."""

        if self._loop is not None and self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self.wait_for(run_id, timeout), self._loop)
            return future.result(timeout + 5)
        return asyncio.run(self.wait_for(run_id, timeout))

    def get_state(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def _append_event(self, state: RunState, event: dict) -> None:
        if self._durable is not None and state.task_ref is not None and state.run_ref is not None:
            try:
                self._durable.emit_execution_event(
                    task_ref=state.task_ref,
                    run_ref=state.run_ref,
                    source_sequence=len(state.events) + 1,
                    event_type="tool_result" if event.get("type") == "tool_result" else "engine_event",
                    payload={"event": event},
                )
            except Exception:
                state.needs_reconciliation = True
                raise
        state.events.append(event)

    async def _execute(self, run_id: str, state: RunState, run_input: dict, *, resume: bool = False) -> None:
        try:
            if self._durable is not None and state.task_ref is not None:
                # RUNNING→RUNNING on resume is an absorbing no-op; a fresh
                # submission moves QUEUED→RUNNING here, never inside submit.
                self._durable.begin_run(state.task_ref)
            # The recovery session is stable within one attempt; each new
            # attempt has its own session, so persisted checkpoints carry
            # across restart-driven resumes. The in-flight run id keeps
            # action-key identity per attempt.
            session_id = (
                self._checkpoint_session(state.task_ref, state.run_ref)
                if state.task_ref is not None
                else uuid.uuid5(uuid.NAMESPACE_URL, f"runner-run:{run_id}")
            )
            # step6d: a resumed interrupted attempt continues from the last
            # persisted superstep instead of replaying the graph from the
            # top; the ledger still arbitrates every action.
            resume_value = (
                f"resume:{run_id}" if (resume and self._resume_from_checkpoint(state.task_ref, state.run_ref)) else None
            )
            try:

                def observe(event: dict) -> RuntimeEventDecision:
                    self._append_event(state, event)
                    if state.needs_reconciliation or state.dispatch_denied:
                        return RuntimeEventDecision.STOP_UNKNOWN
                    if state.wait_request is not None:
                        # step6c: the dispatch boundary requested a persistent
                        # wait — stop the graph instead of running further
                        # nodes; _execute parks the task below.
                        return RuntimeEventDecision.STOP_UNKNOWN
                    return RuntimeEventDecision.CONTINUE

                result = await runtime_execution_service.execute(
                    RuntimeExecutionRequest(
                        runtime=self._assemble_runtime(state),
                        session_id=session_id,
                        initial_input={"input": run_input},
                        observer=observe,
                        should_cancel=lambda: state.cancel_requested,
                        resume_value=resume_value,
                    )
                )
                if state.wait_request is not None:
                    # step6c: persist the wait and hold the task — the
                    # service's UNKNOWN classification is the stop vehicle,
                    # not the run's truth.
                    token = self._durable.park_wait(
                        state.task_ref,
                        state.run_ref,
                        wake_kind=state.wait_request["wake_kind"],
                        contract_ref=state.wait_request["contract_ref"],
                        expires_at=state.wait_request.get("expires_at"),
                    )
                    state.wait_token = token
                    state.status = (
                        "waiting_input" if state.wait_request["wake_kind"] == "provide_input" else "waiting_approval"
                    )
                    self._evidence.append(
                        "execution",
                        state.principal,
                        run_id,
                        "waiting",
                        {"status": state.status, "tool": state.wait_request["contract_ref"].get("tool")},
                    )
                    return
                final_status = result.state.value
                if state.dispatch_denied:
                    final_status = "failed"
                if result.state.value == "failed":
                    state.error = "execution failed; inspect local service logs"
                    logger.warning("Runner execution %s failed: %s", run_id, result.error)
            except RuntimeScheduleCancelledError:
                final_status = "unknown"
                state.error = "runner shut down; outcome is not established"
            result_ref = f"runs/{run_id}/artifacts" if final_status == "succeeded" else None
            self._evidence.append(
                "execution",
                state.principal,
                run_id,
                "ok" if final_status == "succeeded" else OUTCOME_FAILED,
                {
                    "status": final_status,
                    "model_source": self._profile.config.model_backend,
                    "result_ref": result_ref,
                },
            )
            state.result_ref = result_ref
            state.status = final_status
        except Exception:
            logger.exception("Runner execution %s could not retain evidence", run_id)
            state.status = "unknown"
            state.result_ref = None
            state.error = "local evidence write failed; outcome requires inspection"
        finally:
            if (
                self._durable is not None
                and state.task_ref is not None
                and state.status
                in (
                    "waiting_input",
                    "waiting_approval",
                )
            ):
                # Parked (step6c): the wait transition already committed on
                # the signal path; no terminal convergence happens here.
                pass
            elif self._durable is not None and state.task_ref is not None:
                hook_failed = getattr(state.action_hook, "has_failed_outcomes", False)
                try:
                    record = self._durable.finish_run(
                        state.task_ref,
                        state.status,
                        needs_reconciliation=state.needs_reconciliation or bool(hook_failed),
                        error=state.error,
                        cancel_command_id=state.cancel_command_id,
                    )
                    state.status = record.lifecycle_state.value
                except Exception:
                    logger.exception("Could not persist final task state for %s", run_id)
                    state.status = "unknown"
                if state.cancel_command_id is not None:
                    # Cancel receipts converge only on actual effect: applied
                    # when the cooperative boundary took hold, rejected when
                    # the run had already finished another way.
                    try:
                        if state.status in {"succeeded", "failed"}:
                            self._durable.cancel_rejected(state.cancel_command_id)
                    except Exception:
                        logger.exception("Could not converge cancel command %s", state.cancel_command_id)
            self._lock.release()

    def record_tool_result(self, state: RunState, tool_name: str, outcome: dict) -> None:
        self._append_event(state, {"type": "tool_result", "tool": tool_name, "outcome": outcome})

    def _assemble_runtime(self, state: RunState):
        from hecate_runtime.pregel import PregelRuntime

        return PregelRuntime(self._graph, _FanOutWorker(self, state), self._checkpoint_store)


class _FanOutWorker(Worker):
    """Routes each node to the model or read-tool worker by node id."""

    def __init__(self, engine: ExecutionEngine, state: RunState) -> None:
        super().__init__()
        self._engine = engine
        self._state = state

    async def execute(
        self, node_id: str, node_config: dict, channel_snapshot: dict, execution_context: dict | None = None
    ) -> WorkerResult:
        if self._state.cancel_requested:
            raise CooperativeCancellationError
        if node_id == "model":
            worker: Worker = ModelNodeWorker(self._engine._profile)
        else:
            worker = ReadToolNodeWorker(
                lambda arguments, _tool=node_config["tool_name"], _state=self._state: self._engine._dispatch_tool(
                    _state, _tool, arguments
                )
            )
        return await worker.execute(node_id, node_config, channel_snapshot, execution_context)


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()
