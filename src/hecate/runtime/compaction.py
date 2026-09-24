"""Durable context compaction via surface replacement (ADR-033).

The compression processor's ``surface_replacement`` backend records a
compaction as a bracket event sequence on the execution log:
``COMPACTION_STARTED → COMPACTION_SUMMARY → CONTEXT_SURFACE_REPLACED →
COMPACTION_COMPLETED``. The event log and the messages channel are never
mutated — originals stay in the log and in the channel. The working surface
is derived per invocation: the *shadowing ledger* (the recorded
``CONTEXT_SURFACE_REPLACED`` ranges) substitutes one summary node for each
shadowed message range.

Key semantics:

- **Effective surface** — the channel content with every ledger entry
  applied. It is the input to unitization and the measurement object of the
  capacity-axis trigger (``trigger_ratio × context_window``, evaluated at
  the pre-step waterline, independent of the budget ladder's satisfaction
  short-circuit).
- **Message ordinals + anchors** — ``shadowed_seqs`` are message ordinals of
  the messages channel. Exclusivity with channel eviction (enforced at
  assembly) keeps ordinals stable; each range additionally carries content
  hashes of its endpoints (anchors). An anchor mismatch fails open: the
  originals pass through unshadowed and the next trigger re-compacts on the
  current ordinals (self-healing). A summary node substituted for an earlier
  range is covered whole by a later rolling range.
- **Bracket lock** — an unmatched ``COMPACTION_STARTED`` is an in-progress
  lock; a newer ``TURN_END`` makes it stale, and a ``COMPACTION_FAILED``
  marker (bookkeeping CUSTOM event) resolves it after a summarizer failure.
  A per-session in-process lock closes the check→START race between parallel
  nodes; the log-level rules remain the durable truth.
- **Summary QA** — a summary that is structurally invalid or does not shrink
  the effective surface is rejected: no ``CONTEXT_SURFACE_REPLACED`` is
  written, the surface is unchanged, and the invocation proceeds through the
  remaining degradation levels. Failed attempts are logged for audit.

This module never imports other runtime modules at module scope except
``eventstore`` — the chain machinery it cooperates with is imported at call
time to keep the dependency direction acyclic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hecate.core.config import settings
from hecate.runtime.eventstore import EventType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from hecate.runtime.eventstore import EventStore

logger = logging.getLogger(__name__)

# Event payload markers.
COMPACTION_FAILED_EVENT = "COMPACTION_FAILED"  # CUSTOM event_name; bookkeeping only

# Structured summary node contract (validated before the bracket commits).
SUMMARY_REQUIRED_FIELDS: tuple[str, ...] = (
    "objective",
    "key_decisions",
    "current_state",
    "next_steps",
    "critical_context",
)

# Serialization discipline (ADR-033): the summary node enters the log and the
# projection — cap its fields so a rambling summarizer can neither bloat the
# event log nor smuggle oversized content into the working surface.
SUMMARY_FIELD_MAX_CHARS = 8_000
SUMMARY_LIST_ITEM_MAX_CHARS = 2_000
SUMMARY_MAX_LIST_ITEMS = 32

# ChainContext.state keys shared with the context processor chain.
STATE_RAW_MESSAGES = "compaction_raw_messages"
STATE_ORIGIN = "compaction_origin"
STATE_SHADOW_SOURCE = "compaction_shadow_source"
STATE_LEDGER = "compaction_ledger"
STATE_WINDOW = "compaction_window"
STATE_TRIGGERED = "compaction_triggered"
STATE_LISTENER = "compaction_listener"
STATE_SUMMARIZER = "compaction_summarizer"

_LEDGER_CACHE_PREFIX = "__compaction_state__"


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Character heuristic (≈4 chars/token) — the trigger/QA estimator.

    Deliberately independent of the provider-anchored estimator: the anchor
    covers the *sent projection* prefix, which systematically underestimates
    the effective surface.
    """
    if not messages:
        return 0
    total = 0
    for message in messages:
        content = message.get("content")
        chars = 0 if content is None else (len(content) if isinstance(content, str) else len(str(content)))
        total += chars
    return max(1, total // 4)


def surface_token_estimate(messages: list[dict[str, Any]]) -> int:
    """Estimate effective-surface tokens for the capacity trigger (heuristic)."""
    return _estimate_tokens(messages)


def message_anchor(message: dict[str, Any]) -> str:
    """Content hash of a channel message (ledger range endpoint anchor)."""
    canonical = json.dumps(message, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_summary(summary: Any) -> str | None:
    """Structural QA for a summarizer result; None when acceptable."""
    if not isinstance(summary, dict):
        return "summary must be a structured object"
    missing = [name for name in SUMMARY_REQUIRED_FIELDS if name not in summary]
    if missing:
        return f"missing fields: {missing}"
    for name in SUMMARY_REQUIRED_FIELDS:
        value = summary[name]
        if isinstance(value, str):
            if not value.strip():
                return f"field {name!r} must be non-empty"
        elif isinstance(value, (list, tuple)):
            if not value:
                return f"field {name!r} must be non-empty"
        else:
            return f"field {name!r} must be a string or a list"
    return None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f" …[truncated {len(text) - limit} chars]"


def bound_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Cap summary field sizes (strings and list items) before logging/rendering."""
    bounded: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, str):
            bounded[key] = _truncate(value, SUMMARY_FIELD_MAX_CHARS)
        elif isinstance(value, (list, tuple)):
            bounded[key] = [
                _truncate(str(item), SUMMARY_LIST_ITEM_MAX_CHARS) for item in list(value)[:SUMMARY_MAX_LIST_ITEMS]
            ]
        else:
            bounded[key] = value
    return bounded


def render_summary_message(summary: dict[str, Any]) -> dict[str, Any]:
    """Render the structured summary node as the substituting message."""
    lines = ["[context compaction] The earlier conversation was summarized:", ""]
    lines.append(f"Objective: {summary['objective']}")
    lines.append("")
    lines.append("Key decisions:")
    for decision in summary["key_decisions"]:
        lines.append(f"- {decision}")
    lines.append("")
    lines.append(f"Current state: {summary['current_state']}")
    lines.append("")
    lines.append("Next steps:")
    for step in summary["next_steps"]:
        lines.append(f"- {step}")
    lines.append("")
    lines.append(f"Critical context: {summary['critical_context']}")
    return {"role": "user", "content": "\n".join(lines)}


class CompactionSummarizer(ABC):
    """Runtime-internal extension point: produces the structured summary node.

    Production implementations are wired by the composition layer (routing
    pin and cache-namespace isolation are adapter concerns and never leak
    into the runtime domain). Failures surface as exceptions and are handled
    by the chain's failure policy (breaker + escalating cooldown).
    """

    @abstractmethod
    async def summarize(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Summarize the given message range into the structured summary node."""
        ...


@dataclass
class CompactionEntry:
    """One recorded surface replacement (a ledger range)."""

    compaction_id: str
    start_seq: int
    end_seq: int
    start_anchor: str
    end_anchor: str
    summary: dict[str, Any] | None
    prior_compaction_id: str | None
    version: int
    anchor_valid: bool | None = None


@dataclass
class OpenBracket:
    """An unmatched ``COMPACTION_STARTED`` — the log-level in-progress lock."""

    compaction_id: str
    version: int
    stale: bool = False
    failed: bool = False


@dataclass
class CompactionLedgerState:
    """Folded view of a session's compaction events."""

    entries: list[CompactionEntry] = field(default_factory=list)
    open_bracket: OpenBracket | None = None
    pending_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_version: int = 0


def _event_type_name(event_type: Any) -> str:
    return event_type.value if hasattr(event_type, "value") else str(event_type)


def coerce_session_id(value: Any) -> Any:
    """Best-effort UUID coercion for EventStore calls (engine passes UUID)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return value


async def load_compaction_state(
    event_store: EventStore,
    session_id: str,
    cache: dict[str, Any] | None,
) -> CompactionLedgerState:
    """Fold a session's compaction/turn events into the ledger state.

    When ``cache`` (the chain's session-scoped state dict) holds a prior
    fold, only events after its last version are fetched — the per-invocation
    cost is proportional to the new events, not the log length.
    """
    key = f"{_LEDGER_CACHE_PREFIX}{session_id}"
    cached = (cache or {}).get(key)
    state: CompactionLedgerState = cached["state"] if cached else CompactionLedgerState()
    from_version = (cached["version"] + 1) if cached else 0

    events = await event_store.get_events(coerce_session_id(session_id), from_version=from_version)
    for event in events:
        name = _event_type_name(event.event_type)
        payload = event.payload or {}
        version = event.version
        if name == EventType.COMPACTION_STARTED:
            state.open_bracket = OpenBracket(str(payload.get("compaction_id")), version)
        elif name == EventType.COMPACTION_SUMMARY:
            state.pending_summaries[str(payload.get("compaction_id"))] = payload
        elif name == EventType.CONTEXT_SURFACE_REPLACED:
            cid = str(payload.get("compaction_id"))
            state.entries.append(
                CompactionEntry(
                    compaction_id=cid,
                    start_seq=int(payload.get("start_seq", -1)),
                    end_seq=int(payload.get("end_seq", -1)),
                    start_anchor=str(payload.get("start_anchor", "")),
                    end_anchor=str(payload.get("end_anchor", "")),
                    summary=(state.pending_summaries.pop(cid, None) or {}).get("summary"),
                    prior_compaction_id=payload.get("prior_compaction_id"),
                    version=version,
                )
            )
        elif name == EventType.COMPACTION_COMPLETED and state.open_bracket is not None:
            if state.open_bracket.compaction_id == str(payload.get("compaction_id")):
                state.open_bracket = None
        elif name == EventType.TURN_END and state.open_bracket is not None:
            state.open_bracket.stale = True
        elif (
            name == EventType.CUSTOM
            and payload.get("event_name") == COMPACTION_FAILED_EVENT
            and state.open_bracket is not None
            and state.open_bracket.compaction_id == str(payload.get("compaction_id"))
        ):
            state.open_bracket.failed = True
        state.last_version = version

    if cache is not None:
        cache[key] = {"version": state.last_version, "state": state}
    return state


@dataclass
class SurfaceProjection:
    """The effective surface derived from channel content + ledger."""

    messages: list[dict[str, Any]]
    # Channel ordinal per surface position; None where a summary node stands.
    origin: list[int | None]
    # The ledger entry substituted at this position (summary nodes only).
    shadow_source: list[CompactionEntry | None]


def apply_ledger(messages: list[dict[str, Any]], entries: list[CompactionEntry]) -> SurfaceProjection:
    """Apply recorded replacements to channel content (fail-open per entry).

    Entries are applied in version order; a later range supersedes the
    overlapping portion of earlier ranges (rolling re-compaction). An entry
    whose endpoint anchors no longer match the channel content is skipped
    with a warning — originals stay visible, never wrongly hidden.
    """
    segments: list[list[Any]] = []  # disjoint [start, end, entry], inclusive
    for entry in entries:
        if entry.summary is None:
            continue
        if entry.anchor_valid is None:
            entry.anchor_valid = bool(
                0 <= entry.start_seq < len(messages)
                and 0 <= entry.end_seq < len(messages)
                and message_anchor(messages[entry.start_seq]) == entry.start_anchor
                and message_anchor(messages[entry.end_seq]) == entry.end_anchor
            )
        if not entry.anchor_valid:
            logger.warning(
                "Compaction ledger entry %s anchors no longer match the channel content; "
                "leaving seqs [%d, %d] unshadowed (fail-open)",
                entry.compaction_id,
                entry.start_seq,
                entry.end_seq,
            )
            continue
        replacement: list[list[Any]] = []
        for start, end, existing in segments:
            if end < entry.start_seq or start > entry.end_seq:
                replacement.append([start, end, existing])
                continue
            if start < entry.start_seq:
                replacement.append([start, entry.start_seq - 1, existing])
            if end > entry.end_seq:
                replacement.append([entry.end_seq + 1, end, existing])
        replacement.append([entry.start_seq, entry.end_seq, entry])
        segments = replacement

    coverage: dict[int, tuple[int, CompactionEntry]] = {}
    for start, end, entry in segments:
        for idx in range(start, end + 1):
            coverage[idx] = (start, entry)

    out_messages: list[dict[str, Any]] = []
    origin: list[int | None] = []
    shadow_source: list[CompactionEntry | None] = []
    for idx, message in enumerate(messages):
        covered = coverage.get(idx)
        if covered is None:
            out_messages.append(message)
            origin.append(idx)
            shadow_source.append(None)
            continue
        seg_start, entry = covered
        if idx == seg_start:
            out_messages.append(render_summary_message(entry.summary))
            origin.append(None)
            shadow_source.append(entry)
    return SurfaceProjection(messages=out_messages, origin=origin, shadow_source=shadow_source)


_session_locks: dict[str, asyncio.Lock] = {}


def _session_lock(session_id: str) -> asyncio.Lock:
    """Per-session compaction lock (closes the in-process check→START race).

    Cross-process exclusion remains a documented boundary (design D5): the
    log-level bracket rules keep the worst case at duplicate work, never at
    projection corruption.
    """
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


async def _register_flush_window(execution_context: dict[str, Any], session_id: Any) -> None:
    """Register one flush window for the session's consolidation unit.

    Resolves the scope's memory policy first (a policy may disable flush
    for this agent; the platform flag is the upper bound and was already
    checked by the caller). Any failure raises — the caller catches and
    logs, keeping the compaction path untouched.
    """
    raw_ws = execution_context.get("workspace_id")
    raw_agent = execution_context.get("agent_id")
    if not raw_ws or not raw_agent:
        return
    from hecate.core.composition.memory_policy import resolve_policy
    from hecate.core.database import async_session_factory

    workspace_id = uuid.UUID(str(raw_ws))
    agent_id = uuid.UUID(str(raw_agent))
    async with async_session_factory() as db:
        policy = await resolve_policy(db, workspace_id, agent_id)
        if not policy.flush_enabled:
            return

    raw_user = execution_context.get("user_id")
    user_id = uuid.UUID(str(raw_user)) if raw_user else None
    session_uuid: uuid.UUID | None = None
    try:
        session_uuid = uuid.UUID(str(session_id)) if session_id is not None else None
    except ValueError:
        session_uuid = None

    from hecate_memory.memory.consolidation import mark_unit_flush_window

    await mark_unit_flush_window(
        workspace_id,
        agent_id,
        user_id,
        session_id=session_uuid,
    )


async def resolve_context_window(
    node_config: dict[str, Any],
    execution_context: dict[str, Any] | None,
    provider: Callable[[str], Awaitable[int | None]] | None = None,
) -> int | None:
    """Resolve the model context window: injected value → provider lookup → hints.

    Raises nothing — a ``None`` result means "unresolvable"; the surface
    backend treats that as a configuration error at use time.
    """
    if execution_context:
        injected = execution_context.get("context_window")
        if isinstance(injected, int) and not isinstance(injected, bool) and injected > 0:
            return injected
    model = (node_config or {}).get("model") or (execution_context or {}).get("model")
    if provider is not None and model:
        try:
            looked_up = await provider(str(model))
        except Exception:  # noqa: BLE001 — provider lookup must never break projection
            logger.warning("Context-window provider lookup failed for model %s", model, exc_info=True)
            looked_up = None
        if isinstance(looked_up, int) and looked_up > 0:
            return looked_up
    if model:
        from hecate.runtime.context_policy import model_default_window

        hinted = model_default_window(str(model))
        if hinted is not None:
            return hinted
    return None


async def _append_compaction_event(
    event_store: EventStore,
    session_id: Any,
    event_type: EventType,
    node_id: str,
    trace_id: Any,
    superstep: int,
    payload: dict[str, Any],
) -> None:
    from hecate.runtime.eventstore import Event

    await event_store.append(
        Event(
            session_id=coerce_session_id(session_id),
            superstep=superstep,
            event_type=event_type,
            node_id=node_id,
            trace_id=trace_id,
            payload=payload,
        )
    )


async def _audit_failure(
    event_store: EventStore,
    session_id: Any,
    node_id: str,
    trace_id: Any,
    superstep: int,
    compaction_id: str,
    phase: str,
    error: str,
) -> None:
    """Record a failed attempt; the marker also resolves the open bracket."""
    try:
        await _append_compaction_event(
            event_store,
            session_id,
            EventType.CUSTOM,
            node_id,
            trace_id,
            superstep,
            {
                "event_name": COMPACTION_FAILED_EVENT,
                "compaction_id": compaction_id,
                "phase": phase,
                "error": error[:512],
            },
        )
    except Exception:  # noqa: BLE001 — audit must never mask the primary failure
        logger.warning("Failed to append COMPACTION_FAILED marker", exc_info=True)


async def run_surface_replacement(
    units: Any,
    ctx: Any,
    trigger_ratio: float,
    retain_ratio: float,
) -> tuple[list[Any], Any]:
    """Execute the durable compaction bracket for one LLM invocation.

    Called from ``CompressionProcessor`` when the surface-replacement backend
    is configured and the capacity trigger fired. Any failure leaves the
    surface unchanged (the invocation continues through the remaining
    degradation levels); only a fully recorded bracket takes effect.
    """
    from hecate.runtime.context_processors import LEVEL_COMPRESS, ContextUnit, ProcessorResult, flatten_units

    execution_context = ctx.execution_context or {}
    event_store = execution_context.get("event_store")
    summarizer = ctx.state.get(STATE_SUMMARIZER) or execution_context.get("compaction_summarizer")
    window = ctx.state.get(STATE_WINDOW)
    raw_messages: list[dict[str, Any]] = ctx.state.get(STATE_RAW_MESSAGES) or []
    origin: list[int | None] = ctx.state.get(STATE_ORIGIN) or list(range(len(raw_messages)))
    shadow_refs: list[CompactionEntry | None] = ctx.state.get(STATE_SHADOW_SOURCE) or [None] * len(raw_messages)
    ledger = ctx.state.get(STATE_LEDGER)
    session_id = ctx.session_id
    node_id = ctx.node_id
    trace_id = execution_context.get("trace_id")
    superstep = int(execution_context.get("superstep", 0) or 0)

    def _noop(reason: str) -> tuple[list[Any], Any]:
        return units, ProcessorResult(
            processor="compression",
            level=None,
            metadata={"backend": "surface_replacement", "compacted": False, "reason": reason},
        )

    if event_store is None or summarizer is None or not window or ledger is None or not raw_messages:
        return _noop("missing_components")
    open_bracket = ledger.open_bracket
    if open_bracket is not None and not open_bracket.stale and not open_bracket.failed:
        return _noop("bracket_open")

    # Shadow range in surface positions → channel ordinals.
    spans: list[tuple[int, int]] = []
    position = 0
    for unit in units:
        spans.append((position, position + len(unit.messages)))
        position += len(unit.messages)
    head_unit = next((i for i, unit in enumerate(units) if unit.kind != "system"), None)
    if head_unit is None:
        return _noop("only_system_units")
    keep_tokens = retain_ratio * window
    accumulated = 0
    tail_unit = len(units)
    for i in range(len(units) - 1, -1, -1):
        accumulated += _estimate_tokens(units[i].messages)
        if accumulated >= keep_tokens:
            tail_unit = i
            break
    if head_unit >= tail_unit:
        return _noop("nothing_to_shadow")

    head_pos = spans[head_unit][0]
    tail_pos = spans[tail_unit][0]
    start_seq = origin[head_pos]
    if start_seq is None:
        head_source = shadow_refs[head_pos]
        start_seq = head_source.start_seq if head_source is not None else None
    end_seq: int | None = None
    for pos in range(tail_pos - 1, head_pos - 1, -1):
        if origin[pos] is not None:
            end_seq = origin[pos]
            break
        covered_by = shadow_refs[pos]
        if covered_by is not None:
            end_seq = covered_by.end_seq
            break
    if start_seq is None or end_seq is None or start_seq > end_seq:
        return _noop("range_unresolvable")
    if start_seq < 0 or end_seq >= len(raw_messages):
        return _noop("range_out_of_bounds")

    lock = _session_lock(str(session_id))
    if lock.locked():
        return _noop("compaction_in_progress")

    compaction_id = uuid.uuid4().hex
    pre_tokens = surface_token_estimate(flatten_units(units))
    summary_input = flatten_units(units[head_unit:tail_unit])
    prior = shadow_refs[head_pos]

    async with lock:
        # Fresh load under the lock: a parallel node may have opened a
        # bracket between the chain-entry read and now.
        fresh = await load_compaction_state(event_store, str(session_id), cache=None)
        fresh_open = fresh.open_bracket
        if fresh_open is not None and not fresh_open.stale and not fresh_open.failed:
            return _noop("bracket_open")

        await _append_compaction_event(
            event_store,
            execution_context.get("session_id"),
            EventType.COMPACTION_STARTED,
            node_id,
            trace_id,
            superstep,
            {
                "compaction_id": compaction_id,
                "trigger": "threshold",
                "threshold_snapshot": {
                    "budget": ctx.budget,
                    "tokens": pre_tokens,
                    "window": window,
                    "trigger_ratio": trigger_ratio,
                },
            },
        )
        try:
            summary = await summarizer.summarize(summary_input)
        except Exception as exc:  # noqa: BLE001 — recorded, then re-raised for the failure policy
            await _audit_failure(
                event_store,
                execution_context.get("session_id"),
                node_id,
                trace_id,
                superstep,
                compaction_id,
                "summarize",
                str(exc),
            )
            raise
        error = validate_summary(summary)
        if error is not None:
            await _audit_failure(
                event_store,
                execution_context.get("session_id"),
                node_id,
                trace_id,
                superstep,
                compaction_id,
                "validate",
                error,
            )
            return _noop(f"summary_invalid: {error}")
        summary = bound_summary(summary)
        node_message = render_summary_message(summary)
        post_tokens = pre_tokens - _estimate_tokens(summary_input) + _estimate_tokens([node_message])
        if post_tokens >= pre_tokens:
            await _audit_failure(
                event_store,
                execution_context.get("session_id"),
                node_id,
                trace_id,
                superstep,
                compaction_id,
                "size_check",
                "summary does not shrink the effective surface",
            )
            return _noop("summary_not_shrinking")

        summary_payload: dict[str, Any] = {
            "compaction_id": compaction_id,
            "summary": summary,
            "shadowed_seqs": [start_seq, end_seq],
        }
        if prior is not None:
            summary_payload["prior_compaction_id"] = prior.compaction_id
        try:
            await _append_compaction_event(
                event_store,
                execution_context.get("session_id"),
                EventType.COMPACTION_SUMMARY,
                node_id,
                trace_id,
                superstep,
                summary_payload,
            )
            await _append_compaction_event(
                event_store,
                execution_context.get("session_id"),
                EventType.CONTEXT_SURFACE_REPLACED,
                node_id,
                trace_id,
                superstep,
                {
                    "compaction_id": compaction_id,
                    "start_seq": start_seq,
                    "end_seq": end_seq,
                    "start_anchor": message_anchor(raw_messages[start_seq]),
                    "end_anchor": message_anchor(raw_messages[end_seq]),
                },
            )
            await _append_compaction_event(
                event_store,
                execution_context.get("session_id"),
                EventType.COMPACTION_COMPLETED,
                node_id,
                trace_id,
                superstep,
                {
                    "compaction_id": compaction_id,
                    "tokens_before": pre_tokens,
                    "tokens_after": post_tokens,
                },
            )
        except Exception:
            await _audit_failure(
                event_store,
                execution_context.get("session_id"),
                node_id,
                trace_id,
                superstep,
                compaction_id,
                "commit",
                "bracket commit failed mid-sequence",
            )
            raise

    listener: Callable[[str, str], None] | None = ctx.state.get(STATE_LISTENER)
    if listener is not None:
        listener(str(session_id), compaction_id)

    # Pre-compaction flush registration (memory-lifecycle-governance): the
    # shadowed window [start_seq, end_seq] is about to leave the model
    # surface, so hand it to the consolidation trigger bus for extraction.
    # Best-effort by contract — a failure here only logs; compaction has
    # already committed and the raw event log keeps the content (the
    # watermark sweep covers the window on a later trigger).
    if settings.MEMORY_FLUSH_ENABLED:
        try:
            await _register_flush_window(execution_context, session_id)
        except Exception:  # noqa: BLE001 — never fails the compaction path
            logger.warning("Flush window registration failed (non-fatal)", exc_info=True)

    new_units = [*units[:head_unit], ContextUnit([node_message]), *units[tail_unit:]]
    saved = max(0, pre_tokens - post_tokens)
    savings_pct = round(saved / pre_tokens * 100.0, 2) if pre_tokens else 0.0
    return new_units, ProcessorResult(
        processor="compression",
        level=LEVEL_COMPRESS,
        tokens_saved=saved,
        metadata={
            "backend": "surface_replacement",
            "compacted": True,
            "compaction_id": compaction_id,
            "shadowed_seqs": [start_seq, end_seq],
            "savings_pct": savings_pct,
            "tokens_before": pre_tokens,
            "tokens_after": post_tokens,
        },
    )
