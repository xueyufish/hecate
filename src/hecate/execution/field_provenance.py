"""Trusted field provenance for platform-local Controller writes.

A host-declared, versioned profile names protected fields of write tools
(for example ``submit_inventory_update.order_ref``) and the only sources
that may supply their values. The declaration lives in the agent version's
frozen configuration (``config_snapshot.field_provenance``): every Run pins
it through its execution snapshot and every accepted decision pins its
version and digest. Model output, task input and tool arguments can never
declare, widen or satisfy it.

Legal sources are read exclusively from durable host facts:

- a succeeded receipt of a declared read tool in the SAME Task and Run
  action ledger, dispatched by an EARLIER accepted decision (so the model
  could have seen it) and bound to the SAME resource — the declared
  resource keys of the write's arguments;
- an authorized ``provide_input`` revision that answers this profile's own
  request for the same tool, field and resource, and that the CURRENT
  accepted decision pins.

Model text, provenance claims inside arguments, undeclared tools, results
of the same decision, other Tasks and Runs, live supplements and revisions
the current decision does not pin never supply a value. The guard runs at
the governed execution boundary — after authorization, approval and the
action-ledger claim, before the business call — so its verdict is the
action's recorded outcome:

- allow: exactly one sourced value, equal to the argument;
- input request (``input_required``): no sourced value — the field is
  absent, guessed, or an exact guess without a source;
- refusal (explicit stop, zero business effect): sources disagree or
  contradict the argument (``conflict``), the decision, resource or profile
  binding is not established (``unbound``), or the durable facts cannot be
  read or verified (``unavailable``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

FIELD_PROVENANCE_VERSION = 1
FIELD_PROVENANCE_REFUSED = "field_provenance_refused"
REFUSAL_REASONS = ("conflict", "unbound", "unavailable")
PROVIDE_INPUT_SOURCE = "provide_input"
# Wait-control keys are never business data a supplement could carry.
_RESERVED_FIELDS = frozenset({"approval_ref", "wait_token", "agent_child_result"})
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,63}$")
_MAX_RULES = 16


class FieldProvenanceDeclarationError(ValueError):
    """The host declaration is malformed; nothing may run under it."""


class _UnboundError(Exception):
    """The decision, resource or profile binding is not established."""


class _UnavailableError(Exception):
    """Durable source facts cannot be read or verified."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _name(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value or len(value) > 128:
        raise FieldProvenanceDeclarationError(f"{label} must be a nonblank, trimmed string of at most 128 characters")
    return value


def _object(value: Any, label: str, *, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FieldProvenanceDeclarationError(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise FieldProvenanceDeclarationError(f"{label} has unknown field(s) {sorted(unknown)}")
    missing = required - set(value)
    if missing:
        raise FieldProvenanceDeclarationError(f"{label} is missing {sorted(missing)}")
    return value


def _is_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class ReadReceiptSource:
    """A read tool whose recorded result may supply the protected value."""

    tool: str
    field: str


@dataclass(frozen=True)
class FieldProvenanceRule:
    """One protected field of one write tool and its legal sources."""

    tool: str
    field: str
    resource_keys: tuple[str, ...]
    read_receipts: tuple[ReadReceiptSource, ...] = ()
    provided_input: bool = False
    prompt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The normalized declaration of this rule."""
        return {
            "tool": self.tool,
            "field": self.field,
            "resource_keys": list(self.resource_keys),
            "sources": {
                "read_receipts": [{"tool": item.tool, "field": item.field} for item in self.read_receipts],
                "provided_input": self.provided_input,
            },
            **({"prompt": self.prompt} if self.prompt is not None else {}),
        }


@dataclass(frozen=True)
class FieldProvenanceProfile:
    """A validated, versioned host declaration of protected fields."""

    profile: str
    rules: tuple[FieldProvenanceRule, ...]
    version: int = FIELD_PROVENANCE_VERSION

    def to_dict(self) -> dict[str, Any]:
        """The normalized declaration the digest covers."""
        return {"version": self.version, "profile": self.profile, "rules": [rule.to_dict() for rule in self.rules]}

    @property
    def sha256(self) -> str:
        """Content digest: a rewrite under the same profile name changes it."""
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    def pin(self) -> dict[str, Any]:
        """The identity every accepted decision of a protected task carries."""
        return {"version": self.version, "profile": self.profile, "sha256": self.sha256}

    def rules_for(self, tool: str) -> tuple[FieldProvenanceRule, ...]:
        """The rules protecting one tool's arguments (empty when unprotected)."""
        return tuple(rule for rule in self.rules if rule.tool == tool)

    @classmethod
    def from_declaration(cls, value: Any) -> FieldProvenanceProfile:
        """Validate a host declaration; unknown or ambiguous shapes fail fast."""
        body = _object(
            value, "field_provenance", allowed={"version", "profile", "rules"}, required={"version", "profile", "rules"}
        )
        if type(body["version"]) is not int or body["version"] != FIELD_PROVENANCE_VERSION:
            raise FieldProvenanceDeclarationError(f"field_provenance.version must be {FIELD_PROVENANCE_VERSION}")
        profile = body["profile"]
        if not isinstance(profile, str) or not _PROFILE_ID.match(profile):
            raise FieldProvenanceDeclarationError(
                "field_provenance.profile must be a lowercase identifier of at most 64 characters"
            )
        raw_rules = body["rules"]
        if not isinstance(raw_rules, list) or not raw_rules or len(raw_rules) > _MAX_RULES:
            raise FieldProvenanceDeclarationError(
                f"field_provenance.rules must be a nonempty list of at most {_MAX_RULES} rules"
            )
        rules: list[FieldProvenanceRule] = []
        seen: set[tuple[str, str]] = set()
        resource_by_tool: dict[str, tuple[str, ...]] = {}
        for index, raw in enumerate(raw_rules):
            label = f"field_provenance.rules[{index}]"
            entry = _object(
                raw,
                label,
                allowed={"tool", "field", "resource_keys", "sources", "prompt"},
                required={"tool", "field", "resource_keys", "sources"},
            )
            tool = _name(entry["tool"], f"{label}.tool")
            field_name = _name(entry["field"], f"{label}.field")
            if field_name in _RESERVED_FIELDS:
                raise FieldProvenanceDeclarationError(f"{label}.field cannot protect the control key {field_name!r}")
            if (tool, field_name) in seen:
                raise FieldProvenanceDeclarationError(f"{label} duplicates the rule for {tool}.{field_name}")
            seen.add((tool, field_name))
            raw_keys = entry["resource_keys"]
            if not isinstance(raw_keys, list) or not raw_keys:
                raise FieldProvenanceDeclarationError(f"{label}.resource_keys must be a nonempty list")
            resource_keys = tuple(_name(key, f"{label}.resource_keys[]") for key in raw_keys)
            if len(set(resource_keys)) != len(resource_keys) or field_name in resource_keys:
                raise FieldProvenanceDeclarationError(
                    f"{label}.resource_keys must be unique and must not include the protected field"
                )
            # One resource identity per tool: an input request binds exactly one resource.
            if resource_by_tool.setdefault(tool, resource_keys) != resource_keys:
                raise FieldProvenanceDeclarationError(f"{label}: rules of one tool must declare the same resource_keys")
            sources = _object(
                entry["sources"], f"{label}.sources", allowed={"read_receipts", "provided_input"}, required=set()
            )
            raw_reads = sources.get("read_receipts", [])
            if not isinstance(raw_reads, list):
                raise FieldProvenanceDeclarationError(f"{label}.sources.read_receipts must be a list")
            reads: list[ReadReceiptSource] = []
            for read_index, raw_read in enumerate(raw_reads):
                read_label = f"{label}.sources.read_receipts[{read_index}]"
                read = _object(raw_read, read_label, allowed={"tool", "field"}, required={"tool", "field"})
                source = ReadReceiptSource(
                    _name(read["tool"], f"{read_label}.tool"), _name(read["field"], f"{read_label}.field")
                )
                if source.tool == tool:
                    raise FieldProvenanceDeclarationError(f"{read_label}: a write tool cannot source its own field")
                if any(prior.tool == source.tool for prior in reads):
                    raise FieldProvenanceDeclarationError(f"{read_label} duplicates a declared read tool")
                reads.append(source)
            provided = sources.get("provided_input", False)
            if not isinstance(provided, bool):
                raise FieldProvenanceDeclarationError(f"{label}.sources.provided_input must be a boolean")
            if not reads and not provided:
                raise FieldProvenanceDeclarationError(f"{label} declares no legal source")
            prompt = entry.get("prompt")
            if prompt is not None and (not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 500):
                raise FieldProvenanceDeclarationError(
                    f"{label}.prompt must be a nonblank string of at most 500 characters"
                )
            rules.append(FieldProvenanceRule(tool, field_name, resource_keys, tuple(reads), provided, prompt))
        return cls(profile=profile, rules=tuple(rules))


@dataclass(frozen=True)
class ReadFact:
    """A succeeded, ledger-verified read receipt and its deciding turn."""

    tool: str
    arguments: dict[str, Any]
    result: Any
    turn: int
    execution_id: str = ""


@dataclass(frozen=True)
class SupplementFact:
    """A ``provide_input`` revision pinned by the current decision.

    ``request`` is the profile input request the revision answered — the
    binding (tool, fields, resource) recorded as the outcome of the asking
    action — or ``None`` when it answered something else.
    """

    revision_id: str
    content: dict[str, Any]
    effective_turn: int
    request: dict[str, Any] | None


@dataclass(frozen=True)
class ProvenanceFacts:
    """Durable facts in scope for one protected action of decision ``turn``."""

    turn: int
    reads: tuple[ReadFact, ...] = ()
    supplements: tuple[SupplementFact, ...] = ()
    execution_id: str = ""


@dataclass(frozen=True)
class FieldVerdict:
    """The verdict on one protected action across its rules."""

    kind: Literal["allow", "input_required", "refuse"]
    fields: tuple[str, ...] = ()
    reason: str | None = None
    resource: dict[str, str] = field(default_factory=dict)


def evaluate_field_provenance(
    rules: tuple[FieldProvenanceRule, ...], arguments: dict[str, Any], facts: ProvenanceFacts
) -> FieldVerdict:
    """Judge one protected action from durable facts alone.

    Pure: the same rules, arguments and facts always produce the same
    verdict, so a rebuilt host reaches the decision a crashed one would
    have. A refusal on any rule wins; otherwise every field without a
    sourced value joins one input request.
    """

    fields = tuple(rule.field for rule in rules)
    resource: dict[str, str] = {}
    for key in rules[0].resource_keys:
        value = arguments.get(key)
        if not _is_value(value):
            return FieldVerdict("refuse", fields, "unbound")
        resource[key] = value
    missing: list[str] = []
    for rule in rules:
        outcome = _evaluate_rule(rule, arguments, resource, facts)
        if outcome in REFUSAL_REASONS:
            return FieldVerdict("refuse", (rule.field,), outcome, resource)
        if outcome == "input_required":
            missing.append(rule.field)
    if missing:
        return FieldVerdict("input_required", tuple(missing), None, resource)
    return FieldVerdict("allow", fields, None, resource)


def _evaluate_rule(
    rule: FieldProvenanceRule, arguments: dict[str, Any], resource: dict[str, str], facts: ProvenanceFacts
) -> str:
    values: set[str] = set()
    for read in facts.reads:
        if read.turn >= facts.turn:
            continue  # the decision was taken before this result existed
        source = next((item for item in rule.read_receipts if item.tool == read.tool), None)
        if source is None or any(read.arguments.get(key) != resource[key] for key in rule.resource_keys):
            continue
        payload = read.result
        if not isinstance(payload, dict):
            continue
        if any(key in payload and payload[key] != resource[key] for key in rule.resource_keys):
            return "unbound"  # the receipt names another resource than its own call
        if source.field not in payload:
            continue
        value = payload[source.field]
        if not _is_value(value):
            return "unbound"
        values.add(value)
    if rule.provided_input:
        for supplement in facts.supplements:
            request = supplement.request
            if (
                supplement.effective_turn > facts.turn
                or request is None
                or request.get("tool") != rule.tool
                or rule.field not in request.get("fields", ())
                or request.get("resource") != resource
            ):
                continue
            value = supplement.content.get(rule.field)
            if not _is_value(value):
                return "unbound"
            values.add(value)
    if len(values) > 1:
        return "conflict"
    if not values or rule.field not in arguments:
        return "input_required" if rule.provided_input else "unbound"
    value = arguments[rule.field]
    return "allow" if isinstance(value, str) and value in values else "conflict"


def profile_request(payload: Any, profile: FieldProvenanceProfile) -> dict[str, Any] | None:
    """Recognize this profile's own recorded input request, or ``None``."""

    if not isinstance(payload, dict) or payload.get("status") != "input_required":
        return None
    contract = payload.get("contract")
    binding = contract.get("field_provenance") if isinstance(contract, dict) else None
    if not isinstance(binding, dict) or binding.get("profile_sha256") != profile.sha256:
        return None
    tool, fields, resource = binding.get("tool"), binding.get("fields"), binding.get("resource")
    if (
        not _is_value(tool)
        or not isinstance(fields, list)
        or not fields
        or not all(_is_value(item) for item in fields)
        or not isinstance(resource, dict)
        or not all(_is_value(item) for item in resource.values())
    ):
        return None
    return {"tool": tool, "fields": tuple(fields), "resource": dict(resource)}


def load_provenance_facts(
    store: Any,
    profile: FieldProvenanceProfile,
    rules: tuple[FieldProvenanceRule, ...],
    *,
    task_ref: Any,
    run_ref: Any,
    controller_run: Any,
    tool_call_id: str,
    tool: str,
    arguments: dict[str, Any],
) -> ProvenanceFacts:
    """Read the durable facts in scope for one protected action (blocking).

    Raises ``_UnboundError`` when the action is not the verified action of an
    accepted decision taken under this profile, and ``_UnavailableError``
    when the decision chain, action ledger or input history cannot be read
    or disagree with each other.
    """

    try:
        return _load(
            store,
            profile,
            rules,
            task_ref=task_ref,
            run_ref=run_ref,
            controller_run=controller_run,
            tool_call_id=tool_call_id,
            tool=tool,
            arguments=arguments,
        )
    except (_UnboundError, _UnavailableError):
        raise
    except Exception as exc:
        raise _UnavailableError(f"source facts are unreadable ({type(exc).__name__})") from exc


def _load(
    store: Any,
    profile: FieldProvenanceProfile,
    rules: tuple[FieldProvenanceRule, ...],
    *,
    task_ref: Any,
    run_ref: Any,
    controller_run: Any,
    tool_call_id: str,
    tool: str,
    arguments: dict[str, Any],
) -> ProvenanceFacts:
    from hecate_durable.runtime_hook import SqlAcceptedDecisionStore
    from hecate_runtime.accepted_action_step import execution_id_for_action, tool_call_id_for_action
    from hecate_runtime.accepted_decision import canonical_decision_digest, canonical_json
    from hecate_runtime.agent_controller import CONTROLLER_LOOP_MARKER

    decisions = SqlAcceptedDecisionStore(store)
    records = []
    for entry in store.list_accepted_actions(run_ref):
        record = decisions.lookup_sync(controller_run, entry["decision_step_id"])
        if (
            record is None
            or record.run_ref != controller_run
            or record.decision_step_id != entry["decision_step_id"]
            or record.content_digest != entry["content_digest"]
            or canonical_decision_digest(record) != record.content_digest
        ):
            raise _UnavailableError("accepted decision history is not verifiable")
        records.append(record)
    turns: dict[str, int] = {}
    current = None
    for record in records:
        turn = record.input_refs.get("turn")
        if record.input_refs.get("controller") != CONTROLLER_LOOP_MARKER or type(turn) is not int or turn < 1:
            raise _UnavailableError("an accepted decision lacks its controller turn")
        turns[record.decision_step_id] = turn
        for action in record.actions:
            if tool_call_id_for_action(controller_run, record.decision_step_id, action.action_key) == tool_call_id:
                current = (record, action)
    if len(set(turns.values())) != len(turns):
        raise _UnavailableError("two accepted decisions claim the same turn")
    if current is None:
        raise _UnboundError("the call is not an action of an accepted decision of this run")
    record, action = current
    if action.tool != tool or canonical_json(action.arguments) != canonical_json(arguments):
        raise _UnboundError("the executed call differs from its accepted action")
    if record.input_refs.get("field_provenance") != profile.pin():
        raise _UnboundError("the accepted decision was not taken under this profile")
    turn = turns[record.decision_step_id]
    by_turn = {turns[item.decision_step_id]: item for item in records}

    read_tools = {source.tool for rule in rules for source in rule.read_receipts}
    reads: list[ReadFact] = []
    for earlier in records:
        earlier_turn = turns[earlier.decision_step_id]
        if earlier_turn >= turn:
            continue
        for item in earlier.actions:
            if item.tool not in read_tools:
                continue
            row = _ledger_row(store, task_ref, run_ref, controller_run, earlier.decision_step_id, item)
            if row is not None and _succeeded(row):
                if earlier.input_refs.get("field_provenance") != profile.pin():
                    raise _UnavailableError("a read receipt belongs to a different profile")
                payload = row.get("result_payload")
                if isinstance(payload, dict) and payload.get("status") in (None, "ok", "success", "succeeded"):
                    execution_id = execution_id_for_action(controller_run, earlier.decision_step_id, item.action_key)
                    reads.append(ReadFact(item.tool, dict(item.arguments), payload, earlier_turn, execution_id))

    supplements: list[SupplementFact] = []
    pinned = record.input_refs.get("inputs") or []
    if pinned and any(rule.provided_input for rule in rules):
        digests = record.input_refs.get("input_digests") or []
        if not isinstance(pinned, list) or not isinstance(digests, list) or len(pinned) != len(digests):
            raise _UnavailableError("the decision's input pins are malformed")
        revisions = {row["revision_id"]: row for row in store.list_input_revisions(task_ref, run_ref)}
        for revision_id, digest in zip(pinned, digests, strict=True):
            row = revisions.get(revision_id)
            if row is None:
                raise _UnavailableError("a pinned input revision is missing")
            recomputed = hashlib.sha256(
                canonical_json(
                    {"id": revision_id, "content": row["content"], "effective_turn": row["effective_turn"]}
                ).encode("utf-8")
            ).hexdigest()
            if recomputed != digest or row["effective_turn"] > turn:
                raise _UnavailableError("a pinned input revision disagrees with its decision")
            if row.get("source") != PROVIDE_INPUT_SOURCE:
                continue
            asking = by_turn.get(row["effective_turn"] - 1)
            binding = _applied_input_request(store, task_ref, run_ref, row)
            request = _answered_request(store, task_ref, run_ref, controller_run, profile, asking, binding)
            supplements.append(SupplementFact(revision_id, dict(row["content"]), row["effective_turn"], request))
    return ProvenanceFacts(
        turn=turn,
        reads=tuple(reads),
        supplements=tuple(supplements),
        execution_id=execution_id_for_action(controller_run, record.decision_step_id, action.action_key),
    )


def _ledger_row(store: Any, task_ref: Any, run_ref: Any, controller_run: Any, step: str, action: Any) -> Any:
    from hecate_runtime.accepted_action_step import execution_id_for_action
    from hecate_runtime.action_ledger import tool_arguments_digest

    row = store.get_run_action(run_ref, execution_id_for_action(controller_run, step, action.action_key))
    if row is None:
        return None  # never dispatched: no receipt exists
    intent = row.get("intent") or {}
    if (
        (row.get("task_issuer"), row.get("task_id")) != (task_ref.issuer_domain, task_ref.id)
        or intent.get("action_name") != action.tool
        or intent.get("arguments_digest") != tool_arguments_digest(action.arguments)
    ):
        raise _UnavailableError("the action ledger disagrees with the accepted decision")
    return row


def _succeeded(row: dict[str, Any]) -> bool:
    from hecate_runtime.action_ledger import tool_arguments_digest

    outcome = row.get("last_outcome") or {}
    if outcome.get("outcome") != "succeeded" or row.get("pending_reconciliation"):
        return False
    payload = row.get("result_payload")
    if outcome.get("result_digest") != (tool_arguments_digest(payload) if payload is not None else None):
        raise _UnavailableError("the source receipt does not match its result digest")
    return True


def _result_object(result: Any) -> dict[str, Any] | None:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, RecursionError):
            return None
    return result if isinstance(result, dict) else None


def _applied_input_request(store: Any, task_ref: Any, run_ref: Any, revision: dict[str, Any]) -> dict[str, str] | None:
    from hecate_durable.contracts.durable import CommandState, ControlCommandKind

    command = store.get(revision["source_ref"])
    if (
        command is None
        or command.state != CommandState.APPLIED
        or command.kind != ControlCommandKind.PROVIDE_INPUT
        or command.task_ref != task_ref
        or command.extra.get("effect_run_ref") != run_ref.to_dict()
        or command.extra.get("input_revision") != revision["revision_id"]
        or not revision.get("wait_token")
        or (command.detail_ns or {}).get("wait_token") != revision["wait_token"]
    ):
        raise _UnavailableError("the input revision has no matching applied wake receipt")
    binding = command.extra.get("input_request_binding")
    if binding is not None and (
        not isinstance(binding, dict)
        or not _is_value(binding.get("execution_id"))
        or not _is_value(binding.get("contract_digest"))
    ):
        raise _UnavailableError("the applied wake has a malformed protected request binding")
    return binding


def _answered_request(
    store: Any,
    task_ref: Any,
    run_ref: Any,
    controller_run: Any,
    profile: FieldProvenanceProfile,
    decision: Any,
    binding: dict[str, str] | None,
) -> dict[str, Any] | None:
    from hecate_durable.contracts.durable import canonical_request_digest
    from hecate_runtime.accepted_action_step import execution_id_for_action

    if binding is None:
        return None
    if decision is None or decision.input_refs.get("field_provenance") != profile.pin():
        raise _UnavailableError("the input request's accepted decision is unavailable")
    for item in decision.actions:
        if (
            execution_id_for_action(controller_run, decision.decision_step_id, item.action_key)
            != binding["execution_id"]
        ):
            continue
        row = _ledger_row(store, task_ref, run_ref, controller_run, decision.decision_step_id, item)
        if row is None or not _succeeded(row):
            break
        payload = row.get("result_payload")
        request = profile_request(payload, profile)
        if request is None:
            break
        rules = profile.rules_for(item.tool)
        if (
            rules
            and request["tool"] == item.tool
            and canonical_request_digest(payload["contract"]) == binding["contract_digest"]
            and set(request["fields"]) <= {rule.field for rule in rules if rule.provided_input}
            and request["resource"] == {key: item.arguments.get(key) for key in rules[0].resource_keys}
        ):
            return request
        break
    raise _UnavailableError("the applied wake does not match a protected input request")


def refusal_envelope(
    *, execution_id: str, profile: FieldProvenanceProfile, tool: str, fields: tuple[str, ...], reason: str
) -> str:
    """The typed refusal recorded as the blocked action's outcome.

    JSON text, not a mapping: text round-trips byte-identically through the
    live tool message and the ledger backfill, so first runs and replays
    recognize the same stop. It names no protected or sourced value.
    """

    return _canonical(
        {
            "status": FIELD_PROVENANCE_REFUSED,
            "version": FIELD_PROVENANCE_VERSION,
            "execution_id": execution_id,
            "profile": profile.profile,
            "profile_sha256": profile.sha256,
            "tool": tool,
            "fields": list(fields),
            "reason": reason,
        }
    )


def field_provenance_refusal(
    result: Any, *, execution_id: str, tool: str, profile: FieldProvenanceProfile
) -> dict[str, Any] | None:
    """Recognize a refusal bound to this action, tool and profile."""

    if not isinstance(result, dict) or result.get("status") != FIELD_PROVENANCE_REFUSED:
        return None
    fields = result.get("fields")
    if (
        result.get("version") != FIELD_PROVENANCE_VERSION
        or result.get("execution_id") != execution_id
        or result.get("tool") != tool
        or result.get("profile_sha256") != profile.sha256
        or result.get("reason") not in REFUSAL_REASONS
        or not isinstance(fields, list)
        or not fields
        or not all(_is_value(item) for item in fields)
    ):
        return None
    return {"execution_id": execution_id, "tool": tool, "fields": list(fields), "reason": result["reason"]}


class ControllerFieldProvenanceGuard:
    """Judge one controller Run's protected writes at the execution boundary.

    Bound to the trusted Task, ledger Run and decision Run identities of one
    dispatch; the per-call identity is the kernel-assigned accepted action
    id in the governed tool context, never a model-supplied value.
    """

    def __init__(
        self,
        profile: FieldProvenanceProfile,
        *,
        store: Any,
        task_ref: Any,
        run_ref: Any,
        controller_run: Any,
    ) -> None:
        self._profile = profile
        self._store = store
        self._task_ref = task_ref
        self._run_ref = run_ref
        self._controller_run = controller_run
        self._read_tools = frozenset(source.tool for rule in profile.rules for source in rule.read_receipts)
        self._observed_reads: dict[str, str] = {}

    @property
    def profile(self) -> FieldProvenanceProfile:
        """The pinned profile this guard enforces."""
        return self._profile

    def prepare_business_result(self, result: Any) -> Any:
        """Reserve provenance control markers for host-generated verdicts."""

        payload = _result_object(result)
        if payload is None:
            return result
        if payload.get("status") == FIELD_PROVENANCE_REFUSED:
            return {"status": "rejected", "reason": "invalid provenance control envelope"}
        contract = payload.get("contract")
        if isinstance(contract, dict) and "field_provenance" in contract:
            payload = {**payload, "contract": {k: v for k, v in contract.items() if k != "field_provenance"}}
            return _canonical(payload) if isinstance(result, str) else payload
        return result

    async def observe_read(self, execution_id: str, tool: str, result: Any) -> None:
        """Track a source receipt only when both projections released it."""

        if tool not in self._read_tools:
            return
        self._observed_reads.pop(execution_id, None)
        if await asyncio.to_thread(self._confirm_read, execution_id, result):
            self._observed_reads[execution_id] = result

    def _confirm_read(self, execution_id: str, result: Any) -> bool:
        row = self._store.get_run_action(self._run_ref, execution_id)
        if row is None or not _succeeded(row):
            return False
        if (row.get("task_issuer"), row.get("task_id")) != (self._task_ref.issuer_domain, self._task_ref.id):
            raise _UnavailableError("the source receipt belongs to another task")
        payload = row.get("result_payload")
        if not isinstance(payload, dict):
            return False
        released = isinstance(result, str) and (result == str(payload) or _result_object(result) == payload)
        digest = row["last_outcome"]["result_digest"] if released else None
        return self._store.confirm_first_action_projection(self._task_ref, self._run_ref, execution_id, digest)

    async def restore_source_result(self, execution_id: str, tool: str, content: Any) -> Any:
        """Give result governance the structured form of a replayed source."""

        if tool in self._read_tools:
            result = _result_object(content)
            if result is not None:
                row = await asyncio.to_thread(self._store.get_run_action, self._run_ref, execution_id)
                if row is not None and row.get("result_payload") == result:
                    return result
        return content

    async def check(self, name: str, arguments: dict[str, Any], context: dict[str, Any]) -> Any:
        """``None`` lets the call proceed; otherwise its governed outcome."""

        rules = self._profile.rules_for(name)
        if not rules:
            return None
        fields = tuple(rule.field for rule in rules)
        tool_call_id = context.get("tool_call_id")
        try:
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise _UnboundError("the call carries no accepted action identity")
            facts = await asyncio.to_thread(
                load_provenance_facts,
                self._store,
                self._profile,
                rules,
                task_ref=self._task_ref,
                run_ref=self._run_ref,
                controller_run=self._controller_run,
                tool_call_id=tool_call_id,
                tool=name,
                arguments=arguments,
            )
            if context.get("invocation_id") != facts.execution_id:
                raise _UnboundError("the invocation does not match the accepted action")
            # Fresh dictionary results are rendered as text. Durable replay
            # may instead render JSON. Compare the original receipt to what
            # the result guards actually released in either representation.
            reads = tuple(
                read
                for read in facts.reads
                if (observed := self._observed_reads.get(read.execution_id)) is not None
                and (observed == str(read.result) or _result_object(observed) == read.result)
            )
            verdict = evaluate_field_provenance(rules, arguments, ProvenanceFacts(facts.turn, reads, facts.supplements))
        except _UnboundError as exc:
            logger.warning("field provenance binding not established for %s: %s", name, exc)
            verdict = FieldVerdict("refuse", fields, "unbound")
        except Exception as exc:  # noqa: BLE001 - unverifiable facts stop explicitly, never allow
            logger.warning("field provenance facts unavailable for %s (%s)", name, type(exc).__name__)
            verdict = FieldVerdict("refuse", fields, "unavailable")
        if verdict.kind == "allow":
            return None
        if verdict.kind == "input_required":
            prompt = next((rule.prompt for rule in rules if rule.field in verdict.fields and rule.prompt), None)
            return {
                "status": "input_required",
                "contract": {
                    "fields": list(verdict.fields),
                    "prompt": prompt or f"Provide {', '.join(verdict.fields)} from a trusted source.",
                    # The binding the answering revision is later verified
                    # against: this profile, tool, fields and resource.
                    "field_provenance": {
                        "profile": self._profile.profile,
                        "profile_sha256": self._profile.sha256,
                        "tool": name,
                        "fields": list(verdict.fields),
                        "resource": dict(verdict.resource),
                    },
                },
            }
        return refusal_envelope(
            execution_id=str(context.get("invocation_id") or ""),
            profile=self._profile,
            tool=name,
            fields=verdict.fields,
            reason=verdict.reason or "unavailable",
        )
