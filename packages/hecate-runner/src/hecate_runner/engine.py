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
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from hecate_durable.contracts.credentials import CredentialError
from hecate_durable.contracts.durable import canonical_request_digest
from hecate_durable.contracts.references import BackendRef
from hecate_durable.contracts.tools import ToolSideEffectClass
from hecate_durable.storage.lease import LeaseHandle
from hecate_runtime.action_ledger import ActionLedgerHook, tool_arguments_digest
from hecate_runtime.checkpoint import InMemoryCheckpointStore
from hecate_runtime.errors import ApprovalWaitSignal
from hecate_runtime.execution_driver import PregelExecutionDriver
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
from jsonschema import Draft202012Validator, SchemaError

from .durable import (
    DurableRuntime,
    action_key_for,
    gate_dispatch_async,
    is_decided_action,
    record_outcome_async,
)
from .evidence import OUTCOME_FAILED, EvidenceStore
from .managed import MANAGED_ISSUER, LeaseGate, LeaseRefusedError, ManagedIdentity
from .profile import BUILTIN_TOOL_SCHEMAS, Profile, ProfileError, _check_local_schema_refs

# step7 2.5: dispatch outcome status → governance error category. The four
# failure classes stay distinguishable in evidence; unknown outcomes never
# retry blindly and reconcile instead.
ERROR_CATEGORY_BY_STATUS = {
    "schema_invalid": "schema_invalid",
    "authorization": "policy_denied",
    "business": "business_rejected",
    "transient": "technical_failure",
    "unsupported": "technical_failure",
    "unknown": "outcome_unknown",
    "store_unavailable": "outcome_unknown",
    "reconciliation_required": "outcome_unknown",
    "output_invalid": "outcome_unknown",
}

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
        usage = None
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
                usage = payload.get("usage")
        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "plan": {
                    "tools": list(config.tool_allowlist),
                    "prompt": prompt,
                    "model_source": node_config.get("model_source", "stub"),
                    "content": content,
                    "usage": usage,
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
    model_invocation_id: str | None = None
    # Delivery-acceptance outcome of a controller final answer: content,
    # acceptance record and (when checks withheld success) the error facts.
    # finish_run persists it with the terminal state in one transaction.
    result: dict | None = None
    controller_lease: LeaseHandle | None = None
    local_approval_refs: dict[str, str] = field(default_factory=dict)
    # Durable event-stream sequence for this run (single writer: this
    # engine). Seeded from the persisted stream on re-entry so a resumed
    # run continues the sequence instead of colliding with it.
    event_seq: int = 0


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
        lease_refresh_wait_seconds: float = _LEASE_REFRESH_WAIT_SECONDS,
        approval_gate: Any = None,
        approval_finder: Any = None,
        budget_gate: Any = None,
        governance_service: Any = None,
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
        # step7 3.3: optional approval authority gate — (tool, digest,
        # approval_ref) -> bool. Injected by the local policy assembly (5.x);
        # None keeps the step6 grant semantics unchanged.
        self._approval_gate = approval_gate
        # step7 I-Cb: optional approval locator — ActionRequest -> approval_ref
        # | None. Lets a valid pre-approved binding authorize a protected
        # dispatch before the lease gate (disconnect bridge).
        self._approval_finder = approval_finder
        # step7 4.2: optional budget gate for metered model calls.
        self._budget_gate = budget_gate
        # step7 5.2: optional local governance service (shared governance
        # seam over the runner's local policy profile). ALL dispatches —
        # readonly included — pass through its judgment.
        self._governance_service = governance_service
        # Declared renewal/disconnect window (step6b): how long a protected
        # dispatch waits for a fresh lease when the previous one is consumed
        # before the refusal becomes final. Profile-injected; the module
        # constant is only the default.
        self._lease_refresh_wait_seconds = float(lease_refresh_wait_seconds)
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
        # Explicit opt-in: drive the shared graph-free agent controller
        # (durable accepted-decision chain as the continuation position)
        # instead of the fixed preview graph. Requires the durable profile
        # (validated at load time).
        self._controller_loop = profile.config.controller_loop
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

    async def _dispatch_tool(
        self, state: RunState, tool_name: str, arguments: dict, *, accepted_context: dict | None = None
    ) -> dict:
        """Run one tool with the run's server-verified identity scope.

        In the durable profile every dispatch first passes the persistent
        ledger gate (recovery verdict + atomic claim) and, for protected
        tools, the evidence-writability gate; the real outcome is mirrored
        back to the ledger after the business call.
        """

        # Accepted actions already have their decision-scoped authoritative
        # claim. The legacy runner key (one per tool/run) would create a
        # second ledger entry and collapse distinct calls to the same tool.
        ledger_hook = None if accepted_context is not None else state.action_hook
        invocation_id = accepted_context.get("invocation_id") if accepted_context is not None else None
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
                ledger_hook is not None
                and await is_decided_action(
                    ledger_hook,
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
            refusal = await self._lease_refusal(state, tool_name, arguments, invocation_id=invocation_id)
            if refusal is not None:
                state.dispatch_denied = True
                return refusal
        if ledger_hook is not None and await is_decided_action(
            ledger_hook,
            run_id=action_run_id,
            tool_name=tool_name,
            arguments=arguments,
            side_effect_class=effect,
        ):
            withheld, blocked = await gate_dispatch_async(
                ledger_hook,
                run_id=action_run_id,
                tool_name=tool_name,
                arguments=arguments,
                side_effect_class=effect,
            )
            replay = withheld if withheld is not None else blocked
            if replay is not None:
                self._mark_reconciliation(state, replay)
                self.record_tool_result(state, tool_name, replay)
                return replay
        governance_refusal = await self._governance_refusal(
            state, tool_name, arguments, effect, invocation_id=invocation_id
        )
        if governance_refusal is not None:
            state.dispatch_denied = governance_refusal.get("status") != "awaiting_approval"
            self.record_tool_result(state, tool_name, governance_refusal)
            return governance_refusal
        if (
            tool_name in self._approval_tools
            and not (accepted_context is not None and accepted_context.get("approval_ref"))
            and (invocation_id or action_key_for(action_run_id, tool_name)) not in state.local_approval_refs
            and not await self._consume_wake_grant(state, tool_name, digest, arguments)
        ):
            # A wait is not an action claim: no business dispatch has begun.
            state.wait_request = {
                "wake_kind": "resume",
                "contract_ref": {"tool": tool_name, "arguments_digest": digest, "kind": "approval"},
            }
            outcome = {"status": "awaiting_approval", "detail": "parked in waiting_approval; resume to dispatch"}
            self.record_tool_result(state, tool_name, outcome)
            return outcome
        if ledger_hook is not None:
            withheld, claim_blocked = await gate_dispatch_async(
                ledger_hook,
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
        if (ledger_hook is not None or accepted_context is not None) and effect is not ToolSideEffectClass.READONLY:
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
        schema_detail = self._input_schema_violation(tool_name, arguments)
        if schema_detail is not None:
            # step7 2.5: schema mismatch is rejected BEFORE the business call
            # (after ledger arbitration, so recovery of already-claimed
            # actions is never hijacked by a re-validation).
            outcome = {"status": "schema_invalid", "detail": schema_detail}
            self.record_tool_result(state, tool_name, outcome)
            return outcome
        if arguments.get("domain") not in state.domains:
            outcome = {"status": "authorization", "detail": "requested domain is outside the trusted identity scope"}
        else:
            try:
                outcome = await self._tool_dispatch(tool_name, arguments, state.principal, list(state.domains))
            except Exception:
                if effect is not ToolSideEffectClass.READONLY:
                    state.needs_reconciliation = True
                raise
        output_detail = self._output_schema_violation(tool_name, outcome)
        if output_detail is not None:
            # step7 2.5: an off-contract result is not consumed as success —
            # the side effect already happened, so the outcome is unknown and
            # the run reconciles instead of trusting the malformed payload.
            outcome = {
                "status": "output_invalid",
                "detail": f"result violates the declared output schema: {output_detail}",
                "raw_status": str(outcome.get("status") or ""),
            }
            if effect is not ToolSideEffectClass.READONLY:
                state.needs_reconciliation = True
        status = str(outcome.get("status") or "")
        if status == "authorization":
            state.dispatch_denied = True
        if status == "input_required" and not await self._consume_wake_grant(state, tool_name, digest, arguments):
            contract_value: object = outcome.get("contract")
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
                    "contract": contract_value,
                },
            }
            self.record_tool_result(state, tool_name, outcome)
            return outcome
        evidence_outcome = "ok" if status == "ok" else "denied" if status == "authorization" else "failed"
        detail: dict[str, str] = {"tool": tool_name, "status": status}
        category = ERROR_CATEGORY_BY_STATUS.get(status)
        if category is not None:
            detail["error_category"] = category
        self._evidence.append("tool_result", state.principal, state.run_id, evidence_outcome, detail)
        if ledger_hook is not None:
            await record_outcome_async(
                ledger_hook,
                run_id=action_run_id,
                tool_name=tool_name,
                arguments=arguments,
                outcome=outcome,
            )
            if getattr(ledger_hook, "has_failed_outcomes", False) or status in (
                "unknown",
                "reconciliation_required",
                "store_unavailable",
            ):
                state.needs_reconciliation = True
        self.record_tool_result(state, tool_name, outcome)
        return outcome

    async def _lease_refusal(
        self, state: RunState, tool_name: str, arguments: dict, *, invocation_id: str | None = None
    ) -> dict | None:
        """Lease-gate one managed protected dispatch; ``None`` = authorized.

        Authorization order (step7 I-Cb): a valid local approval binding
        authorizes first — it consumes the binding's one-time CAS and never
        the lease nonce, bridging disconnects for pre-approved actions.
        Otherwise the CURRENT lease is verified (signature, expiry,
        deployment binding, unconsumed nonce) and the requested data domain
        must be inside the lease's scope. Each lease authorizes exactly one
        protected dispatch, so a multi-action run consumes the current
        lease and the NEXT dispatch waits (bounded) for the channel's next
        pull to install a fresh one — "not yet refreshed" is transient, not
        a denial. Structural refusals (identity/policy-version mismatch)
        deny immediately: no pull will heal them. After the wait budget the
        refusal is an explicit, evidenced denial with zero business side
        effects; nonce consumption on a scope denial is deliberate
        conservatism — a used lease authorizes nothing further.
        """

        if await self._authorize_via_local_approval(state, tool_name, arguments, invocation_id=invocation_id):
            return None

        requested_domain = arguments.get("domain")
        denial: dict | None = None
        deadline = time.monotonic() + self._lease_refresh_wait_seconds
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
            except LeaseRefusedError as exc:
                # Structural: waiting cannot heal it — deny now.
                denial = {"status": "authorization", "detail": f"tool {tool_name} withheld: {exc}"}
                break
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

    async def _authorize_via_local_approval(
        self, state: RunState, tool_name: str, arguments: dict, *, invocation_id: str | None = None
    ) -> bool:
        """True when a valid local approval binding authorizes this dispatch.

        The finder locates an unconsumed binding matching the verified
        action request (subject, tool, parameter digest); the approval gate
        is the authority — it re-validates the binding under trusted time
        and consumes it atomically. Authorization is evidenced with its
        source (``local_approval``); the lease nonce is not consumed, so
        this path bridges disconnects without spending lease validity.
        """

        if self._approval_finder is None or self._lease_gate is None:
            return False
        if not self._lease_gate.approval_scope_allows(arguments.get("domain")):
            return False
        from hecate_governance.action import target_resources_for
        from hecate_governance.approval import ActionRequest
        from hecate_governance.principal import ActionPrincipal, PrincipalSource

        digest = canonical_request_digest(arguments)
        principal = ActionPrincipal.from_verified_claims(
            {"sub": state.principal}, source=PrincipalSource.RUNNER_LOCAL, domains=tuple(state.domains)
        )
        persisted = self._durable.store.get_task_input(state.task_ref) or {} if self._durable and state.task_ref else {}
        origins = persisted.get("_action_origins") or {}
        request = ActionRequest(
            invocation_id=invocation_id or action_key_for(origins.get(tool_name, state.run_id), tool_name),
            principal_fingerprint=principal.fingerprint(),
            tool_name=tool_name,
            arguments_digest=digest,
            target_resources=target_resources_for(arguments),
            domains=tuple(state.domains),
            task_scope=state.task_ref.id if state.task_ref else None,
        )
        approval_ref = await self._approval_finder(request)
        if approval_ref is None:
            return False
        if self._approval_gate is None or not await self._approval_gate(approval_ref, request):
            return False
        state.local_approval_refs[request.invocation_id] = approval_ref
        try:
            self._evidence.append(
                "approval_basis",
                state.principal,
                state.run_id,
                "granted",
                {"tool": tool_name, "approval_ref": approval_ref, "source": "local_approval"},
            )
        except Exception:  # noqa: BLE001 — evidence failure handled by its own gate
            logger.debug("approval basis evidence failed", exc_info=True)
        return True

    async def _consume_wake_grant(
        self, state: RunState, tool_name: str, digest: str, arguments: dict, *, invocation_id: str | None = None
    ) -> bool:
        """True when the persisted input carries a matching one-shot grant.

        The grant is stamped by the wake application (the recorded decision)
        and consumed by the first matching dispatch of the new attempt; a
        mismatched or missing grant re-parks the run.

        step7 3.3: when an approval authority gate is configured, the grant
        must carry an approval reference the gate accepts — a bare wake token
        never authorizes a protected dispatch. The accepted basis is recorded
        in evidence; a refused or missing reference re-parks the run without
        consuming the approval.
        """

        granted = None
        if state.task_ref is not None:
            persisted = self._durable.store.get_task_input(state.task_ref) or {}
            granted = persisted.get("_wake_grant")
        if not (
            isinstance(granted, dict) and granted.get("tool") == tool_name and granted.get("arguments_digest") == digest
        ):
            return False
        approval_ref = granted.get("approval_ref")
        if self._approval_gate is not None:
            if approval_ref is None:
                return False
            from hecate_governance.action import target_resources_for
            from hecate_governance.approval import ActionRequest
            from hecate_governance.principal import ActionPrincipal, PrincipalSource

            principal = ActionPrincipal.from_verified_claims(
                {"sub": state.principal}, source=PrincipalSource.RUNNER_LOCAL, domains=tuple(state.domains)
            )
            origins = persisted.get("_action_origins") or {}
            request = ActionRequest(
                invocation_id=invocation_id or action_key_for(origins.get(tool_name, state.run_id), tool_name),
                principal_fingerprint=principal.fingerprint(),
                tool_name=tool_name,
                arguments_digest=digest,
                target_resources=target_resources_for(arguments),
                domains=tuple(state.domains),
                task_scope=state.task_ref.id if state.task_ref else None,
            )
            accepted = await self._approval_gate(approval_ref, request)
            if not accepted:
                return False
            try:
                self._evidence.append(
                    "approval_basis",
                    state.principal,
                    state.run_id,
                    "granted",
                    {"tool": tool_name, "approval_ref": approval_ref},
                )
            except Exception:  # noqa: BLE001 — evidence failure handled by its own gate
                logger.debug("approval basis evidence failed", exc_info=True)
        return True

    async def _governance_refusal(
        self,
        state: RunState,
        tool_name: str,
        arguments: dict,
        effect: ToolSideEffectClass,
        *,
        invocation_id: str | None = None,
    ) -> dict | None:
        """Local governance judgment for one dispatch (step7 5.2).

        Runs for every dispatch — readonly included; only the managed lease
        gate sits before it. Denials leave zero business calls and explicit
        evidence. ``REQUIRE_APPROVAL`` parks the run via the existing wait
        machinery when the manifest has not already marked the tool.
        """
        if self._governance_service is None:
            return None
        from datetime import UTC, datetime

        from hecate_governance.action import ActionDecision, target_resources_for
        from hecate_governance.policy import PolicyContext as GovContext
        from hecate_governance.principal import ActionPrincipal, PrincipalSource

        principal = ActionPrincipal.from_verified_claims(
            {"sub": state.principal},
            source=PrincipalSource.MANAGED_DELIVERY if state.managed else PrincipalSource.RUNNER_LOCAL,
            domains=tuple(state.domains),
        )
        persisted = self._durable.store.get_task_input(state.task_ref) or {} if self._durable and state.task_ref else {}
        grant = persisted.get("_wake_grant") or {}
        approval_ref = grant.get("approval_ref") if grant.get("tool") == tool_name else None
        origins = persisted.get("_action_origins") or {}
        invocation_id = invocation_id or action_key_for(origins.get(tool_name, state.run_id), tool_name)
        approval_ref = state.local_approval_refs.get(invocation_id, approval_ref)
        gov_context = GovContext(
            principal=principal,
            tool_name=tool_name,
            arguments=arguments,
            arguments_digest=tool_arguments_digest(arguments),
            invocation_id=invocation_id,
            target_resources=target_resources_for(arguments),
            task_ref=state.task_ref.id if state.task_ref else None,
            tool_side_effect=effect.value,
            managed_execution=state.managed,
            approval_ref=approval_ref,
        )
        decision = await self._governance_service.authorize(
            gov_context, approval_ref=approval_ref, now=datetime.now(UTC), evidence_writable=True
        )
        if decision.allowed:
            return None
        if decision.policy_outcome is not None and decision.policy_outcome.decision is ActionDecision.REQUIRE_APPROVAL:
            state.wait_request = {
                "wake_kind": "resume",
                "contract_ref": {
                    "tool": tool_name,
                    "arguments_digest": tool_arguments_digest(arguments),
                    "kind": "approval",
                },
            }
            return {"status": "awaiting_approval", "detail": decision.reason}
        try:
            self._evidence.append(
                "policy_denial",
                state.principal,
                state.run_id,
                "denied",
                {"tool": tool_name, "reason": decision.reason},
            )
        except Exception:  # noqa: BLE001 — evidence failure must not mask the denial
            logger.debug("policy denial evidence failed", exc_info=True)
        return {
            "status": "authorization",
            "detail": f"local policy denied: {decision.reason}",
        }

    async def _reserve_model_budget(self, state: RunState) -> WorkerResult | None:
        """Reserve budget for one metered model call; None = proceed.

        A refusal records explicit evidence and stops the run with a budget
        error — never a silent skip.
        """
        if self._budget_gate is None:
            return None
        from hecate_governance.usage import ReservationStatus

        key = f"model:{state.run_id}:{uuid.uuid4()}"
        state.model_invocation_id = key
        outcome = await self._budget_gate.ensure_budget(
            key, 4096 if self._profile.config.model_backend == "endpoint" else 1
        )
        if outcome.status is not ReservationStatus.RESERVED:
            try:
                self._evidence.append(
                    "budget",
                    state.principal,
                    state.run_id,
                    "denied",
                    {"reason": "budget exhausted or reservation refused", "remaining": outcome.remaining},
                )
            except Exception:  # noqa: BLE001 — evidence failure must not mask the refusal
                logger.debug("budget evidence failed", exc_info=True)
            from hecate_runner.budget import BudgetExhaustedError

            return WorkerResult(
                node_id="model",
                error=BudgetExhaustedError("metered model call refused by local budget"),
            )
        return None

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

    def _input_schema_violation(self, tool_name: str, arguments: dict) -> str | None:
        """Return the first schema violation for dispatch arguments, if any.

        Mirrors the admission-time pair (builtin schema + profile-declared
        schema); a tool without declared schemas is not constrained here.
        """
        schemas = []
        builtin = BUILTIN_TOOL_SCHEMAS.get(tool_name)
        if builtin is not None:
            schemas.append(builtin)
        declared = self._profile.tool_schemas.get(tool_name)
        if declared is not None:
            schemas.append(declared)
        for schema in schemas:
            error = next(Draft202012Validator(schema).iter_errors(arguments), None)
            if error is not None:
                return f"invalid arguments for tool {tool_name}: {error.message}"
        return None

    def _output_schema_violation(self, tool_name: str, outcome: dict) -> str | None:
        """Return the first output-schema violation for a dispatch result."""
        schema = getattr(self._profile, "tool_output_schemas", {}).get(tool_name)
        if schema is None:
            return None
        result = outcome.get("result")
        error = next(Draft202012Validator(schema).iter_errors(result), None)
        return error.message if error is not None else None

    def validate_run_input(self, run_input: dict) -> None:
        """Validate every scheduled tool before accepting any execution.

        The controller-loop binding skips the fixed tool-argument pre-check:
        the model chooses arguments per turn and the dispatch boundary
        validates each one against the same schemas (``_dispatch_tool``);
        pre-validating a caller-supplied argument snapshot would re-impose
        the fixed-plan admission this binding replaces.
        """
        if not isinstance(run_input, dict):
            raise ValueError("run request must be an object")
        if any(key.startswith("_") for key in run_input):
            raise ValueError("internal host fields cannot be supplied in a run request")
        if "input" in run_input and not isinstance(run_input["input"], dict):
            raise ValueError("input must be an object")
        raw = run_input.get("tool_arguments", {})
        if not isinstance(raw, dict):
            raise ValueError("tool_arguments must be an object")
        if isinstance(run_input.get("controller"), dict) and "timer_wait" in run_input["controller"]:
            raise ValueError("controller.timer_wait is unsupported by Runner")
        if self._controller_loop:
            from hecate_runtime.agent_controller import ControllerContinuationPolicy, ControllerLimits, NoProgressPolicy
            from hecate_runtime.delivery_acceptance import validate_acceptance_declaration

            schema = run_input.get("output_schema")
            if schema is not None:
                if not isinstance(schema, dict):
                    raise ValueError("output_schema must be an object")
                try:
                    Draft202012Validator.check_schema(schema)
                    _check_local_schema_refs(schema)
                except (SchemaError, ProfileError, ValueError) as exc:
                    raise ValueError(f"invalid output_schema: {exc}") from exc
            acceptance = run_input.get("acceptance")
            if acceptance is not None:
                if not isinstance(acceptance, dict):
                    raise ValueError("acceptance must be an object")
                try:
                    validate_acceptance_declaration(acceptance)
                except ValueError as exc:
                    raise ValueError(f"invalid acceptance declaration: {exc}") from exc
            harness = run_input.get("harness")
            if harness is not None:
                from hecate_runtime.agent_harness import validate_harness_declaration

                try:
                    # Runner callers author the full semantic section set —
                    # there is no agent persona here to derive it from.
                    validate_harness_declaration(harness)
                except ValueError as exc:
                    raise ValueError(f"invalid harness declaration: {exc}") from exc
            controller = run_input.get("controller", {})
            if not isinstance(controller, dict):
                raise ValueError("controller must be an object")
            ControllerLimits(max_turns=controller.get("max_turns", 16), deadline=controller.get("deadline"))
            progress_policy = NoProgressPolicy.from_declaration(controller.get("no_progress"))
            if progress_policy is not None:
                progress_policy.validate_observations(self._controller_readonly_observations())
            # A bounded continuation corrects a delivery against a FROZEN
            # standard: without a declared acceptance block there is
            # nothing to correct against, so the combination is refused at
            # admission rather than becoming an unbounded revision channel.
            continuation_policy = ControllerContinuationPolicy.from_declaration(controller.get("continuation"))
            if continuation_policy is not None and acceptance is None:
                raise ValueError("controller.continuation requires a declared acceptance block")
            return
        if run_input.get("harness") is not None:
            raise ValueError("harness composition requires the controller loop")
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

    def _controller_readonly_observations(self) -> tuple[str, ...]:
        """Attest admitted manifest reads and the native read-only adapter."""
        return tuple(
            name
            for name in self._profile.config.tool_allowlist
            if self._side_effects.get(name) is ToolSideEffectClass.READONLY
            or (name == "query_inventory" and name not in self._side_effects)
        )

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
        result = None
        if self._durable is not None:
            try:
                result = self._durable.read_result(task_ref, run_ref)
            except Exception:
                status = "reconciliation_required"
                error = "delivery acceptance history is unreadable; manual reconciliation required"
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
            result=result,
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
                state.event_seq += 1
                self._durable.emit_execution_event(
                    task_ref=state.task_ref,
                    run_ref=state.run_ref,
                    source_sequence=state.event_seq,
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
                if self._controller_loop and state.task_ref is not None:
                    # Opt-in execution binding: the shared graph-free
                    # controller over the durable accepted-decision chain.
                    await self._run_controller(state, run_id, run_input)
                    if state.wait_request is not None:
                        self._persist_wait(state, run_id)
                    return

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
                    self._persist_wait(state, run_id)
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
            elif (
                self._durable is not None
                and state.task_ref is not None
                and (not self._controller_loop or state.controller_lease is not None)
            ):
                hook_failed = getattr(state.action_hook, "has_failed_outcomes", False)
                try:
                    record = self._durable.finish_run(
                        state.task_ref,
                        state.status,
                        needs_reconciliation=state.needs_reconciliation or bool(hook_failed),
                        error=state.error,
                        cancel_command_id=state.cancel_command_id,
                        result=state.result,
                        lease=state.controller_lease,
                    )
                    state.status = record.lifecycle_state.value
                except Exception:
                    logger.exception("Could not persist final task state for %s", run_id)
                    state.status = "unknown"
                    state.result_ref = None
                if state.cancel_command_id is not None:
                    # Cancel receipts converge only on actual effect: applied
                    # when the cooperative boundary took hold, rejected when
                    # the run had already finished another way.
                    try:
                        if state.status in {"succeeded", "failed"}:
                            self._durable.cancel_rejected(state.cancel_command_id)
                    except Exception:
                        logger.exception("Could not converge cancel command %s", state.cancel_command_id)
            try:
                if self._durable is not None and state.controller_lease is not None:
                    handle = state.controller_lease
                    self._durable.store.leases.release(
                        handle.lease_key,
                        handle.holder,
                        fencing_token=handle.fencing_token,
                    )
            finally:
                self._lock.release()

    def record_tool_result(self, state: RunState, tool_name: str, outcome: dict) -> None:
        self._append_event(state, {"type": "tool_result", "tool": tool_name, "outcome": outcome})

    def _persist_wait(self, state: RunState, run_id: str) -> None:
        """step6c: persist the wait and hold the task (shared by both paths)."""

        wait_request = state.wait_request or {}
        token = self._durable.park_wait(
            state.task_ref,
            state.run_ref,
            wake_kind=wait_request["wake_kind"],
            contract_ref=wait_request["contract_ref"],
            expires_at=wait_request.get("expires_at"),
        )
        state.wait_token = token
        state.status = "waiting_input" if wait_request["wake_kind"] == "provide_input" else "waiting_approval"
        self._evidence.append(
            "execution",
            state.principal,
            run_id,
            "waiting",
            {"status": state.status, "tool": (wait_request.get("contract_ref") or {}).get("tool")},
        )

    def _controller_tool_definitions(self) -> dict[str, dict]:
        """Function-calling definitions for the allowlisted tools."""

        definitions: dict[str, dict] = {}
        for name in self._profile.config.tool_allowlist:
            schema = (
                self._profile.tool_schemas.get(name)
                or BUILTIN_TOOL_SCHEMAS.get(name)
                or {
                    "type": "object",
                    "properties": {},
                }
            )
            definitions[name] = {"type": "function", "function": {"name": name, "parameters": schema}}
        return definitions

    async def _run_controller(self, state: RunState, run_id: str, run_input: dict) -> None:
        """Drive the shared agent controller for one durable task dispatch.

        The continuation position is the durable accepted-decision chain
        keyed by the task's stable run; a restart replays committed turns
        from storage and continues at the first uncommitted one. Input
        history is the run's durable revision records: each consumed input
        wake folds at its causal turn boundary and every decision pins the
        revisions it saw, so multiple waits and inputs recover in their
        true order. Dispatch fencing uses a real task-dispatch lease so
        decision commits and action dispatches validate ownership exactly
        like the platform path; the lease is released when the dispatch
        ends. A park-worthy dispatch outcome raises the durable wait
        signal, which this method converts into ``state.wait_request`` so
        the shared park flow commits it; the wake re-drives the same
        replay.
        """

        import uuid as _uuid

        from hecate_durable.contracts.references import RefKind
        from hecate_durable.runtime_hook import SqlAcceptedDecisionStore, SqlActionLedgerHook
        from hecate_durable.storage.lease import task_dispatch_lease_key
        from hecate_runtime.accepted_decision import DecisionOwnership, RunRef
        from hecate_runtime.agent_controller import (
            AgentController,
            ControllerExecutionDriver,
            ControllerLimits,
            ControllerRunSpec,
            PortTurnResponderSource,
        )
        from hecate_runtime.agent_harness import HarnessSection, declared_harness
        from hecate_runtime.agent_turn import AgentTurn
        from hecate_runtime.context_policy import ContextChainFactory
        from hecate_runtime.delivery_acceptance import declared_acceptance
        from hecate_runtime.harness_turn import HarnessTurnPipeline
        from hecate_runtime.tool_call_executor import ToolCallExecutor

        # The persisted admission is detached from caller-owned request
        # dictionaries that may change while model work is in flight.
        persisted_input = self._durable.store.get_task_input(state.task_ref) or {}
        run_input = {key: value for key, value in persisted_input.items() if not key.startswith("_")}
        durable_run = self._durable.store.run_for_task(state.task_ref)
        controller_run = RunRef(
            issuer_domain=durable_run.issuer_domain if durable_run else "hecate-runner",
            run_id=durable_run.id if durable_run else run_id,
        )
        # Continue the run's durable event sequence: a re-entry (wake or
        # crash recovery) must append after the stream it already wrote.
        cursor, has_more = 0, True
        while has_more:
            page = self._durable.read_execution_events(durable_run, cursor=cursor, limit=100) if durable_run else None
            if page is None:
                break
            cursor, has_more = page.next_cursor, page.has_more
        state.event_seq = cursor
        lease_key = task_dispatch_lease_key(state.task_ref.issuer_domain, state.task_ref.id)
        holder = f"runner-controller:{_uuid.uuid4().hex}"
        handle = self._durable.store.leases.acquire(lease_key, holder, 120)
        if handle is None:
            state.status = "failed"
            state.error = "controller dispatch lease unavailable"
            return
        ownership = DecisionOwnership(handle.lease_key, handle.fencing_token, handle.holder)
        state.controller_lease = handle
        hook = SqlActionLedgerHook(
            self._durable.store,
            task_ref=state.task_ref,
            run_ref=BackendRef(RefKind.RUN, controller_run.issuer_domain, controller_run.run_id),
            holder=handle.holder,
            lease=handle,
        )
        state.action_hook = hook
        port = _ControllerModelPort(self, state)
        executor = (
            ToolCallExecutor(
                port=port,
                action_hook=hook,
                access_policy=_ControllerApprovalPolicy(self),
                approval_callback=_ControllerApprovalCallback(self, state),
            )
            if self._approval_tools
            else ToolCallExecutor(port=port, action_hook=hook)
        )
        # Per-turn harness pipeline: the shared context processor chain's
        # projection (platform-default policy — the Runner carries no agent
        # processor configuration) and provider shaping over the port. The
        # Runner wires no LLM guard chains: none are configured on this
        # host, so those stages are pass-throughs and tool-side governance
        # stays with the executor.
        controller_session = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"runner-controller:{controller_run.run_id}"))
        turn_pipeline = (
            HarnessTurnPipeline(
                context_chain=ContextChainFactory(),
                port=port,
                session_id=controller_session,
                model=self._profile.config.model_backend,
            )
            if run_input.get("harness") is not None
            else None
        )
        source = PortTurnResponderSource(
            port,
            config={"model": self._profile.config.model_backend},
            tool_definitions=self._controller_tool_definitions(),
            base_refs={
                "definition": self._definition_digest,
                "model": self._profile.config.model_backend,
                "output_schema": run_input.get("output_schema"),
            },
            pipeline=turn_pipeline,
        )
        prompt = str(
            (run_input.get("prompt") if isinstance(run_input.get("prompt"), str) else None)
            or ((run_input.get("input") or {}).get("prompt") if isinstance(run_input.get("input"), dict) else "")
            or ""
        )
        base_messages = [{"role": "user", "content": prompt}] if prompt else []
        persisted = self._durable.store.get_task_input(state.task_ref) or {}
        base_messages = base_messages or [
            {
                "role": "user",
                "content": json.dumps(
                    {key: value for key, value in persisted.items() if not key.startswith("_")},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        ]
        controller_options = run_input.get("controller") or {}
        # Bounded-continuation opt-in: validated fail-fast (shape, bound,
        # acceptance co-requirement) like every other controller option.
        try:
            from hecate_runtime.accepted_decision import DecisionValidationError
            from hecate_runtime.agent_controller import ControllerContinuationPolicy

            continuation_policy = ControllerContinuationPolicy.from_declaration(controller_options.get("continuation"))
            if continuation_policy is not None and run_input.get("acceptance") is None:
                raise ValueError("controller.continuation requires a declared acceptance block")
        except (DecisionValidationError, ValueError) as exc:
            state.status = "failed"
            state.error = f"invalid controller continuation config: {exc}"
            return
        limits = ControllerLimits(
            max_turns=controller_options.get("max_turns", 16), deadline=controller_options.get("deadline")
        )
        from hecate_runtime.agent_controller import NoProgressPolicy

        no_progress = NoProgressPolicy.from_declaration(controller_options.get("no_progress"))
        # Harness context composition: a declared block (persisted with the
        # task input, validated on submission) composes the frozen base
        # through the shared module — caller semantic sections, then the
        # task input as the final section — and pins the composition digest
        # on every decision. No block keeps the legacy base input and
        # replays byte-for-byte. A block that no longer parses is storage
        # corruption: the dispatch fails closed with its reason.
        harness_spec = None
        harness_declaration = declared_harness(run_input.get("harness"))
        if harness_declaration is not None:
            sections = []
            if run_input.get("output_schema") is not None:
                sections.append(HarnessSection("output_schema", run_input["output_schema"]))
            sections.append(HarnessSection("task", base_messages))
            harness_spec = harness_declaration.compose_spec(*sections)
            base_messages = harness_spec.compose_messages()
        revisions = await self._controller_input_revisions(state, run_input, durable_run)
        if revisions is None:
            return
        spec = ControllerRunSpec(
            run_ref=controller_run,
            base_messages=base_messages,
            ownership=ownership,
            limits=limits,
            input_revisions=revisions,
            # An explicitly declared acceptance standard pins its
            # version and digest on every decision; the delivery gate
            # below evaluates under the same frozen declaration.
            acceptance=declared_acceptance(run_input.get("acceptance")),
            # An explicitly declared harness composition pins its version
            # and digest on every decision; the controller additionally
            # verifies the base messages equal the frozen composition.
            harness=harness_spec,
            # A durable wait parked mid-turn (input request recorded as
            # the real outcome) stops the turn's remaining actions
            # BEFORE their ledger claim and ends the pass for parking.
            stop_check=lambda: "input_wait_requested" if state.wait_request is not None else None,
            # An explicitly declared no-progress policy pins its version
            # and digest on every decision; absent keeps prior behavior.
            no_progress=no_progress,
            continuation_policy=continuation_policy,
            readonly_observation_tools=self._controller_readonly_observations() if no_progress is not None else (),
        )
        # Persisted trigger facts are the durable authority for the
        # continuation position: rebuilt before the first pass so a
        # redelivery replays committed turns, skips already-rejected
        # candidates and projects their feedback deterministically.
        continuations = self._persisted_continuation_entries(
            state, continuation_policy, spec.acceptance, controller_run
        )
        if continuations is None:
            return
        if continuations:
            spec = replace(spec, continuations=continuations)
            entry = continuations[-1]
            try:
                candidate = await SqlAcceptedDecisionStore(self._durable.store).lookup(
                    controller_run, entry.candidate_decision_step_id
                )
            except Exception as exc:  # noqa: BLE001 - unavailable history cannot resume
                state.needs_reconciliation = True
                state.status = "unknown"
                state.error = f"continuation candidate is unavailable: {exc}"
                return
            if candidate is not None:
                state.result = {
                    "content": candidate.accepted_output.get("text", ""),
                    "acceptance": entry.acceptance_record,
                }
        initial_refs = {**source.frozen_refs(), "controller": "agent-loop/1", "turn": 1}
        first_responder = source.responder_for(base_messages, initial_refs)
        controller = AgentController(
            AgentTurn(SqlAcceptedDecisionStore(self._durable.store), first_responder, executor),
            source,
        )

        def observe(event: dict) -> RuntimeEventDecision:
            # Renew the dispatch lease at every turn boundary so a long
            # multi-turn loop keeps valid fencing (same TTL budget per
            # turn; a lost lease fences the next turn's writes).
            self._durable.store.leases.renew(lease_key, holder, 120, fencing_token=handle.fencing_token)
            self._append_event(state, event)
            if state.needs_reconciliation or state.dispatch_denied:
                return RuntimeEventDecision.STOP_UNKNOWN
            if state.wait_request is not None:
                # An input-required dispatch completed its turn with the
                # request as the real recorded outcome; park now instead
                # of deciding the next turn without the human input.
                return RuntimeEventDecision.STOP_UNKNOWN
            return RuntimeEventDecision.CONTINUE

        while True:
            driver = ControllerExecutionDriver(controller, spec, should_cancel=lambda: state.cancel_requested)
            try:
                result = await runtime_execution_service.execute(
                    RuntimeExecutionRequest(
                        runtime=driver,
                        session_id=_uuid.UUID(controller_session),
                        initial_input={},
                        observer=observe,
                        should_cancel=lambda: state.cancel_requested,
                    )
                )
            except ApprovalWaitSignal as signal:
                # A park-worthy dispatch outcome: record the wait on the
                # run state; the shared park flow commits the durable wait
                # and the caller's finally keeps the task non-terminal. The
                # contract carries the accepted action's real arguments so
                # the wake's one-shot grant matches them.
                state.wait_request = {
                    "wake_kind": signal.context.get("wake_kind", "resume"),
                    "contract_ref": {
                        "tool": signal.tool_name,
                        "arguments_digest": tool_arguments_digest(signal.arguments),
                        "arguments": dict(signal.arguments or {}),
                        "invocation_id": signal.context.get("invocation_id"),
                        "kind": signal.context.get("wait_kind", "approval"),
                    },
                }
                return
            if state.wait_request is not None:
                # The observer stopped the stream at the turn boundary after
                # an input-required outcome was durably recorded; the shared
                # park flow commits the wait and releases the worker.
                return
            if result.state.value == "failed":
                state.needs_reconciliation = bool(result.detail.get("requires_reconciliation"))
                state.status = "unknown" if state.needs_reconciliation else "failed"
                state.error = result.error or "controller execution failed"
                if state.result is not None:
                    state.result = {
                        **state.result,
                        "status": state.status,
                        "error": state.error,
                        "error_category": "decision_history_unavailable"
                        if state.needs_reconciliation
                        else "controller_execution_failed",
                    }
                return
            if result.state.value in ("unknown", "cancelled"):
                state.status = "cancelled" if result.state.value == "cancelled" else "unknown"
                state.error = f"controller execution {result.state.value}"
                return
            outcome = driver.outcome
            if outcome is None:  # pragma: no cover - completed streams carry one
                state.status = "unknown"
                state.error = "controller stream ended without an outcome"
                return
            if outcome.is_final_answer:
                from hecate_runtime.delivery_acceptance import evaluate_delivery, evaluation_declaration

                text = outcome.final_text or ""
                declaration = evaluation_declaration(run_input.get("acceptance"), run_input.get("output_schema"))
                final_decision = (
                    {
                        "decision_step_id": outcome.final_decision_step_id,
                        "content_digest": outcome.final_decision_digest,
                    }
                    if outcome.final_decision_step_id is not None and outcome.final_decision_digest is not None
                    else None
                )
                record = evaluate_delivery(
                    text,
                    declaration,
                    final_decision=final_decision,
                    evaluator={"host": "runner", "principal": state.principal},
                    task_ref=(
                        {"issuer": state.task_ref.issuer_domain, "id": state.task_ref.id}
                        if state.task_ref is not None
                        else None
                    ),
                    run_ref=(
                        {"issuer": state.run_ref.issuer_domain, "id": state.run_ref.id}
                        if state.run_ref is not None
                        else None
                    ),
                )
                state.result = {"content": text, "acceptance": record}
                if record["status"] not in ("passed", "not_declared"):
                    # The committed final-answer decision retains delivery;
                    # failed or unverified checks must not grant success and
                    # the confirmed actions are never redone.
                    problem = next(check["detail"] for check in record["checks"] if check["status"] != "passed")
                    state.result["error"] = problem
                    state.result["error_category"] = record["status"]
                    # A bounded continuation may still correct this
                    # candidate: the run stays RUNNING until the bound, the
                    # verdict or a cancellation decides it. Publishing a
                    # terminal status here would let an observer finish the
                    # run while the correction is still in flight.
                    if continuation_policy is None:
                        state.status = "failed"
                        state.error = problem
                        return
                    from datetime import UTC, datetime

                    deadline = datetime.fromisoformat(limits.deadline) if limits.deadline else None
                    if outcome.turns >= limits.max_turns or (
                        deadline is not None
                        and datetime.now(UTC) >= (deadline if deadline.tzinfo else deadline.replace(tzinfo=UTC))
                    ):
                        state.status = "failed"
                        state.error = problem
                        return
                    advanced = self._advance_runner_continuation(
                        state,
                        record,
                        policy=continuation_policy,
                        attempts=list(spec.continuations),
                        durable_run=durable_run,
                    )
                    if advanced is None:
                        state.status = "failed"
                        state.error = problem
                        return  # bound reached or ineligible: real classification stands
                    if advanced == "cancelled":
                        state.status = "cancelled"
                        state.error = problem
                        return
                    spec = replace(spec, continuations=(*spec.continuations, advanced))
                    continue
                state.status = "succeeded"
                state.result["status"] = "succeeded"
                state.result_ref = f"runs/{run_id}/artifacts"
                self.record_tool_result(state, "controller", {"status": "final_answer", "turns": outcome.turns})
                return
            if outcome.reason == "cancel_requested":
                state.status = "cancelled"
                return
            if outcome.reason in {"needs_review", "reconciliation_required", "store_unavailable"}:
                state.needs_reconciliation = True
                state.status = "unknown"
                state.error = f"controller loop stopped: {outcome.reason}"
                return
            state.status = "failed"
            state.error = f"controller loop stopped: {outcome.reason}"
            state.result = {
                **(state.result or {}),
                "status": "failed",
                "error_category": f"controller_{outcome.reason}",
                "error": state.error,
                **({"no_progress": outcome.no_progress} if outcome.no_progress is not None else {}),
            }
            return

    def _persisted_continuation_entries(
        self,
        state: RunState,
        policy: Any,
        acceptance: Any,
        controller_run: Any,
    ) -> tuple[Any, ...] | None:
        """Restore the validated continuation trail, or stop for reconciliation."""
        from hecate_runtime.agent_controller import ControllerContinuation

        try:
            record = self._durable.store.get_task_state(state.task_ref)
            block = (record.extra or {}).get("continuation") if record else None
            return ControllerContinuation.from_block(
                block,
                policy,
                acceptance,
                task_ref={"issuer": state.task_ref.issuer_domain, "id": state.task_ref.id},
                run_ref={"issuer": controller_run.issuer_domain, "id": controller_run.run_id},
            )
        except Exception as exc:  # noqa: BLE001 - unreadable history requires reconciliation
            state.needs_reconciliation = True
            state.status = "unknown"
            state.error = f"stored continuation history is unverifiable: {exc}"
            return None

    def _advance_runner_continuation(
        self,
        state: RunState,
        record: dict[str, Any],
        *,
        policy: Any,
        attempts: list[Any],
        durable_run: Any,
    ) -> Any | None:
        """Persist the next trigger fact and return its loop entry.

        Mirrors the platform's ``_advance_continuation``: only an
        acceptance record whose checks are all verifiable with at least one
        explicit failure consumes a trigger; the bound and the consume-once
        guard live in the shared store method. A task that moved off
        RUNNING yields the ``"cancelled"`` sentinel.
        """

        from hecate_durable.contracts.durable import TaskLifecycleState
        from hecate_runtime.agent_controller import ControllerContinuation
        from hecate_runtime.delivery_acceptance import continuation_feedback_payload

        if record.get("status") != "acceptance_failed" or state.task_ref is None:
            return None
        checks = record.get("checks") or []
        verifiable = all(check.get("status") in ("passed", "failed") for check in checks if isinstance(check, dict))
        explicitly_failed = any(check.get("status") == "failed" for check in checks if isinstance(check, dict))
        if not verifiable or not explicitly_failed:
            return None
        if len(attempts) >= policy.max_continuations:
            return None
        candidate = record.get("final_decision") or {}
        candidate_id = candidate.get("decision_step_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            return None
        if any(entry.candidate_decision_step_id == candidate_id for entry in attempts):
            state.needs_reconciliation = True
            state.status = "unknown"
            state.error = "continuation candidate already recorded but the loop re-proposed it"
            return None
        candidate_turn = int(candidate_id.rsplit("-", 1)[1])
        attempt = {
            "attempt": len(attempts) + 1,
            "candidate_decision_step_id": candidate_id,
            "candidate_digest": str(candidate.get("content_digest")),
            "effective_turn": candidate_turn + 1,
            "standard_version": record.get("version"),
            "standard_sha256": record.get("declaration_sha256"),
            "status": record.get("status"),
            "policy_sha256": policy.sha256,
            "acceptance_record": record,
            "feedback": continuation_feedback_payload(
                record, attempt=len(attempts) + 1, max_continuations=policy.max_continuations
            ),
        }
        current = self._durable.store.get_task_state(state.task_ref)
        if current is None or current.lifecycle_state is not TaskLifecycleState.RUNNING:
            return "cancelled"
        try:
            stored = self._durable.store.record_continuation_trigger(
                state.task_ref,
                attempt,
                policy_sha256=policy.sha256,
                max_continuations=policy.max_continuations,
                expected_revision=current.revision,
                lease=state.controller_lease,
                **({"event_run_ref": durable_run} if durable_run is not None else {}),
            )
        except ValueError:
            # A stored identical trigger (crash-replay race) is consumed
            # already; anything else refuses the extension.
            latest = self._durable.store.get_task_state(state.task_ref)
            if latest is None or latest.lifecycle_state is not TaskLifecycleState.RUNNING:
                return "cancelled"
            raise
        stored_block = (stored.extra or {}).get("continuation") or {}
        return ControllerContinuation.from_record(stored_block["attempts"][-1])

    async def _controller_input_revisions(
        self, state: RunState, run_input: dict, durable_run: Any
    ) -> tuple[Any, ...] | None:
        """The run's durable input history, or ``None`` after an explicit stop.

        Recorded revisions are the authority: replay folds each one at its
        causal turn boundary and the decisions pin what they consumed. A
        Business data in ``provided`` is pre-revision history without its
        own causal binding, even if later revision rows exist. Stop for
        reconciliation rather than inventing or dropping that input.
        """

        from hecate_runtime.agent_controller import ControllerInputRevision

        try:
            rows = await asyncio.to_thread(self._durable.store.list_input_revisions, state.task_ref, durable_run)
        except Exception:
            state.status = "unknown"
            state.needs_reconciliation = True
            state.error = "input revision history is unreadable; refusing to replay without it"
            return None
        provided = run_input.get("provided")
        content = {k: v for k, v in provided.items() if k != "approval_ref"} if isinstance(provided, dict) else {}
        if content:
            state.status = "unknown"
            state.needs_reconciliation = True
            state.error = "legacy provided input has no causal revision binding; manual reconciliation required"
            return None
        return tuple(
            ControllerInputRevision(row["revision_id"], dict(row["content"]), int(row["effective_turn"]))
            for row in rows
        )

    def _assemble_runtime(self, state: RunState) -> PregelExecutionDriver:
        from hecate_runtime.pregel import PregelRuntime

        return PregelExecutionDriver(PregelRuntime(self._graph, _FanOutWorker(self, state), self._checkpoint_store))


class _ControllerApprovalPolicy:
    """Executor-level access policy mirroring the engine's approval tools.

    The accepted-action stack requires approval BEFORE its authoritative
    ledger claim (a park after the claim would dangle as an unknown
    outcome), so the controller executor gates approval-declared tools
    here instead of inside ``_dispatch_tool``.
    """

    def __init__(self, engine: ExecutionEngine) -> None:
        self._engine = engine

    def evaluate(self, tool_meta, rules, eval_context, arguments=None):
        from hecate_runtime.tool_access import AccessDecision

        if tool_meta["name"] in self._engine._approval_tools:
            return AccessDecision.REQUIRE_APPROVAL
        return AccessDecision.EXECUTE


class _ControllerApprovalCallback:
    """Consume the one-shot wake grant, or park via the durable wait signal."""

    def __init__(self, engine: ExecutionEngine, state: RunState) -> None:
        self._engine = engine
        self._state = state

    async def request_approval(self, tool_name, arguments, risk_level, context):
        from hecate_runtime.tool_access import ApprovalDecision

        digest = tool_arguments_digest(arguments)
        grant = None
        wait: dict[str, Any] = {}
        if self._state.task_ref is not None:
            persisted = self._engine._durable.store.get_task_input(self._state.task_ref) or {}
            grant = persisted.get("_wake_grant")
            record = self._engine._durable.store.get_task_state(self._state.task_ref)
            wait = (record.extra or {}).get("wait") or {} if record else {}
        invocation_id = context.get("invocation_id")
        if (
            isinstance(grant, dict)
            and invocation_id
            and wait.get("consumed") is True
            and (wait.get("contract_ref") or {}).get("invocation_id") == invocation_id
            and await self._engine._consume_wake_grant(
                self._state, tool_name, digest, arguments, invocation_id=invocation_id
            )
        ):
            return ApprovalDecision(
                approved=True, reason="wake grant", approval_ref=str(grant.get("approval_ref") or "wake")
            )
        raise ApprovalWaitSignal(tool_name, dict(arguments or {}), context={"invocation_id": invocation_id})


class _ControllerModelPort:
    """Runtime-port-shaped adapter binding the controller loop to the engine.

    Model calls reuse the profile's declared backend: the endpoint backend
    receives the full rebuilt conversation and tool definitions and may
    answer with ``tool_calls`` (absent tool calls are a final answer); the
    stub backend answers deterministically without tools. Tool dispatches
    route through the engine's governed ``_dispatch_tool`` chain — a
    park-worthy outcome (``awaiting_approval`` / ``input_required``) raises
    the durable wait signal instead of becoming a tool result the loop
    would treat as feedback.
    """

    def __init__(self, engine: ExecutionEngine, state: RunState) -> None:
        self._engine = engine
        self._state = state

    async def context_assemble(self, messages: list[dict], tools: list[dict] | None, session_id: str, model: str = ""):
        """Apply the shared provider shaping to controller model inputs."""
        from hecate_runtime.context_shaping import shape_context

        shaped_messages, shaped_tools, strategy = shape_context(messages, tools, model)
        return {"messages": shaped_messages, "tools": shaped_tools, "metadata": {"shaping_strategy": strategy}}

    async def create_span(self, name, parent_id=None, attributes=None):
        """No-op tracing surface the executor's span protocol requires."""
        return None

    async def end_span(self, span_id, output_data=None, usage=None) -> None:
        return None

    async def llm_invoke_structured(self, messages: list[dict], config: dict):
        """Reserve and settle every fresh model attempt through the host budget."""
        refusal = await self._engine._reserve_model_budget(self._state)
        if refusal is not None:
            raise refusal.error or RuntimeError("model budget reservation refused")
        key = self._state.model_invocation_id
        gate = self._engine._budget_gate if key else None
        usage = None
        output_size = 0
        try:
            async for chunk in self._invoke(messages, config):
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                output_size += len(json.dumps(chunk, ensure_ascii=False))
                yield chunk
            if gate is not None:
                from hecate_governance.usage import SettleStatus, UsageRecord, UsageStatus

                tokens = usage.get("total_tokens") if usage is not None else None
                actual = type(tokens) is int and tokens >= 0
                units = tokens if actual else max(1, (len(json.dumps(messages)) + output_size + 3) // 4)
                settled = await gate.settle(
                    UsageRecord(key, units, UsageStatus.ACTUAL if actual else UsageStatus.ESTIMATED)
                )
                if settled.status is SettleStatus.CONFLICT:
                    raise RuntimeError("model usage settlement failed")
        except BaseException:
            if gate is not None:
                from hecate_governance.usage import UsageRecord, UsageStatus

                await gate.settle(UsageRecord(key, 0, UsageStatus.UNKNOWN, "controller model attempt interrupted"))
                await gate._ledger.hold_for_reconciliation(key)
            raise

    async def _invoke(self, messages: list[dict], config: dict):
        import json as _json

        backend = self._engine._profile.config
        if backend.model_backend == "endpoint":
            headers = {}
            if backend.model_auth_env:
                headers["Authorization"] = f"Bearer {os.environ[backend.model_auth_env]}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    backend.model_endpoint,
                    json={"messages": messages, "tools": config.get("tools") or []},
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("model endpoint must return an object")
            tool_calls = payload.get("tool_calls")
            if tool_calls is not None and not isinstance(tool_calls, list):
                raise ValueError("model endpoint tool_calls must be an array")
            content = payload.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("model endpoint content must be text or null")
            usage = payload.get("usage")
            if content:
                yield {"content": content, "tool_calls": None}
            yield {
                "content": None,
                "tool_calls": tool_calls if isinstance(tool_calls, list) else None,
                **({"usage": usage} if usage is not None else {}),
            }
            return
        joined = _json.dumps(messages[-1], ensure_ascii=False) if messages else ""
        yield {"content": f"stub controller answer for {joined[:200]}", "tool_calls": None}

    async def tool_execute(self, name, args, context=None):
        outcome = await self._engine._dispatch_tool(self._state, name, dict(args or {}), accepted_context=context)
        status = str(outcome.get("status") or "")
        if status == "awaiting_approval":
            # Approval parks happen at the executor's approval boundary —
            # BEFORE the authoritative ledger claim, so recovery never sees
            # a dangling claim for a parked action.
            invocation_id = (context or {}).get("invocation_id")
            signal = ApprovalWaitSignal(name, dict(args or {}), context={"invocation_id": invocation_id})
            signal.context["wait_kind"] = "approval"
            signal.context["wake_kind"] = "resume"
            raise signal
        if status == "input_required":
            # The business interaction completed with a KNOWN result (the
            # input request; no side effect per the contract). Returning it
            # lets the executor record that real outcome, the turn completes,
            # and the loop parks at the turn boundary (the observer watches
            # ``state.wait_request``) — the wake's provided input then rides
            # the next fresh turn as a durable input revision.
            return {"status": "input_required", "detail": outcome}
        if status != "ok":
            detail = str(outcome.get("detail") or f"business tool returned {status}")
            if status in {"unknown", "needs_review", "reconciliation_required", "store_unavailable", "output_invalid"}:
                self._state.needs_reconciliation = True
                raise TimeoutError(detail)
            raise RuntimeError(detail)
        return outcome


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
            refusal = await self._engine._reserve_model_budget(self._state)
            if refusal is not None:
                return refusal
            worker: Worker = ModelNodeWorker(self._engine._profile)
        else:
            worker = ReadToolNodeWorker(
                lambda arguments, _tool=node_config["tool_name"], _state=self._state: self._engine._dispatch_tool(
                    _state, _tool, arguments
                )
            )
        from hecate_governance.usage import SettleStatus, UsageRecord, UsageStatus

        key = self._state.model_invocation_id if node_id == "model" else None
        gate = self._engine._budget_gate if key else None
        try:
            result = await worker.execute(node_id, node_config, channel_snapshot, execution_context)
            if gate is not None:
                plan = result.channel_updates.get("plan", {})
                usage = plan.get("usage")
                units = (
                    int(usage.get("total_tokens", 0))
                    if isinstance(usage, dict)
                    else max(1, (len(str(plan.get("prompt") or "")) + len(str(plan.get("content") or "")) + 3) // 4)
                )
                outcome = await gate.settle(
                    UsageRecord(key, units, UsageStatus.ACTUAL if isinstance(usage, dict) else UsageStatus.ESTIMATED)
                )
                if outcome.status is SettleStatus.CONFLICT:
                    raise RuntimeError("model usage settlement failed")
            return result
        except BaseException:
            if gate is not None:
                await gate.settle(UsageRecord(key, 0, UsageStatus.UNKNOWN, "model call interrupted"))
                await gate._ledger.hold_for_reconciliation(key)
            raise


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()
