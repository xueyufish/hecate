"""Contracts and the delegation tool for persistent platform Agent child calls.

A controller task may explicitly declare callable sibling agents
(``controller.agent_children``): named targets bound to concrete agents in
the same workspace, each with an optional input schema and call limit.
Tool targets use the parent's and target's rule intersection; knowledge,
skills and memory remain unsupported. Declared token budgets persist
through recovery. The parent model delegates through the host-provided
``delegate_agent`` tool — it chooses ONLY a declared alias and authors the
input object; it can never pick the subject, workspace, deployment,
version or relax the declared acceptance.

Acceptance is durable and idempotent: the tool submits a REAL child task
through ``TaskControlService.submit`` under an idempotency key derived
from the parent task and the calling action's stable execution id, so a
crash between the child creation and any later step re-derives the same
child — never a duplicate. The acceptance envelope rides the action
ledger; the parent parks on a durable ``agent_child`` wait whose contract
binds Task, Run, accepted decision (via the execution id) and the child
task. The child's own terminal path issues the verified callback from the
platform's records (never from caller data), the wait is consumed under
the shared commit gate, and the child's committed result enters the
parent's controller as a durable ``agent_child_result`` input revision —
never as a human ``provide_input`` revision.

Responsibility chain: the child keeps the parent's REAL initiator (the
chain is never ``user_id=None`` system-scoped like workflow children);
its authorization is the target's own frozen policy narrowed by the
declared exact tool list — combinations that would need resource-level
intersection the platform cannot express are refused at acceptance.
The child's token allocation is metered durably, including unknown model
attempts. Workspace quota holds settle independently of parent wakes.

Supported scope: same-workspace, builtin/in_process, Agent → Agent, one
level deep (children can neither declare targets nor be workflow
parents). Runner children, Agent → Workflow, generic graphs, partial
plans, enterprise teams and cross-tenant targets are out of scope.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from typing import Any

from hecate_runtime.action_ledger import tool_arguments_digest
from hecate_runtime.tool_side_effects import _BUILTIN_CLASSIFICATIONS, SideEffectClass

from hecate.contracts.execution.durable import InvalidTaskTransitionError
from hecate.contracts.execution.durable import TaskLifecycleState as TaskLifecycleStateRef

AGENT_CHILD_ENVELOPE_STATUS = "agent_child_accepted"
AGENT_CHILD_REFUSAL_STATUS = "agent_child_refused"
AGENT_CHILD_CONTRACT_KIND = "agent_child"
AGENT_CHILD_RESULT_SOURCE = "agent_child_result"
DELEGATE_AGENT_TOOL_NAME = "delegate_agent"
AGENT_CHILD_DEFAULT_MAX_CALLS = 8
AGENT_CHILD_RESULT_SCHEMA_REF = "urn:hecate:agent-child-callback:child-outcome/0"

_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_TARGET_KEYS = frozenset(
    {"alias", "agent_id", "allowed_tools", "input_schema", "output_schema", "max_calls", "budget_units"}
)
_POLICY_KEYS = frozenset({"targets", "max_calls", "budget_units"})

# The delegation action persists durable child facts, but every submission
# is idempotent under its call identity: a retry re-derives the same child
# instead of duplicating it, so the ledger may safely re-dispatch it.
_BUILTIN_CLASSIFICATIONS[DELEGATE_AGENT_TOOL_NAME] = SideEffectClass.IDEMPOTENT_WRITE

logger = logging.getLogger(__name__)


def agent_child_policy(value: Any) -> dict[str, Any] | None:
    """Validate the task-level declaration; ``None`` keeps prior behavior.

    ``controller.agent_children`` is ``{"targets": [...], "max_calls": int?}``;
    each target binds an alias to a concrete same-workspace agent with an
    optional exact-name ``allowed_tools`` narrowing, an optional
    ``input_schema`` (JSON Schema for the model-authored input object), an
    optional per-target ``max_calls``. A task-level token budget requires
    an allocation on every target, and allocations require a task total.
    Any other shape is a submission error, never a silent disable.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("controller.agent_children must be a declaration object")
    unknown = set(value) - _POLICY_KEYS
    if unknown:
        raise ValueError(f"controller.agent_children has unsupported fields: {sorted(unknown)}")
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("controller.agent_children.targets must be a non-empty array")
    parsed: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(raw_targets):
        target = _validate_target(raw, index)
        if target["alias"] in parsed:
            raise ValueError(f"controller.agent_children.targets[{index}] duplicates alias {target['alias']!r}")
        parsed[target["alias"]] = target
    max_calls = value.get("max_calls", AGENT_CHILD_DEFAULT_MAX_CALLS)
    if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
        raise ValueError("controller.agent_children.max_calls must be a positive integer")
    total_budget = value.get("budget_units")
    per_call_declared = any(target.get("budget_units") is not None for target in parsed.values())
    if per_call_declared and total_budget is None:
        # A per-call slice without a task total cannot be checked against
        # any remaining budget — the combination stays refused.
        raise ValueError("controller.agent_children.budget_units (task total) is required with per-call budget_units")
    if total_budget is not None:
        if isinstance(total_budget, bool) or not isinstance(total_budget, int) or total_budget < 1:
            raise ValueError("controller.agent_children.budget_units must be a positive integer")
        if any(target.get("budget_units") is None for target in parsed.values()):
            raise ValueError("every target requires budget_units when a task total is declared")
        for alias, target in parsed.items():
            per_call = target.get("budget_units")
            if per_call is not None and per_call > total_budget:
                raise ValueError(
                    f"controller.agent_children.targets[{alias!r}].budget_units exceeds the task's total budget"
                )
    return {
        "targets": parsed,
        "max_calls": max_calls,
        **({"budget_units": total_budget} if total_budget is not None else {}),
    }


def _validate_target(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"controller.agent_children.targets[{index}] must be an object")
    unknown = set(raw) - _TARGET_KEYS
    if unknown:
        raise ValueError(f"controller.agent_children.targets[{index}] has unsupported fields: {sorted(unknown)}")
    alias = raw.get("alias")
    if not isinstance(alias, str) or not _ALIAS_PATTERN.match(alias):
        raise ValueError(f"controller.agent_children.targets[{index}].alias must match {_ALIAS_PATTERN.pattern!r}")
    agent_id = raw.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError(f"controller.agent_children.targets[{index}].agent_id must be a non-empty string")
    try:
        import uuid as _uuid

        _uuid.UUID(agent_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"controller.agent_children.targets[{index}].agent_id must be a UUID") from exc
    allowed_tools = raw.get("allowed_tools")
    if allowed_tools is not None and (
        not isinstance(allowed_tools, list)
        or not allowed_tools
        or any(not isinstance(name, str) or not name.strip() for name in allowed_tools)
        or len(set(allowed_tools)) != len(allowed_tools)
    ):
        raise ValueError(
            f"controller.agent_children.targets[{index}].allowed_tools must be a non-empty list of "
            "distinct non-empty tool names (no globs — unexpressable narrowing is refused)"
        )
    schema = raw.get("input_schema")
    if schema is not None:
        from hecate.core.output_schema import check_output_schema

        if not isinstance(schema, dict):
            raise ValueError(f"controller.agent_children.targets[{index}].input_schema must be an object")
        try:
            check_output_schema(schema)
        except ValueError as exc:
            raise ValueError(
                f"controller.agent_children.targets[{index}].input_schema is not a valid JSON Schema: {exc}"
            ) from exc
    output_schema = raw.get("output_schema")
    if output_schema is not None:
        from hecate.core.output_schema import check_output_schema

        if not isinstance(output_schema, dict):
            raise ValueError(f"controller.agent_children.targets[{index}].output_schema must be an object")
        try:
            check_output_schema(output_schema)
        except ValueError as exc:
            raise ValueError(
                f"controller.agent_children.targets[{index}].output_schema is not a valid JSON Schema: {exc}"
            ) from exc
    target_max_calls = raw.get("max_calls")
    if target_max_calls is not None and (
        isinstance(target_max_calls, bool) or not isinstance(target_max_calls, int) or target_max_calls < 1
    ):
        raise ValueError(f"controller.agent_children.targets[{index}].max_calls must be a positive integer")
    budget_units = raw.get("budget_units")
    if budget_units is not None and (
        isinstance(budget_units, bool) or not isinstance(budget_units, int) or budget_units < 1
    ):
        raise ValueError(f"controller.agent_children.targets[{index}].budget_units must be a positive integer")
    return {
        "alias": alias,
        "agent_id": agent_id,
        **({"allowed_tools": list(allowed_tools)} if allowed_tools is not None else {}),
        **({"input_schema": schema} if schema is not None else {}),
        **({"output_schema": output_schema} if output_schema is not None else {}),
        **({"max_calls": target_max_calls} if target_max_calls is not None else {}),
        **({"budget_units": budget_units} if budget_units is not None else {}),
    }


def child_input_ok(value: Any, schema: Any) -> str | None:
    """Validate the model-authored input object against the declared schema.

    Returns the first problem message, or ``None`` when the input conforms.
    """

    if not isinstance(value, dict):
        return "agent child input must be an object"
    if schema is None:
        return None
    import jsonschema

    try:
        jsonschema.validate(value, schema)
    except jsonschema.SchemaError as exc:
        return f"agent child input_schema is not a valid JSON Schema: {exc.message}"
    except jsonschema.ValidationError as exc:
        return f"agent child input violates the declared schema: {exc.message}"
    return None


def agent_child_acceptance(value: Any) -> dict[str, Any] | None:
    """Recognize the tool's durable acceptance envelope; anything else is data.

    Only ``{"status": "agent_child_accepted", "call": {call_id, alias,
    child_task_ref, target, input_digest}}`` qualifies, with the envelope's
    ``call_id`` matching the observing action's own execution id.
    """

    if not isinstance(value, dict) or value.get("status") != AGENT_CHILD_ENVELOPE_STATUS:
        return None
    call = value.get("call")
    if not isinstance(call, dict):
        return None
    if any(not isinstance(call.get(key), str) or not call[key] for key in ("call_id", "alias", "input_digest")):
        return None
    if not isinstance(call.get("child_task_ref"), dict):
        return None
    if not isinstance(call.get("target"), dict):
        return None
    return call


def agent_child_contract(value: Any) -> dict[str, Any] | None:
    """Recognize the persisted wait contract this package's wake path owns.

    Binds the parent Task/Run, the accepting decision (via ``call_id``, the
    action's stable execution id), the child task and the frozen target
    facts. ``None`` means the wait is not an agent child wait (or its
    contract is corrupt — callers must converge such records to
    reconciliation instead of guessing a wake).
    """

    if not isinstance(value, dict) or value.get("kind") != AGENT_CHILD_CONTRACT_KIND:
        return None
    string_keys = ("alias", "call_id", "arguments_digest", "input_digest")
    if any(not isinstance(value.get(key), str) or not value[key] for key in string_keys):
        return None
    if not isinstance(value.get("child_task_ref"), dict):
        return None
    if not isinstance(value.get("target"), dict):
        return None
    if value.get("output_schema") is not None and not isinstance(value.get("output_schema"), dict):
        return None
    return value


def agent_child_stamp(value: Any) -> dict[str, Any] | None:
    """Recognize the child's durable parent stamp; ``None`` = not an agent child."""

    if not isinstance(value, dict):
        return None
    raw_ref = value.get("task_ref")
    if not isinstance(raw_ref, dict):
        return None
    if any(not isinstance(value.get(key), str) or not value[key] for key in ("call_id", "alias", "input_digest")):
        return None
    if not isinstance(value.get("agent_id"), str) or not value["agent_id"]:
        return None
    if not isinstance(value.get("parent_agent_id"), str) or not value["parent_agent_id"]:
        return None
    return value


def delegate_agent_definition(aliases: list[str]) -> dict[str, Any]:
    """The function-calling definition for the host delegation tool.

    The alias enum is frozen from the task's declaration, so the model can
    only address declared targets — never subjects, workspaces or agents.
    """

    return {
        "name": DELEGATE_AGENT_TOOL_NAME,
        "description": (
            "Delegate one bounded task to a declared sibling agent. Provide the target alias "
            "and a JSON input object matching the target's declared schema. The call is "
            "durable: this task waits for the child's verified result before continuing."
        ),
        "risk_level": "MEDIUM",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "alias": {
                    "type": "string",
                    "description": "The declared target alias to invoke",
                    "enum": sorted(aliases),
                },
                "input": {
                    "type": "object",
                    "description": (
                        "The task input object for the child agent (must match the target's declared input schema)"
                    ),
                },
            },
            "required": ["alias", "input"],
        },
    }


def refusal(reason: str) -> dict[str, Any]:
    """A structured, model-visible refusal — never an exception."""

    return {"status": AGENT_CHILD_REFUSAL_STATUS, "reason": reason}


def input_digest_of(input_obj: Any) -> str:
    """The canonical digest binding an acceptance to its authored input."""

    return tool_arguments_digest({"input": input_obj})


async def verify_child_call(
    store: Any, parent_ref: Any, contract: dict[str, Any], *, wait: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Verify a wait against its actual acceptance receipt and submitted child facts."""
    import asyncio
    from datetime import UTC, datetime

    from hecate.contracts.execution.references import BackendRef, RefKind

    run_ref = await asyncio.to_thread(store.run_for_task, parent_ref)
    if run_ref is None or contract.get("run_id") != run_ref.id:
        raise ValueError("agent child parent Run binding is invalid")
    row = await asyncio.to_thread(store.get_run_action, run_ref, contract["call_id"])
    if not row:
        raise ValueError("agent child acceptance receipt is missing")
    intent, outcome = row.get("intent") or {}, row.get("last_outcome") or {}
    if (
        (row.get("task_issuer"), row.get("task_id")) != (parent_ref.issuer_domain, parent_ref.id)
        or intent.get("action_name") != DELEGATE_AGENT_TOOL_NAME
        or intent.get("arguments_digest") != contract.get("arguments_digest")
        or outcome.get("outcome") != "succeeded"
        or row.get("pending_reconciliation")
    ):
        raise ValueError("agent child acceptance receipt identity is invalid")
    payload = row.get("result_payload")
    if tool_arguments_digest(payload) != outcome.get("result_digest"):
        raise ValueError("agent child acceptance receipt digest mismatch")
    if isinstance(payload, str):
        payload = json.loads(payload)
    call = agent_child_acceptance(payload)
    if call is None or any(
        call.get(key) != contract.get(key)
        for key in (
            "call_id",
            "alias",
            "child_task_ref",
            "target",
            "input_digest",
            "budget_units",
        )
    ):
        raise ValueError("agent child wait does not match its acceptance receipt")
    raw = contract["child_task_ref"]
    child_ref = BackendRef.from_dict(raw)
    if child_ref.kind is not RefKind.TASK or child_ref.issuer_domain != parent_ref.issuer_domain:
        raise ValueError("agent child reference belongs to another execution domain")
    parent_input = await asyncio.to_thread(store.get_task_input, parent_ref) or {}
    controller = parent_input.get("controller")
    if not isinstance(controller, dict):
        raise ValueError("agent child parent controller binding is malformed")
    if wait is not None:
        normalized: list[datetime | None] = []
        for raw_time in (controller.get("deadline"), wait.get("wait_expires_at")):
            if raw_time is None:
                normalized.append(None)
            else:
                if not isinstance(raw_time, str):
                    raise ValueError("agent child deadline binding is malformed")
                parsed = datetime.fromisoformat(raw_time)
                normalized.append(parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC))
        if normalized[0] != normalized[1]:
            raise ValueError("agent child deadline does not match the parent task")
    policy = agent_child_policy(controller.get("agent_children"))
    target = (policy or {}).get("targets", {}).get(contract["alias"])
    if target is None or target.get("agent_id") != contract["target"].get("agent_id"):
        raise ValueError("agent child target does not match the declared alias")
    if target.get("output_schema") != contract.get("output_schema"):
        raise ValueError("agent child acceptance standard does not match its declaration")
    child_input = await asyncio.to_thread(store.get_task_input, child_ref) or {}
    stamp = agent_child_stamp(child_input.get("agent_child_parent"))
    if (
        stamp is None
        or stamp.get("task_ref") != parent_ref.to_dict()
        or any(stamp.get(key) != contract.get(key) for key in ("call_id", "alias", "input_digest"))
        or stamp.get("agent_id") != target["agent_id"]
        or child_input.get("agent_id") != target["agent_id"]
        or child_input.get("deployment_id") != contract["target"].get("deployment_id")
        or child_input.get("workspace_id") != parent_input.get("workspace_id")
        or child_input.get("user_id") != parent_input.get("user_id")
        or child_input.get("output_schema") != target.get("output_schema")
        or stamp.get("allowed_tools") != target.get("allowed_tools")
        or stamp.get("budget_units") != target.get("budget_units")
    ):
        raise ValueError("agent child submitted facts do not match the accepted call")
    messages = child_input.get("messages") or []
    if len(messages) != 1 or not isinstance(messages[0], dict):
        raise ValueError("agent child input is malformed")
    authored = json.loads(messages[0].get("content", ""))
    if (
        not isinstance(authored, dict)
        or "input" not in authored
        or input_digest_of(authored["input"]) != contract["input_digest"]
        or tool_arguments_digest({"alias": contract["alias"], "input": authored["input"]})
        != contract["arguments_digest"]
    ):
        raise ValueError("agent child input digest mismatch")
    return child_input


async def verify_delegable_tools(
    db: Any,
    *,
    workspace_id: Any,
    parent_agent_id: Any,
    target_agent_id: Any,
    tools: list[str],
) -> str | None:
    """Verify the declared tools survive the parent ∧ target intersection.

    The rule engine is deny-wins (DENY → ASK → ALLOW tiers), so the merged
    parent+target rule set the child will run under IS the intersection;
    this check refuses up front what that intersection would deny or gate:
    a declared tool either side denies, or gates behind an approval rule,
    is not a delegable grant. Name-only rules are statically verifiable;
    argument-conditioned rules ride the runtime intersection unchanged.
    """

    from hecate_runtime.tool_access import AccessDecision, ToolAccessPolicy

    from hecate.core.composition.guardrail_platform import assemble_guardrails

    parent_bundle = await assemble_guardrails(
        db, workspace_id=workspace_id, agent_id=parent_agent_id, guardrail_config=None
    )
    target_bundle = await assemble_guardrails(
        db, workspace_id=workspace_id, agent_id=target_agent_id, guardrail_config=None
    )
    engine = ToolAccessPolicy()
    for name in tools:
        context = {"tool_name": name}
        for side, rules in (
            ("parent", parent_bundle.rules if parent_bundle else []),
            ("target", target_bundle.rules if target_bundle else []),
        ):
            meta = {"name": name, "approval_required": False, "sandbox_enabled": False}
            verdict = engine._evaluate_impl(meta, list(rules or []), context, None)
            if verdict is AccessDecision.DENY:
                return f"tool {name!r} is denied by the {side} policy; the intersection cannot grant it"
            if verdict is AccessDecision.REQUIRE_APPROVAL:
                return f"tool {name!r} requires approval on the {side} policy; approval is not delegable"
    return None


def validate_child_delivery(
    facts: Any, *, child_state: str, task_ref: Any, run_ref: Any, schema: Any, workspace_id: str
) -> None:
    """Refuse corrupted, unbound or unverified child delivery records."""
    import hashlib

    from hecate_runtime.delivery_acceptance import evaluation_declaration, validate_acceptance_record

    if not isinstance(facts, dict):
        raise ValueError("agent child result facts are malformed")
    if child_state == "cancelled":
        return
    content, record = facts.get("content"), facts.get("acceptance")
    if child_state == "failed" and facts.get("status") == "failed" and record is None:
        # A dispatch can fail before producing any delivery. Its committed
        # lifecycle is still a failure fact; it confers no successful output.
        return
    if not isinstance(content, str) or not isinstance(record, dict):
        raise ValueError("agent child delivery has no verifiable acceptance record")
    validate_acceptance_record(record)
    if (
        record.get("content_sha256") != hashlib.sha256(content.encode("utf-8")).hexdigest()
        or record.get("task_ref") != {"issuer": task_ref.issuer_domain, "id": task_ref.id}
        or run_ref is None
        or record.get("run_ref") != {"issuer": run_ref.issuer_domain, "id": run_ref.id}
        or (record.get("evaluated_by") or {}).get("workspace_id") != workspace_id
    ):
        raise ValueError("agent child delivery content or ownership mismatch")
    declaration = evaluation_declaration(None, schema)
    if record.get("declaration_sha256") != (declaration.sha256 if declaration is not None else None):
        raise ValueError("agent child delivery standard mismatch")
    if child_state == "succeeded" and (
        facts.get("status") != "succeeded" or record.get("status") not in ("passed", "not_declared")
    ):
        raise ValueError("agent child delivery did not pass its required checks")


def stamp_budget_units(contract: Any) -> int | None:
    """The slice allocation the acceptance froze into the wait contract."""

    if not isinstance(contract, dict):
        return None
    units = contract.get("budget_units")
    if isinstance(units, bool) or not isinstance(units, int) or units < 1:
        return None
    return units


def derive_slice_usage(slice_block: Any, *, allocated: int | None) -> dict[str, Any] | None:
    """Export the trusted budget summary from the child's durable slice block.

    ``used`` counts settled units plus every entry still holding a
    reservation (in flight or unknown) — an unknown attempt is never
    charged as zero. ``status`` is unknown when any hold remains, else the
    weakest settled quality, else "none" for a proven-unexecuted call.
    ``None`` (no contract allocation) means there is nothing to settle.
    """

    if allocated is None:
        return None
    if slice_block is None:
        # Legacy or missing accounting cannot establish that no model ran.
        return {"allocated": allocated, "used": allocated, "status": "unknown", "unknown_count": 1}
    if not isinstance(slice_block, dict) or slice_block.get("allocated") != allocated:
        raise ValueError("agent child slice allocation is inconsistent")
    entries = slice_block.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("agent child slice entries are malformed")
    for key, entry in entries.items():
        if not isinstance(key, str) or not key or not isinstance(entry, dict):
            raise ValueError("agent child slice entry is malformed")
        for field in ("reserved", "settled"):
            units = entry.get(field)
            if field == "settled" and units is None:
                continue
            if isinstance(units, bool) or not isinstance(units, int) or units < 0:
                raise ValueError("agent child slice units are malformed")
        if entry.get("status") not in (None, "actual", "estimated", "unverified", "unknown"):
            raise ValueError("agent child slice usage status is malformed")
    used = 0
    statuses: set[str] = set()
    unknown_count = 0
    for entry in entries.values():
        if entry.get("settled") is not None:
            used += int(entry["settled"])
            statuses.add(entry.get("status") or "actual")
        else:
            # In flight or explicitly unknown: the reservation stays held.
            used += int(entry["reserved"])
            statuses.add("unknown")
            unknown_count += 1
    if unknown_count:
        status = "unknown"
    elif "unverified" in statuses:
        status = "unverified"
    elif "estimated" in statuses:
        status = "estimated"
    elif "actual" in statuses:
        status = "actual"
    else:
        status = "none"
    return {"allocated": allocated, "used": used, "status": status, "unknown_count": unknown_count}


def child_messages(input_obj: Any) -> list[dict[str, str]]:
    """The child's base messages: the authored input, verbatim JSON."""

    return [{"role": "user", "content": json.dumps({"input": input_obj}, ensure_ascii=False)}]


class _QueuedChildWorker:
    """Leave accepted children queued for the durable worker's leased cycle.

    The tool's own submission service must never spawn a competing inline
    dispatch; the platform's durable worker (in-process or dedicated)
    claims the child under the normal dispatch lease.
    """

    async def dispatch_once(self, task_ref: Any) -> bool:
        return False


class DurableSliceLedger:
    """The call slice's durable ledger, persisted on the child task row.

    State lives in the child's durable ``budget_slice`` block under the
    lifecycle revision CAS with the dispatch lease as fence: a recovered
    dispatch re-seeds from the persisted block, so spent and unknown
    budget survive restarts, retries and process boundaries, and no fresh
    dispatch can mint a new slice. Units are model-metered tokens - the
    same contract the port adapter's estimate/settle applies. Charges: a
    settled entry charges its settled units; an entry with no settled
    outcome (in flight or unknown) keeps holding its full reservation.
    """

    _RETRIES = 4

    def __init__(
        self, *, store: Any, task_ref: Any, allocation: int, lease: Any = None, on_revision_advance: Any = None
    ) -> None:
        self._store = store
        self._task_ref = task_ref
        self._allocation = int(allocation)
        self._lease = lease
        self._on_revision_advance = on_revision_advance

    async def _load(self) -> dict[str, Any]:
        record = await asyncio.to_thread(self._store.get_task_state, self._task_ref)
        if record is None:
            raise ValueError("the slice's task row disappeared")
        block = (record.extra or {}).get("budget_slice") if record else None
        if block is None:
            entries = (
                {}
                if record.revision <= 1
                else {"unverified-history": {"reserved": self._allocation, "settled": None, "status": "unknown"}}
            )
            block = {"allocated": self._allocation, "entries": entries}
        derive_slice_usage(block, allocated=self._allocation)
        return {**copy.deepcopy(block), "_revision": record.revision, "_lifecycle": record.lifecycle_state}

    @staticmethod
    def _charged(entries: dict[str, Any]) -> int:
        total = 0
        for entry in entries.values():
            total += entry["settled"] if entry.get("settled") is not None else entry["reserved"]
        return total

    async def _save(self, state: dict[str, Any]) -> None:
        if state["_lifecycle"] is not TaskLifecycleStateRef.RUNNING:
            raise ValueError("the slice's task is not running")
        block = {"allocated": state["allocated"], "entries": state["entries"]}
        derive_slice_usage(block, allocated=self._allocation)
        # The revision must be the one that supplied these entries. The
        # caller retries the entire read/modify/write on a lost CAS.
        updated = await asyncio.to_thread(
            self._store.apply_task_state,
            self._task_ref,
            TaskLifecycleStateRef.RUNNING,
            expected_revision=state["_revision"],
            **({"lease": self._lease} if self._lease is not None else {}),
            extra_update={"budget_slice": block},
        )
        if self._on_revision_advance is not None:
            self._on_revision_advance(updated.revision)

    async def reserve(self, invocation_id: str, units: int) -> Any:
        """Reserve one model attempt against the persisted remaining allocation."""
        from hecate_governance.usage import ReservationOutcome, ReservationStatus

        if units < 0:
            raise ValueError("reservation units must be non-negative")
        last: Exception | None = None
        for _ in range(self._RETRIES):
            state = await self._load()
            entries = state["entries"]
            entry = entries.get(invocation_id)
            if entry is not None:
                if entry.get("settled") is not None or entry["reserved"] != units:
                    return ReservationOutcome(ReservationStatus.CONFLICT, self._charged(entries))
                return ReservationOutcome(ReservationStatus.RESERVED, self._charged(entries))
            charged = self._charged(entries)
            if charged + units > state["allocated"]:
                return ReservationOutcome(ReservationStatus.INSUFFICIENT, max(0, state["allocated"] - charged))
            entries[invocation_id] = {"reserved": units, "settled": None, "status": None}
            try:
                await self._save(state)
            except (InvalidTaskTransitionError, ValueError) as exc:
                last = exc
                continue
            return ReservationOutcome(ReservationStatus.RESERVED, max(0, state["allocated"] - self._charged(entries)))
        raise last if last is not None else RuntimeError("slice reservation could not be persisted")

    async def release(self, invocation_id: str) -> Any:
        """Release a proven-unexecuted attempt without refunding unknown usage."""
        from hecate_governance.usage import ReleaseStatus

        last: Exception | None = None
        for _ in range(self._RETRIES):
            state = await self._load()
            entries = state["entries"]
            entry = entries.get(invocation_id)
            if entry is None or entry.get("settled") is not None:
                return ReleaseStatus.NO_RESERVATION
            if entry.get("status") == "unknown":
                return ReleaseStatus.HELD_FOR_RECONCILIATION
            del entries[invocation_id]
            try:
                await self._save(state)
            except (InvalidTaskTransitionError, ValueError) as exc:
                last = exc
                continue
            return ReleaseStatus.RELEASED
        raise last if last is not None else RuntimeError("slice release could not be persisted")

    async def hold_for_reconciliation(self, invocation_id: str) -> bool:
        """Keep the attempt's reservation when execution cannot be established."""
        last: Exception | None = None
        for _ in range(self._RETRIES):
            state = await self._load()
            entry = state["entries"].get(invocation_id)
            if entry is None or entry.get("settled") is not None:
                return False
            if entry.get("status") == "unknown":
                return True
            entry["status"] = "unknown"
            try:
                await self._save(state)
            except (InvalidTaskTransitionError, ValueError) as exc:
                last = exc
                continue
            return True
        raise last if last is not None else RuntimeError("slice hold could not be persisted")

    async def settle(self, record: Any) -> Any:
        """Merge a usage report into the same durable model attempt."""
        from hecate_governance.usage import SettleOutcome, SettleStatus

        last: Exception | None = None
        for _ in range(self._RETRIES):
            state = await self._load()
            entries = state["entries"]
            entry = entries.get(record.invocation_id)
            if entry is not None and entry.get("settled") is not None:
                # An established charge exists.
                if record.status.value == "unknown":
                    # A late uncertain report must not erase an established
                    # charge (the platform ledger's own semantics).
                    return SettleOutcome(SettleStatus.CONFLICT, record)
                if entry["settled"] == record.units and entry.get("status") == record.status.value:
                    return SettleOutcome(SettleStatus.IDEMPOTENT_CONFIRMED, record)
                entry["settled"] = record.units
                entry["status"] = record.status.value
                try:
                    await self._save(state)
                except (InvalidTaskTransitionError, ValueError) as exc:
                    last = exc
                    continue
                return SettleOutcome(SettleStatus.REVISED, record)
            if record.status.value == "unknown":
                # The outcome is not established: the reservation keeps
                # holding its FULL estimate — never charged as zero.
                if entry is None:
                    entries[record.invocation_id] = {"reserved": 0, "settled": None, "status": "unknown"}
                else:
                    entry["status"] = "unknown"
                try:
                    await self._save(state)
                except (InvalidTaskTransitionError, ValueError) as exc:
                    last = exc
                    continue
                return SettleOutcome(SettleStatus.SETTLED, record)
            if entry is None:
                entries[record.invocation_id] = {
                    "reserved": record.units,
                    "settled": record.units,
                    "status": record.status.value,
                }
            else:
                entry["settled"] = record.units
                entry["status"] = record.status.value
            try:
                await self._save(state)
            except (InvalidTaskTransitionError, ValueError) as exc:
                last = exc
                continue
            return SettleOutcome(SettleStatus.SETTLED, record)
        raise last if last is not None else RuntimeError("slice settlement could not be persisted")

    async def remaining(self) -> int:
        """Return allocation less settled usage and outstanding reservations."""
        state = await self._load()
        return max(0, state["allocated"] - self._charged(state["entries"]))

    async def usage_for(self, invocation_id: str) -> Any:
        """Return the established report for one model attempt, if available."""
        from hecate_governance.usage import UsageRecord, UsageStatus

        state = await self._load()
        entry = state["entries"].get(invocation_id)
        if entry is None or entry.get("settled") is None:
            return None
        return UsageRecord(
            invocation_id=invocation_id,
            units=entry["settled"],
            status=UsageStatus(entry["status"] or "actual"),
            detail="",
        )

    async def snapshot(self) -> dict[str, Any]:
        """The trusted durable summary the wake path exports to the parent."""

        record = await asyncio.to_thread(self._store.get_task_state, self._task_ref)
        block = (record.extra or {}).get("budget_slice") if record else None
        # The allocation is a concrete int here, so the derivation always
        # produces a summary; the fallback only satisfies the optional
        # contract of derive_slice_usage.
        return derive_slice_usage(block, allocated=self._allocation) or {
            "allocated": self._allocation,
            "used": 0,
            "status": "none",
            "unknown_count": 0,
        }


class AgentChildDelegateTool:
    """The host's durable delegation tool: one accepted call per dispatch.

    The model addresses ONLY a declared alias; the tool resolves the frozen
    target facts, validates the authored input against the declared schema,
    and submits a REAL child task through ``TaskControlService.submit``
    under the idempotency key ``agent-child:<parent task>:<call id>`` — the
    calling action's stable execution id. A crash anywhere around the
    acceptance re-derives the same child: a settled acceptance action
    replays its recorded envelope without re-running the tool, and an
    unresolved one re-executes into the idempotent submit, which returns
    the original child instead of minting another.

    Refusals (unknown alias, schema violation, bound exhausted, another
    call already accepted in this dispatch) are structured, model-visible
    data — never exceptions. Storage and internal failures raise so the
    action ledger keeps the call unresolved for recovery.
    """

    def __init__(
        self,
        *,
        policy: dict[str, Any],
        parent_ref: Any,
        parent_goal: str,
        initiator_user_id: str | None,
        parent_agent_id: Any,
        workspace_id: str,
        session_factory: Any,
        store: Any,
        engine_backed: bool,
    ) -> None:
        self._policy = policy
        self._parent_ref = parent_ref
        self._parent_goal = parent_goal
        self._initiator_user_id = initiator_user_id
        self._parent_agent_id = parent_agent_id
        self._workspace_id = workspace_id
        self._session_factory = session_factory
        self._store = store
        self._engine_backed = engine_backed
        self.accepted_this_dispatch = 0

    @property
    def definition(self) -> dict[str, Any]:
        return delegate_agent_definition(list(self._policy["targets"]))

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
        if name != DELEGATE_AGENT_TOOL_NAME:  # pragma: no cover - routed by name
            raise ValueError(f"unexpected tool {name!r}")
        args = args if isinstance(args, dict) else {}
        if set(args) - {"alias", "input"}:
            return refusal("agent child calls accept only alias and input")
        alias = args.get("alias")
        target = self._policy["targets"].get(alias) if isinstance(alias, str) else None
        if target is None:
            return refusal(f"unknown agent target alias {alias!r}; use a declared alias")

        if self.accepted_this_dispatch >= 1:
            # One level deep and one in-flight call per dispatch: a second
            # acceptance in the same turn could never be parked.
            return refusal("another agent call was already accepted in this turn")
        # Durable per-target and global bounds, seeded from the accepted
        # facts so a wake's fresh dispatch continues the same accounting.
        accepted_before = await self._accepted_calls()
        if accepted_before + self.accepted_this_dispatch >= self._policy["max_calls"]:
            return refusal("the declared agent call bound is exhausted")
        target_bound = target.get("max_calls", self._policy["max_calls"])
        target_accepted = await self._accepted_calls(alias)
        if target_accepted + self.accepted_this_dispatch >= target_bound:
            return refusal(f"target {alias!r} has reached its declared call bound")

        input_obj = args.get("input")
        problem = child_input_ok(input_obj, target.get("input_schema"))
        if problem is not None:
            return refusal(problem)

        call_id = str((context or {}).get("invocation_id") or "")
        if not call_id:
            return refusal("the calling action carries no stable execution identity")

        per_call_budget = target.get("budget_units")
        if per_call_budget is not None:
            # The declared per-call slice is reserved durably against the
            # workspace quota before the child exists; the parent's
            # accumulated settled spend bounds the delegation on top.
            exhausted = await self._budget_exhausted(per_call_budget)
            if exhausted:
                return refusal("the parent task's declared budget is exhausted")
            reserved = await self._reserve_call_budget(call_id=call_id, units=int(per_call_budget))
            if reserved is not None:
                return refusal(reserved)

        # The parent ∧ target rule intersection is the delegation grant:
        # refuse up front what the merged rule set would deny or gate.
        if target.get("allowed_tools"):
            async with self._session_factory() as check_db:
                reason = await verify_delegable_tools(
                    check_db,
                    workspace_id=self._policy_workspace(),
                    parent_agent_id=self._parent_agent_id,
                    target_agent_id=self._target_agent(target),
                    tools=list(target["allowed_tools"]),
                )
            if reason is not None:
                if per_call_budget is not None:
                    await self._release_call_budget(call_id=call_id)
                return refusal(reason)

        digest = input_digest_of(input_obj)
        from hecate.execution.task_control import TaskControlService

        parent_task_id = str(self._parent_ref.id)
        # The child runs on the SAME durable decision chain the parent uses:
        # committed decisions replay after a crash, settled actions never
        # re-execute, and the parent's own deadline bounds the child.
        child_controller: dict[str, Any] = {"max_turns": 16}
        parent_payload = await self._parent_payload()
        parent_deadline = (parent_payload.get("controller") or {}).get("deadline")
        if isinstance(parent_deadline, str) and parent_deadline.strip():
            child_controller["deadline"] = parent_deadline
        child_input = {
            "messages": child_messages(input_obj),
            "controller_loop": True,
            "controller": child_controller,
            **({"output_schema": target["output_schema"]} if target.get("output_schema") else {}),
            "agent_child_parent": {
                "task_ref": self._parent_ref.to_dict(),
                "call_id": call_id,
                "alias": alias,
                "agent_id": target["agent_id"],
                "parent_agent_id": str(self._parent_agent_id),
                "input_digest": digest,
                **({"allowed_tools": list(target["allowed_tools"])} if target.get("allowed_tools") else {}),
                **({"budget_units": target["budget_units"]} if target.get("budget_units") else {}),
            },
        }
        async with self._session_factory() as db:
            service = TaskControlService(
                db,
                store=self._store,
                recorder=self._store,
                backend="postgres",
                ledger_source="core",
                session_factory=self._session_factory,
                dispatcher=None,
                worker=_QueuedChildWorker(),
            )
            try:
                result = await service.submit(
                    workspace_id=self._policy_workspace(),
                    user_id=self._initiator_user(),
                    goal=f"agent child call ({alias})",
                    agent_id=self._target_agent(target),
                    input=child_input,
                    idempotency_key=f"agent-child:{parent_task_id}:{call_id}",
                    _verified_agent_child=True,
                )
            except Exception as exc:  # noqa: BLE001 — validation refusals vs internal failures
                from hecate.execution.task_control import TaskControlError, TaskControlValidationError

                if isinstance(exc, (TaskControlValidationError, TaskControlError)):
                    if per_call_budget is not None:
                        # Nothing was created: the hold is proven unexecuted
                        # and is released instead of lingering as an orphan.
                        await self._release_call_budget(call_id=call_id)
                    return refusal(str(exc))
                raise
        self.accepted_this_dispatch += 1
        child_payload = await self._child_payload(result.task_ref)
        return {
            "status": AGENT_CHILD_ENVELOPE_STATUS,
            "call": {
                "call_id": call_id,
                "alias": alias,
                "child_task_ref": result.task_ref.to_dict(),
                "target": {
                    "agent_id": target["agent_id"],
                    "deployment_id": str(child_payload.get("deployment_id") or ""),
                },
                "input_digest": digest,
                **({"budget_units": target["budget_units"]} if target.get("budget_units") else {}),
            },
        }

    def _policy_workspace(self) -> Any:
        import uuid as _uuid

        return _uuid.UUID(self._workspace_id)

    def _initiator_user(self) -> Any:
        if self._initiator_user_id is None:
            return None
        import uuid as _uuid

        return _uuid.UUID(self._initiator_user_id)

    def _target_agent(self, target: dict[str, Any]) -> Any:
        import uuid as _uuid

        return _uuid.UUID(target["agent_id"])

    async def _budget_exhausted(self, per_call: int) -> bool:
        """The parent's accumulated settled spend plus this call exceeds its declared total."""

        total = self._policy.get("budget_units")
        if total is None:
            return False
        record = await asyncio.to_thread(self._store.get_task_state, self._parent_ref)
        used = int((record.extra or {}).get("agent_child_budget_used") or 0) if record else 0
        return used + per_call > int(total)

    async def _reserve_call_budget(self, *, call_id: str, units: int) -> str | None:
        """Hold the call slice in the durable workspace quota; None = reserved.

        The reservation is the durable budget fact for this call: it survives
        crashes and is settled to the child's actual usage (or held for
        reconciliation when usage stays unknown) by the verified callback.
        Without a quota definition the ledger records the hold with
        unlimited headroom — the slice gate still enforces the allocation.
        """

        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        async with self._session_factory() as db:
            ledger = QuotaBudgetLedger(db, workspace_id=self._policy_workspace(), unit_scale=MODEL_TOKEN_UNIT_SCALE)
            outcome = await ledger.reserve(f"agent-child:{self._parent_ref.id}:{call_id}", units)
            await db.commit()
        from hecate_governance.usage import ReservationStatus

        if outcome.status is ReservationStatus.INSUFFICIENT:
            return "the workspace budget has no headroom for the declared call slice"
        if outcome.status is ReservationStatus.CONFLICT:
            return "another reservation holds this call identity"
        return None

    async def _release_call_budget(self, *, call_id: str) -> None:
        """Release a proven-unexecuted hold; failure keeps the action retryable."""

        from hecate.ops.budget_reservations import MODEL_TOKEN_UNIT_SCALE, QuotaBudgetLedger

        # A submission may fail after creating its durable association. A
        # validation exception alone cannot establish absence of execution.
        for child in await asyncio.to_thread(self._store.list_tasks):
            payload = await asyncio.to_thread(self._store.get_task_input, child.task_ref) or {}
            stamp = agent_child_stamp(payload.get("agent_child_parent"))
            if stamp and stamp.get("task_ref") == self._parent_ref.to_dict() and stamp.get("call_id") == call_id:
                raise ValueError("agent child already exists; its reservation requires reconciliation")
        async with self._session_factory() as db:
            ledger = QuotaBudgetLedger(db, workspace_id=self._policy_workspace(), unit_scale=MODEL_TOKEN_UNIT_SCALE)
            outcome = await ledger.release(f"agent-child:{self._parent_ref.id}:{call_id}")
            await db.commit()
        from hecate_governance.usage import ReleaseStatus

        if outcome is ReleaseStatus.HELD_FOR_RECONCILIATION:
            raise ValueError("agent child reservation has an unknown execution outcome")

    async def _parent_payload(self) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(self._store.get_task_input, self._parent_ref) or {}

    async def _accepted_calls(self, alias: str | None = None) -> int:
        import asyncio

        record = await asyncio.to_thread(self._store.get_task_state, self._parent_ref)
        total = int((record.extra or {}).get("agent_child_calls") or 0) if record else 0
        if alias is None or not total:
            return total
        by_alias = (record.extra or {}).get("agent_child_calls_by_alias") or {}
        if alias in by_alias:
            return int(by_alias[alias])
        # Compatibility with waits recorded before per-alias counters existed.
        count = 0
        for task in await asyncio.to_thread(self._store.list_tasks):
            payload = await asyncio.to_thread(self._store.get_task_input, task.task_ref) or {}
            stamp = agent_child_stamp(payload.get("agent_child_parent"))
            if stamp and stamp.get("task_ref") == self._parent_ref.to_dict() and stamp.get("alias") == alias:
                count += 1
        return count

    async def _child_payload(self, child_task_ref: Any) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(self._store.get_task_input, child_task_ref) or {}
