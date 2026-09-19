"""Pluggable context processor chain (4.13 Context Engine Processor Chain).

Replaces the hardcoded five-step context pipeline in ``LLMWorker`` with an
ordered, budget-satisfied projection pipeline. The chain owns:

- **Atomic unit grouping** (``unitize``): tool-call-linked messages are folded
  into indivisible ``ContextUnit``s so no processor can split an assistant
  ``tool_calls`` block from its tool results (Microsoft MessageGroup / dsh
  toolPairingBalanced semantics).
- **Token estimation** (``TokenEstimator``): provider usage anchor preferred,
  character heuristic fallback — all thresholds consume the same sensor.
- **Processors**: named, ordered, each returning a ``ProcessorResult`` with
  its degradation level and metadata; the chain aggregates these into one
  budget snapshot per invocation.
- **Executor-owned failure policy** (``FailurePolicy``): circuit breaker,
  escalating cooldown, anti-thrash for LLM-backed degradation retries.
- **Controlled termination**: when still over budget after all processors,
  the turn ends in a controlled manner (``stop_reason="token_capped"``)
  instead of silent content truncation.

The projection is non-destructive by default: the output is a temporary message list for
the current LLM invocation; channel state, checkpoints, and the event log are
never modified by the chain (offload writes are additive environment files).
The one exception is the compression processor's durable
``surface_replacement`` backend (ADR-033): it appends bracket events to the
execution log — the channel and the log's original events stay untouched,
and the working surface is a per-invocation view derived from the compaction
ledger (``runtime/compaction.py``).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from hecate.runtime.compaction import (
    STATE_LEDGER,
    STATE_LISTENER,
    STATE_ORIGIN,
    STATE_RAW_MESSAGES,
    STATE_SHADOW_SOURCE,
    STATE_SUMMARIZER,
    STATE_TRIGGERED,
    STATE_WINDOW,
    apply_ledger,
    load_compaction_state,
    resolve_context_window,
    run_surface_replacement,
    surface_token_estimate,
)

if TYPE_CHECKING:
    from hecate.runtime.context import ContextEngine

logger = logging.getLogger(__name__)

# Degradation level vocabulary (BUDGET_SNAPSHOT ``levels`` entries, in
# execution order). Additive relative to the pre-chain vocabulary
# (drop/compress/emergency): "warn" and "terminate" are new, and
# "emergency" is superseded by "terminate".
LEVEL_WARN = "warn"
LEVEL_DROP = "drop"
LEVEL_COMPRESS = "compress"
LEVEL_TERMINATE = "terminate"

STOP_REASON_BUDGET_CAPPED = "token_capped"

_DEFAULT_BUDGET = 8000
_DEFAULT_TOOL_RESULT_LIMIT = 2000
_TRUNCATION_INDICATOR = "\n[... truncated]"

_SYNTHETIC_TOOL_RESULT = "[no result recorded — synthetic placeholder]"


# ---------------------------------------------------------------------------
# Atomic unit grouping
# ---------------------------------------------------------------------------


class ContextUnit:
    """An indivisible group of conversation messages.

    An assistant message carrying ``tool_calls`` together with its tool
    results forms one unit; every other message is a single-message unit.
    Processors operate on units, never on bare messages, so a window or
    compression boundary can never orphan a tool result.
    """

    __slots__ = ("messages",)

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = messages

    @property
    def kind(self) -> str:
        """Role class of the unit: ``system`` / ``user`` / ``assistant`` / ``tool_group``."""
        role = self.messages[0].get("role")
        if role == "assistant" and self.messages[0].get("tool_calls"):
            return "tool_group"
        return str(role)

    def flatten(self) -> list[dict[str, Any]]:
        return list(self.messages)


def _synthetic_tool_result(call_id: Any) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": _SYNTHETIC_TOOL_RESULT}


def unitize(messages: list[dict[str, Any]]) -> list[ContextUnit]:
    """Fold a message list into atomic units, healing pairing violations.

    Grouping rules:
    - An assistant message with ``tool_calls`` opens a tool group; following
      ``tool`` messages whose ``tool_call_id`` matches a pending call join it.
    - A tool result with no pending call (dangling) is dropped whole.
    - A call that never received a result gets a synthetic tool result
      attached, so the unit stays provider-acceptable.

    The input list is never mutated.
    """
    units: list[ContextUnit] = []
    i = 0
    total = len(messages)
    while i < total:
        msg = messages[i]
        role = msg.get("role")
        if role == "assistant" and msg.get("tool_calls"):
            pending: set[str] = set()
            for tc in msg.get("tool_calls") or []:
                if isinstance(tc, dict) and tc.get("id"):
                    pending.add(str(tc["id"]))
            group = [msg]
            results: list[dict[str, Any]] = []
            j = i + 1
            while j < total and messages[j].get("role") == "tool":
                call_id = messages[j].get("tool_call_id")
                if call_id is not None and str(call_id) in pending:
                    results.append(messages[j])
                    pending.discard(str(call_id))
                    j += 1
                    continue
                # Tool result that answers a different/unknown call here is
                # dangling for this group — dropped by falling through.
                break
            for call_id in sorted(pending):
                results.append(_synthetic_tool_result(call_id))
            units.append(ContextUnit([*group, *results]))
            i = j
        elif role == "tool":
            # Dangling tool result: no preceding assistant call opened a
            # group for it. Dropped whole (pairing invariant).
            i += 1
        else:
            units.append(ContextUnit([msg]))
            i += 1
    return units


def flatten_units(units: list[ContextUnit]) -> list[dict[str, Any]]:
    """Flatten units back into a message list (order preserving)."""
    flat: list[dict[str, Any]] = []
    for unit in units:
        flat.extend(unit.messages)
    return flat


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


class TokenEstimator(ABC):
    """Sensor for token counts. All chain thresholds consume this."""

    @abstractmethod
    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """Estimate the total token count for a message list."""
        ...

    def estimate_single(self, message: dict[str, Any]) -> int:
        """Estimate tokens for one message (default: list of one)."""
        return self.estimate_messages([message])


def _message_chars(message: dict[str, Any]) -> int:
    content = message.get("content", "")
    if content is None:
        return 0
    return len(content) if isinstance(content, str) else len(str(content))


class HeuristicTokenEstimator(TokenEstimator):
    """Character-based estimate (approximately 4 chars per token)."""

    def __init__(self, chars_per_token: int = 4) -> None:
        self._chars_per_token = max(1, chars_per_token)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        if not messages:
            return 0
        total_chars = sum(_message_chars(m) for m in messages)
        return max(1, total_chars // self._chars_per_token)


class _EngineEstimator(TokenEstimator):
    """Adapts a legacy ``ContextEngine`` into the estimator abstraction.

    The engine-only compatibility path must budget with the engine's own
    ``estimate_tokens`` (legacy parity — custom engines may implement
    non-heuristic sensors); the production engines estimate chars/4, i.e.
    identical to the heuristic fallback.
    """

    def __init__(self, engine: ContextEngine) -> None:
        self._engine = engine

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        if not messages:
            return 0
        return self._engine.estimate_tokens(messages)


class AnchorTokenEstimator(TokenEstimator):
    """Provider-usage-anchored estimator with heuristic delta.

    The anchor is the provider-reported prompt token count for a conversation
    prefix; the caller records how many characters of conversation the anchor
    covered (``anchor_chars``). Messages within that char coverage are priced
    by the anchor (zero marginal cost); everything after is estimated with the
    character heuristic. This keeps thresholds stable where it matters (the
    bulk of the context) while remaining cheap.
    """

    def __init__(self, anchor_prompt_tokens: int, anchor_chars: int, chars_per_token: int = 4) -> None:
        self._anchor_tokens = max(0, anchor_prompt_tokens)
        self._anchor_chars = max(0, anchor_chars)
        self._chars_per_token = max(1, chars_per_token)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        if not messages:
            return 0
        covered_chars = 0
        covered = False
        remainder_chars = 0
        for m in messages:
            if not covered:
                covered_chars += _message_chars(m)
                if covered_chars >= self._anchor_chars:
                    covered = True
            else:
                remainder_chars += _message_chars(m)
        remainder_tokens = max(1, remainder_chars // self._chars_per_token) if remainder_chars else 0
        return self._anchor_tokens + remainder_tokens


# ---------------------------------------------------------------------------
# Chain context and results
# ---------------------------------------------------------------------------


@dataclass
class ChainContext:
    """Per-invocation state shared by processors during one chain run."""

    budget: int
    estimator: TokenEstimator
    node_config: dict[str, Any] = field(default_factory=dict)
    execution_context: dict[str, Any] | None = None
    node_id: str = ""
    session_id: str = ""
    engine: ContextEngine | None = None
    offloader: Any = None
    tool_result_limit: int = _DEFAULT_TOOL_RESULT_LIMIT
    warn_threshold: float = 0.8
    offload_threshold_tokens: int = 6000
    # Scratch shared across processors within this invocation (e.g. the
    # protected-prefix boundary set by KVCacheAwareProcessor).
    state: dict[str, Any] = field(default_factory=dict)

    def tokens(self, messages: list[dict[str, Any]]) -> int:
        return self.estimator.estimate_messages(messages)


@dataclass
class ProcessorResult:
    """Outcome reported by one processor."""

    processor: str
    level: str | None = None
    tokens_saved: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChainReport:
    """Aggregated chain outcome for one LLM invocation."""

    messages: list[dict[str, Any]]
    levels: list[str] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    messages_before: int = 0
    messages_after: int = 0
    stop_reason: str | None = None
    cache_hit_rate: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Failure policy (executor-owned)
# ---------------------------------------------------------------------------


class FailurePolicy:
    """Circuit breaker + escalating cooldown + anti-thrash, keyed per processor.

    - Breaker: opens after ``failure_threshold`` consecutive failures of a
      processor; half-opens after ``half_open_successes`` successful fallback
      runs.
    - Cooldown: a failing LLM-backed processor (e.g. compression summarizer)
      is deferred for an escalating delay (base → max).
    - Anti-thrash: skip a retry when the previous run saved less than
      ``min_savings_pct`` and fewer than ``anti_thrash_min_new_messages`` new
      messages arrived, unless forced.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        half_open_successes: int = 5,
        cooldown_base_seconds: float = 60.0,
        cooldown_max_seconds: float = 900.0,
        min_savings_pct: float = 10.0,
        anti_thrash_min_new_messages: int = 8,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.half_open_successes = half_open_successes
        self.cooldown_base_seconds = cooldown_base_seconds
        self.cooldown_max_seconds = cooldown_max_seconds
        self.min_savings_pct = min_savings_pct
        self.anti_thrash_min_new_messages = anti_thrash_min_new_messages
        self._consecutive_failures: dict[str, int] = {}
        self._fallback_successes: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._cooldown_delay: dict[str, float] = {}

    def allow(self, name: str) -> bool:
        """Whether the processor may run now (breaker + cooldown gate)."""
        if self._consecutive_failures.get(name, 0) >= self.failure_threshold:
            return False
        return time.monotonic() >= self._cooldown_until.get(name, 0.0)

    def record_success(self, name: str) -> None:
        self._consecutive_failures[name] = 0
        self._cooldown_delay[name] = 0.0
        self._cooldown_until.pop(name, None)
        self._fallback_successes.pop(name, None)

    def record_failure(self, name: str) -> None:
        failures = self._consecutive_failures.get(name, 0) + 1
        self._consecutive_failures[name] = failures
        delay = self._cooldown_delay.get(name) or self.cooldown_base_seconds
        if failures > 1:
            delay = min(self.cooldown_max_seconds, delay * 5)
        self._cooldown_delay[name] = delay
        self._cooldown_until[name] = time.monotonic() + delay

    def record_fallback_success(self, name: str) -> int:
        """Count a successful fallback run; returns the half-open counter."""
        count = self._fallback_successes.get(name, 0) + 1
        self._fallback_successes[name] = count
        if count >= self.half_open_successes:
            # Half-open: let the processor retry on the next invocation.
            self._consecutive_failures[name] = self.failure_threshold - 1
            self._fallback_successes[name] = 0
            self._cooldown_until.pop(name, None)
            return 0
        return count

    def should_anti_thrash(
        self,
        name: str,
        last_savings_pct: float | None,
        new_messages_since: int,
        forced: bool = False,
    ) -> bool:
        """True when a retry would be churn (low prior savings, little new input)."""
        if forced:
            return False
        if last_savings_pct is None:
            return False
        return last_savings_pct < self.min_savings_pct and new_messages_since < self.anti_thrash_min_new_messages


# ---------------------------------------------------------------------------
# Processors
# ---------------------------------------------------------------------------


class ContextProcessor(ABC):
    """One named stage of the context projection chain.

    Processors receive the current unit list and the chain context, and
    return a (possibly transformed) unit list plus a result report. They MUST
    be non-destructive: they work on the projection copy, never on channel
    state.
    """

    name: str = "processor"
    # "always" processors run on every invocation (cheap normalization and
    # hint injection); "when_over_budget" processors are skipped once the
    # projection fits the budget (satisfied predicate / early stop).
    run_mode: str = "when_over_budget"

    @abstractmethod
    async def process(
        self, units: list[ContextUnit], ctx: ChainContext
    ) -> tuple[list[ContextUnit], ProcessorResult]: ...


def _message_tokens_heuristic(message: dict[str, Any], chars_per_token: int = 4) -> int:
    chars = _message_chars(message)
    return max(1, chars // chars_per_token) if chars else 0


class ToolResultTruncationProcessor(ContextProcessor):
    """Cap oversized tool result content (legacy ``_truncate_tool_results``)."""

    name = "tool_result_truncation"
    run_mode = "always"

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        limit = ctx.tool_result_limit
        saved = 0
        out: list[ContextUnit] = []
        for unit in units:
            new_messages: list[dict[str, Any]] | None = None
            for idx, msg in enumerate(unit.messages):
                role = msg.get("role")
                if role not in ("tool", "assistant"):
                    continue
                content = msg.get("content")
                if content is None or not isinstance(content, str):
                    continue
                estimated = _message_tokens_heuristic(msg)
                if estimated <= limit:
                    continue
                if new_messages is None:
                    new_messages = list(unit.messages)
                char_limit = limit * 4
                new_messages[idx] = {
                    **msg,
                    "content": content[:char_limit] + _TRUNCATION_INDICATOR,
                }
                saved += estimated - limit
            out.append(ContextUnit(new_messages) if new_messages is not None else unit)
        # Truncation is normalization (capping outliers), not a degradation
        # level — the legacy pipeline ran it before the budget check too.
        return out, ProcessorResult(
            processor=self.name,
            level=None,
            tokens_saved=saved,
            metadata={"truncated_results": saved > 0},
        )


class BudgetWarnProcessor(ContextProcessor):
    """Inject a model-visible budget hint when usage crosses the warn threshold.

    The hint is appended to the projection (never modifies the system prompt)
    and is injected at most once per threshold crossing — the latch lives in
    the chain's session-scoped state.
    """

    name = "budget_warn"
    run_mode = "always"

    def __init__(self, threshold: float = 0.8) -> None:
        self.threshold = threshold

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        # Judge the crossing on the pre-injection baseline so the hint cannot
        # push usage across its own threshold (self-reference loop).
        tokens = ctx.tokens(flatten_units(units))
        warn_at = ctx.budget * self.threshold
        key = f"warn_latched:{ctx.session_id}:{id(self)}"
        latched = ctx.state.get(key, False)
        if tokens < warn_at:
            # Below threshold — reset the latch so a later crossing warns again.
            ctx.state[key] = False
            return units, ProcessorResult(processor=self.name, metadata={"warned": False, "tokens": tokens})
        if latched:
            return units, ProcessorResult(processor=self.name, metadata={"warned": False, "latched": True})
        ctx.state[key] = True
        hint = {
            "role": "user",
            "content": (
                f"[budget_warning] Context usage is at {tokens} of {ctx.budget} tokens "
                f"({self.threshold:.0%} threshold). Conserve context: prefer concise tool "
                "results and summarize before the budget is exhausted."
            ),
        }
        out = [*units, ContextUnit([hint])]
        return out, ProcessorResult(
            processor=self.name,
            level=LEVEL_WARN,
            tokens_saved=0,
            metadata={"warned": True, "tokens": tokens, "threshold": warn_at},
        )


class KVCacheAwareProcessor(ContextProcessor):
    """Mark a protected stable prefix to maximize provider prompt-cache reuse.

    Sets ``ctx.state["protected_upto"]``: the unit index up to which window
    selection must not drop. The protected region is a tail window inverted —
    everything except the most recent ``window_units`` units is treated as the
    stable prefix (prompt caches pay for identical prefixes; only the tail is
    cheap to drop). Protection is best-effort: if the tail cannot absorb the
    deficit, selection may extend into the protected region (recorded in
    metadata as ``prefix_protection_yielded`` by the window processor).
    """

    name = "kv_cache_aware"
    run_mode = "when_over_budget"

    def __init__(self, window_units: int = 6) -> None:
        self.window_units = max(1, window_units)

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        protected_upto = max(0, len(units) - self.window_units)
        # System units at the head are always protected regardless of the window.
        for idx, unit in enumerate(units):
            if unit.kind == "system":
                protected_upto = max(protected_upto, idx + 1)
            else:
                break
        ctx.state["protected_upto"] = protected_upto
        # Annotate the first message after the prefix as a cache breakpoint —
        # provider shaping renders this into provider syntax (4.11).
        annotated: list[ContextUnit] | None = None
        if 0 < protected_upto < len(units):
            annotated = [ContextUnit(list(u.messages)) for u in units]
            target = annotated[protected_upto].messages[0]
            annotated[protected_upto].messages[0] = {**target, "cache_hint": "breakpoint"}
        return (annotated or units), ProcessorResult(
            processor=self.name,
            metadata={"protected_upto": protected_upto, "window_units": self.window_units},
        )


class RoundWindowProcessor(ContextProcessor):
    """Recency-window selection; importance ranking inside the droppable region.

    With an engine and no protected prefix, selection delegates to
    ``engine.select_messages`` (byte-equivalent with the legacy pipeline for
    suffix engines), then heals unit atomicity. With a protected prefix (KV
    policy) or no engine, selection drops newest-first inside the droppable
    region; system units and the newest user unit are pinned.
    """

    name = "round_window"

    def __init__(self, ranking: str = "engine") -> None:
        if ranking not in ("engine", "recency"):
            raise ValueError(f"round_window ranking must be 'engine' or 'recency', got {ranking!r}")
        self.ranking = ranking

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        protected_upto = ctx.state.get("protected_upto")
        if ctx.engine is None or protected_upto is not None:
            return self._window_select(units, ctx, protected_upto)
        return await self._engine_select(units, ctx)

    def _pinned_indices(self, units: list[ContextUnit]) -> set[int]:
        pinned: set[int] = set()
        newest_user: int | None = None
        for idx, unit in enumerate(units):
            if unit.kind == "system":
                pinned.add(idx)
            elif unit.kind == "user":
                newest_user = idx
        if newest_user is not None:
            pinned.add(newest_user)
        return pinned

    def _unit_tokens(self, unit: ContextUnit) -> int:
        return sum(_message_tokens_heuristic(m) for m in unit.messages)

    async def _engine_select(
        self, units: list[ContextUnit], ctx: ChainContext
    ) -> tuple[list[ContextUnit], ProcessorResult]:
        if ctx.engine is None:  # pragma: no cover — caller guards via run_mode
            return units, ProcessorResult(processor=self.name, metadata={"mode": "engine"})
        flat = flatten_units(units)
        selected = ctx.engine.select_messages(flat, ctx.budget)
        if len(selected) >= len(flat):
            return units, ProcessorResult(processor=self.name, metadata={"dropped_units": 0})
        kept_ids = {id(m) for m in selected}
        # Heal unit atomicity: a unit with any kept message is re-added whole.
        kept_units: list[ContextUnit] = []
        dropped_list: list[ContextUnit] = []
        for unit in units:
            if any(id(m) in kept_ids for m in unit.messages):
                kept_units.append(unit)
            else:
                dropped_list.append(unit)
        ctx.state["dropped_units"] = dropped_list
        return kept_units, ProcessorResult(
            processor=self.name,
            level=LEVEL_DROP if dropped_list else None,
            tokens_saved=ctx.tokens(flat) - ctx.tokens(flatten_units(kept_units)),
            metadata={"dropped_units": len(dropped_list), "mode": "engine"},
        )

    def _window_select(
        self,
        units: list[ContextUnit],
        ctx: ChainContext,
        protected_upto: int | None,
    ) -> tuple[list[ContextUnit], ProcessorResult]:
        pinned = self._pinned_indices(units)
        protected_upto = protected_upto if protected_upto is not None else 0
        # The stable prefix [0, protected_upto) is kept by default — it is
        # what the provider's prompt cache pays for. Selection drops only in
        # the tail; the prefix is yielded (oldest-first) only when the budget
        # cannot cover it.
        protected = {i for i in range(protected_upto) if i not in pinned}
        kept: set[int] = set(pinned) | protected
        used = sum(self._unit_tokens(units[i]) for i in kept)
        for idx in range(len(units) - 1, protected_upto - 1, -1):
            if idx in kept:
                continue
            tokens = self._unit_tokens(units[idx])
            if used + tokens > ctx.budget:
                continue
            kept.add(idx)
            used += tokens
        yielded = False
        if used > ctx.budget:
            yielded = True
            for idx in sorted(protected):
                if used <= ctx.budget:
                    break
                used -= self._unit_tokens(units[idx])
                kept.discard(idx)
        kept_units = [units[i] for i in sorted(kept)]
        dropped_list = [units[i] for i in range(len(units)) if i not in kept]
        ctx.state["dropped_units"] = dropped_list
        return kept_units, ProcessorResult(
            processor=self.name,
            level=LEVEL_DROP if dropped_list else None,
            tokens_saved=ctx.tokens(flatten_units(units)) - used,
            metadata={
                "dropped_units": len(dropped_list),
                "mode": "window",
                "prefix_protection_yielded": yielded,
            },
        )


class OffloadProcessor(ContextProcessor):
    """Offload the dropped leading block to the environment, replace with a stub.

    Runs after selection so compression (lossy) never fires before the
    recoverable path. Uses the ``ContextOffloader`` from the execution
    context; on failure logs and falls through to compression.
    """

    name = "offload"

    def __init__(self, threshold_tokens: int = 6000) -> None:
        self.threshold_tokens = threshold_tokens

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        offloader = ctx.offloader
        if offloader is None or not getattr(offloader, "is_enabled", lambda: False)():
            return units, ProcessorResult(processor=self.name, metadata={"offloaded": False, "reason": "no_offloader"})
        # The window processor stashes the units it dropped; offload covers
        # the whole dropped block (the leading prefix for suffix engines).
        dropped_units: list[ContextUnit] = ctx.state.get("dropped_units") or []
        if not dropped_units:
            noop = ProcessorResult(processor=self.name, metadata={"offloaded": False, "reason": "nothing_dropped"})
            return units, noop
        dropped_messages = flatten_units(dropped_units)
        dropped_tokens = ctx.tokens(dropped_messages)
        threshold = getattr(offloader, "threshold_tokens", self.threshold_tokens)
        if dropped_tokens < threshold:
            return units, ProcessorResult(
                processor=self.name,
                metadata={"offloaded": False, "reason": "below_threshold", "dropped_tokens": dropped_tokens},
            )
        try:
            stub_msg = await offloader.offload(dropped_messages, ctx.session_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("Context offload failed, falling back to compress: %s", e)
            return units, ProcessorResult(
                processor=self.name,
                metadata={"offloaded": False, "reason": "offload_failed", "error": str(e)},
            )
        return [ContextUnit([stub_msg]), *units], ProcessorResult(
            processor=self.name,
            tokens_saved=0,
            metadata={"offloaded": True, "dropped_units": len(dropped_units), "dropped_tokens": dropped_tokens},
        )


class CompressionProcessor(ContextProcessor):
    """Compress the projection when it still exceeds the budget.

    Delegates to ``engine.compress`` when an engine is present (legacy
    equivalence); otherwise keeps the newest half with system units pinned.

    Two backends (ADR-033):

    - ``projection`` (default): transient, non-destructive per-invocation
      compression of the projection only.
    - ``surface_replacement``: durable compaction recorded as bracket events
      on the execution log; the working surface becomes summary + retained
      tail while originals stay in the log and channel. Triggering is
      capacity-axis (``trigger_ratio × context_window`` on the effective
      surface, evaluated by the chain at the pre-step waterline) and the
      most recent ``retain_ratio`` of the window is never shadowed.
    """

    name = "compression"

    def __init__(
        self,
        backend: str = "projection",
        trigger_ratio: float = 0.8,
        retain_ratio: float = 0.16,
    ) -> None:
        if backend not in ("projection", "surface_replacement"):
            raise ValueError(f"compression backend must be 'projection' or 'surface_replacement', got {backend!r}")
        if not isinstance(trigger_ratio, (int, float)) or isinstance(trigger_ratio, bool) or not 0 < trigger_ratio <= 1:
            raise ValueError(f"trigger_ratio must be in (0, 1], got {trigger_ratio!r}")
        if not isinstance(retain_ratio, (int, float)) or isinstance(retain_ratio, bool) or not 0 < retain_ratio < 1:
            raise ValueError(f"retain_ratio must be in (0, 1), got {retain_ratio!r}")
        self.backend = backend
        self.trigger_ratio = float(trigger_ratio)
        self.retain_ratio = float(retain_ratio)

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        if self.backend == "surface_replacement":
            return await run_surface_replacement(units, ctx, self.trigger_ratio, self.retain_ratio)
        before = ctx.tokens(flatten_units(units))
        if ctx.engine is not None:
            compressed_msgs = ctx.engine.compress(flatten_units(units))
            kept_ids = {id(m) for m in compressed_msgs}
            kept_units = [u for u in units if any(id(m) in kept_ids for m in u.messages)]
        else:
            pinned = set[int]()
            newest_user: int | None = None
            for idx, unit in enumerate(units):
                if unit.kind == "system":
                    pinned.add(idx)
                elif unit.kind == "user":
                    newest_user = idx
            keep_count = max(2, len(units) // 2)
            order = [i for i in range(len(units) - 1, -1, -1) if i not in pinned]
            kept: set[int] = set(pinned)
            if newest_user is not None:
                kept.add(newest_user)
            for idx in order:
                if len(kept) >= keep_count:
                    break
                kept.add(idx)
            kept_units = [units[i] for i in sorted(kept)]
        after = ctx.tokens(flatten_units(kept_units))
        saved_pct = ((before - after) / before * 100.0) if before else 0.0
        return kept_units, ProcessorResult(
            processor=self.name,
            # Compression only runs while over budget, so executing it is
            # itself a degradation — even when the backend could not shrink
            # the projection further.
            level=LEVEL_COMPRESS,
            tokens_saved=max(0, before - after),
            metadata={"backend": self.backend, "savings_pct": round(saved_pct, 2)},
        )


class TerminationProcessor(ContextProcessor):
    """Final level: controlled termination instead of silent truncation.

    Ensures system units and the newest user unit survive (re-inserting them
    from the original projection if compression dropped them), records
    ``stop_reason="token_capped"``. The worker suppresses the response's tool
    calls so the agent loop finalizes without new tool invocations.
    """

    name = "terminate"

    def __init__(self, stop_reason: str = STOP_REASON_BUDGET_CAPPED) -> None:
        self.stop_reason = stop_reason

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        has_system = any(u.kind == "system" for u in units)
        has_user = any(u.kind == "user" for u in units)
        out = units
        if not has_system or not has_user:
            originals: list[ContextUnit] = ctx.state.get("original_units") or []
            existing = {id(u) for u in units}
            restored: list[ContextUnit] = []
            if not has_system:
                restored.extend(u for u in originals if u.kind == "system")
            if not has_user:
                newest_user = next((u for u in reversed(originals) if u.kind == "user"), None)
                if newest_user is not None and id(newest_user) not in existing:
                    restored.append(newest_user)
            if restored:
                out = [*restored, *units]
        return out, ProcessorResult(
            processor=self.name,
            level=LEVEL_TERMINATE,
            metadata={"stop_reason": self.stop_reason},
        )


# -- Memory integration (agent-memory-tools) ------------------------------------
# Both processors self-disable outside their enabling conditions so appending
# them to the default chain keeps flag-off behavior byte-identical. They are
# deliberately NOT part of the policy processor registry (ADR-029 trust
# boundary): policy-configured chains keep their exact configured composition,
# and the canonical policy hash vocabulary is unchanged.

_PREFETCH_MARKER = "[memory_context]"
_ESCALATION_MARKER = "[memory_hint]"
_PREFETCH_QUERY_MAX_CHARS = 2000
_ESCALATION_COOLDOWN_SECONDS = 120.0
# Session-scoped cooldown for the escalation hint. Module-level because the
# default chain (and its processors) is rebuilt per invocation — an instance
# attribute would reset every call and never debounce anything.
_ESCALATION_LAST_HINT: OrderedDict[str, float] = OrderedDict()
_ESCALATION_LAST_HINT_MAX = 4096


class MemoryPrefetchProcessor(ContextProcessor):
    """Inject a memory context block before the LLM call (prefetch channel).

    Queries the active memory provider with the most recent conversation text
    and appends a ``[memory_context]`` block at the projection tail — after
    the KV-cache protected prefix (never invalidating it) and counted in the
    projection's token accounting. Skips injection when disabled, over
    budget, without scope, or on any provider failure (prefetch is strictly
    best-effort: it must never block or fail the turn).
    """

    name = "memory_prefetch"
    run_mode = "always"

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self._timeout_seconds = timeout_seconds

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        from hecate.core.config import settings

        meta: dict[str, Any] = {"injected": False}
        if not settings.MEMORY_PREFETCH_ENABLED:
            meta["reason"] = "disabled"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        if ctx.tokens(flatten_units(units)) > ctx.budget:
            # The projection is already over budget — injecting more context
            # would only push toward controlled termination.
            meta["reason"] = "over_budget"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        execution_context = ctx.execution_context or {}
        raw_ws = execution_context.get("workspace_id")
        raw_agent = execution_context.get("agent_id")
        if not raw_ws or not raw_agent:
            meta["reason"] = "no_scope"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        query_text = self._recent_text(units)
        if not query_text:
            meta["reason"] = "no_query"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        from hecate.core.composition.memory_provider import (
            CAP_PREFETCH,
            provider_supports,
            resolve_memory_provider,
        )

        provider = resolve_memory_provider()
        if provider is None or not provider_supports(provider, CAP_PREFETCH):
            meta["reason"] = "provider_unavailable"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        try:
            entries = await asyncio.wait_for(
                provider.prefetch(
                    query_text=query_text,
                    workspace_id=uuid.UUID(str(raw_ws)),
                    agent_id=uuid.UUID(str(raw_agent)),
                    max_entries=int(settings.MEMORY_PREFETCH_MAX_ENTRIES),
                    max_tokens=int(settings.MEMORY_PREFETCH_MAX_TOKENS),
                ),
                timeout=self._timeout_seconds,
            )
        except Exception as e:
            logger.warning("Memory prefetch failed on session %s: %s", ctx.session_id, e)
            meta["reason"] = "provider_error"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        if not entries:
            meta["reason"] = "empty"
            return units, ProcessorResult(processor=self.name, metadata=meta)

        lines = [f"- {e.content}" for e in entries]
        block = {"role": "user", "content": _PREFETCH_MARKER + "\n" + "\n".join(lines)}
        out = [*units, ContextUnit([block])]
        meta.update({"injected": True, "entries": len(lines), "tokens": ctx.tokens([block])})
        return out, ProcessorResult(processor=self.name, metadata=meta)

    @staticmethod
    def _recent_text(units: list[ContextUnit]) -> str:
        """The most recent user/assistant exchange, as the prefetch query."""
        recent: list[str] = []
        for msg in reversed(flatten_units(units)):
            if msg.get("role") not in ("user", "assistant"):
                continue
            content = msg.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            recent.append(content.strip())
            if len(recent) >= 2:
                break
        text = "\n".join(reversed(recent))
        return text[:_PREFETCH_QUERY_MAX_CHARS]


class RetrievalEscalationHintProcessor(ContextProcessor):
    """Nudge the model to iterate when a memory search came back weak.

    The tool loop sets ``execution_context["memory_retrieval_low_signal"]``
    when a memory/conversation search returns empty or below-threshold
    results; this processor consumes the marker and appends one
    ``[memory_hint]`` block telling the model how to iterate (reformulate,
    narrow the window, use the cursor, exclude inspected sessions). Debounced
    per session — at most one hint per cooldown window.
    """

    name = "retrieval_escalation_hint"
    run_mode = "always"

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        execution_context = ctx.execution_context or {}
        if not execution_context.get("memory_retrieval_low_signal"):
            return units, ProcessorResult(processor=self.name, metadata={"hinted": False})
        # Consume the marker: one hint per weak-search turn, regardless of
        # how many weak searches fired.
        execution_context.pop("memory_retrieval_low_signal", None)

        now = time.monotonic()
        session_key = ctx.session_id or "anonymous"
        last = _ESCALATION_LAST_HINT.get(session_key)
        if last is not None and (now - last) < _ESCALATION_COOLDOWN_SECONDS:
            return units, ProcessorResult(processor=self.name, metadata={"hinted": False, "cooldown": True})

        _ESCALATION_LAST_HINT[session_key] = now
        _ESCALATION_LAST_HINT.move_to_end(session_key)
        while len(_ESCALATION_LAST_HINT) > _ESCALATION_LAST_HINT_MAX:
            _ESCALATION_LAST_HINT.popitem(last=False)

        hint = {
            "role": "user",
            "content": (
                f"{_ESCALATION_MARKER} Your recent memory or conversation search returned weak or empty "
                "results. Before concluding the information does not exist: reformulate with different "
                "keywords, adjust start_date/end_date, page through results with the cursor, or skip "
                "already-inspected sessions via exclude_session_ids."
            ),
        }
        return [*units, ContextUnit([hint])], ProcessorResult(processor=self.name, metadata={"hinted": True})


class HintProcessor(ContextProcessor):
    """Inject runtime state hints (elapsed time, context usage) into the projection.

    Hints are appended as persistent user-role messages; the system prompt is
    never modified. Each hint type fires only when its condition holds, and
    time hints are deduplicated per interval in the chain's session state.
    A pending-task hint is supported through a ``task_state_provider``
    callable returning a non-empty summary string when tasks are pending.
    """

    name = "hint"
    run_mode = "always"

    def __init__(
        self,
        time_interval_minutes: int = 60,
        usage_buffer_ratio: float = 0.7,
        task_state_provider: Callable[[], str] | None = None,
    ) -> None:
        self.time_interval_minutes = time_interval_minutes
        self.usage_buffer_ratio = usage_buffer_ratio
        self.task_state_provider = task_state_provider

    async def process(self, units: list[ContextUnit], ctx: ChainContext) -> tuple[list[ContextUnit], ProcessorResult]:
        hints: list[str] = []
        now = time.monotonic()
        key = f"last_time_hint:{ctx.session_id}:{id(self)}"
        last = ctx.state.get(key)
        if last is None or (now - last) >= self.time_interval_minutes * 60:
            ctx.state[key] = now
            from datetime import UTC, datetime

            hints.append(
                f"[hint] Time check: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}. "
                "Re-anchor on the current time if your task depends on it."
            )

        tokens = ctx.tokens(flatten_units(units))
        usage_at = ctx.budget * self.usage_buffer_ratio
        if tokens >= usage_at:
            hints.append(
                f"[hint] Context usage: {tokens}/{ctx.budget} tokens "
                f"({self.usage_buffer_ratio:.0%} buffer threshold). "
                "Compaction will trigger as the budget fills."
            )
        if self.task_state_provider is not None:
            summary = self.task_state_provider()
            if summary:
                hints.append(f"[hint] Pending tasks: {summary}")

        if not hints:
            return units, ProcessorResult(processor=self.name, metadata={"hints": 0})
        out = [*units, ContextUnit([{"role": "user", "content": " ".join(hints)}])]
        return out, ProcessorResult(processor=self.name, metadata={"hints": len(hints)})


# ---------------------------------------------------------------------------
# Budget resolution and snapshot emission
# ---------------------------------------------------------------------------


def resolve_budget(node_config: dict[str, Any], execution_context: dict[str, Any] | None) -> int:
    """Token budget priority: node config > runtime budget > model default > 8000."""
    node_budget = node_config.get("max_tokens")
    if isinstance(node_budget, int) and node_budget > 0:
        return node_budget
    if execution_context:
        ctx_budget = execution_context.get("context_budget")
        if isinstance(ctx_budget, int) and ctx_budget > 0:
            return ctx_budget
        model_default = execution_context.get("context_budget_model_default")
        if isinstance(model_default, int) and model_default > 0:
            return model_default
    return _DEFAULT_BUDGET


async def emit_budget_snapshot(
    *,
    execution_context: dict[str, Any] | None,
    node_id: str,
    report: ChainReport,
    budget: int,
) -> None:
    """Persist a budget degradation snapshot to the EventStore (fold-skipped).

    Additive relative to the 4.10 schema: ``levels`` may now include
    ``warn``/``terminate`` and a ``stop_reason`` field appears when the
    terminate level fired. Consumers reading the 4.10 fields are unaffected.
    """
    if not execution_context:
        return
    event_store = execution_context.get("event_store")
    if event_store is None:
        return
    payload: dict[str, Any] = {
        "event_name": "BUDGET_SNAPSHOT",
        "budget": budget,
        "tokens_before": report.tokens_before,
        "tokens_after": report.tokens_after,
        "messages_before": report.messages_before,
        "messages_after": report.messages_after,
        "levels": report.levels,
    }
    if report.stop_reason is not None:
        payload["stop_reason"] = report.stop_reason
    if report.cache_hit_rate is not None:
        payload["cache_hit_rate"] = report.cache_hit_rate
    try:
        from hecate.runtime.eventstore import Event, EventType

        await event_store.append(
            Event(
                session_id=execution_context["session_id"],
                superstep=execution_context.get("superstep", 0),
                event_type=EventType.CUSTOM,
                node_id=node_id,
                trace_id=execution_context.get("trace_id"),
                payload=payload,
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("Budget snapshot emission failed on node '%s'", node_id, exc_info=True)


def cache_hit_rate_from_usage(usage: dict[str, Any] | None) -> float | None:
    """Compute cache hit rate (cached / total input tokens) from provider usage.

    Returns None when the provider did not report cache usage.
    """
    if not usage:
        return None
    cached = usage.get("cached_tokens", usage.get("cache_read_input_tokens"))
    total = usage.get("prompt_tokens", usage.get("input_tokens"))
    if not isinstance(total, (int, float)) or total <= 0:
        return None
    if not isinstance(cached, (int, float)) or cached < 0:
        return None
    return round(cached / total, 4)


# ---------------------------------------------------------------------------
# The chain executor
# ---------------------------------------------------------------------------


class ContextProcessorChain:
    """Ordered, budget-satisfied context projection pipeline (4.13).

    Processors run in declaration order. "when_over_budget" processors are
    skipped once the projection fits the budget (satisfied predicate). Every
    processor reports a ``ProcessorResult``; the chain aggregates them into a
    ``ChainReport`` and emits one budget snapshot when any degradation level
    fired. The session-scoped latches (warn crossing, time hints) live on the
    chain instance, so a chain should be reused across the invocations of a
    session/run — the composition layer guarantees this.
    """

    def __init__(
        self,
        processors: list[ContextProcessor],
        failure_policy: FailurePolicy | None = None,
        session_state: dict[str, Any] | None = None,
        compaction_summarizer: Any = None,
        model_window_provider: Callable[[str], Any] | None = None,
        compaction_listener: Callable[[str, str], None] | None = None,
    ) -> None:
        self.processors = list(processors)
        self.failure_policy = failure_policy or FailurePolicy()
        # Session-scoped memory shared by every apply() of this chain (warn
        # latch, time-hint dedup, anchor state, compaction ledger cache).
        # Keyed by session_id.
        self.session_state: dict[str, Any] = session_state if session_state is not None else {}
        # ADR-033 durable compaction wiring (composition-layer adapters).
        self._compaction_summarizer = compaction_summarizer
        self._model_window_provider = model_window_provider
        self._compaction_listener = compaction_listener
        self._last_compression_savings: dict[str, float | None] = {}
        self._messages_at_last_degradation: dict[str, int] = {}

    def note_provider_usage(
        self, session_id: str, usage: dict[str, Any], covered_messages: list[dict[str, Any]]
    ) -> None:
        """Anchor future estimates on provider-reported usage for a covered prefix."""
        prompt_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        if not isinstance(prompt_tokens, (int, float)) or prompt_tokens <= 0:
            return
        anchor_chars = sum(_message_chars(m) for m in covered_messages)
        self.session_state[f"anchor:{session_id}"] = AnchorTokenEstimator(
            anchor_prompt_tokens=int(prompt_tokens), anchor_chars=anchor_chars
        )

    def _estimator_for(self, session_id: str, engine: ContextEngine | None = None) -> TokenEstimator:
        anchor = self.session_state.get(f"anchor:{session_id}")
        if isinstance(anchor, AnchorTokenEstimator):
            return anchor
        if engine is not None:
            return _EngineEstimator(engine)
        return HeuristicTokenEstimator()

    async def apply(
        self,
        messages: list[dict[str, Any]],
        node_config: dict[str, Any],
        execution_context: dict[str, Any] | None,
        node_id: str = "",
    ) -> ChainReport:
        engine = None
        offloader = None
        session_id = ""
        if execution_context:
            engine = execution_context.get("context_engine")
            offloader = execution_context.get("context_offloader")
            raw_sid = execution_context.get("session_id")
            session_id = str(raw_sid) if raw_sid is not None else ""

        # ADR-033 substrate: derive the effective surface from the shadowing
        # ledger before any processor runs. The ledger applies to every
        # chain regardless of node policy; the backend selection only
        # decides whether this chain may trigger a new compaction.
        raw_messages = list(messages)
        surface = raw_messages
        projection_origin: list[int | None] = list(range(len(raw_messages)))
        projection_shadow: list[Any] = [None] * len(raw_messages)
        ledger = None
        window: int | None = None
        backend = next(
            (p for p in self.processors if isinstance(p, CompressionProcessor) and p.backend == "surface_replacement"),
            None,
        )
        if raw_messages and execution_context and execution_context.get("event_store") is not None and session_id:
            try:
                ledger = await load_compaction_state(execution_context["event_store"], session_id, self.session_state)
                projection = apply_ledger(raw_messages, ledger.entries)
                surface = projection.messages
                projection_origin = projection.origin
                projection_shadow = projection.shadow_source
            except Exception:  # noqa: BLE001 — ledger read failure degrades to the unshadowed surface
                logger.warning(
                    "Compaction ledger read failed on session %s; projecting unshadowed", session_id, exc_info=True
                )
                ledger = None
            if backend is not None:
                window = await resolve_context_window(node_config, execution_context, self._model_window_provider)
                if window is None:
                    from hecate.runtime.context_policy import ChainPolicyError

                    raise ChainPolicyError(
                        "compression backend 'surface_replacement' requires a resolvable context window "
                        "(execution_context['context_window'], a model-window provider, or a known model family)"
                    )

        budget = resolve_budget(node_config, execution_context)
        estimator = self._estimator_for(session_id, engine)
        ctx = ChainContext(
            budget=budget,
            estimator=estimator,
            node_config=node_config,
            execution_context=execution_context,
            node_id=node_id,
            session_id=session_id,
            engine=engine,
            offloader=offloader,
            tool_result_limit=_resolve_tool_result_limit(node_config),
            offload_threshold_tokens=getattr(offloader, "threshold_tokens", 6000) if offloader else 6000,
            state={},
        )
        ctx.state[STATE_RAW_MESSAGES] = raw_messages
        ctx.state[STATE_LEDGER] = ledger
        ctx.state[STATE_ORIGIN] = projection_origin
        ctx.state[STATE_SHADOW_SOURCE] = projection_shadow
        ctx.state[STATE_WINDOW] = window
        ctx.state[STATE_LISTENER] = self._compaction_listener
        ctx.state[STATE_SUMMARIZER] = self._compaction_summarizer
        # Capacity axis: evaluated on the effective surface (post-shadowing,
        # pre-ladder) at the pre-step waterline — deliberately independent of
        # the budget-ladder satisfaction short-circuit below.
        if (
            backend is not None
            and window is not None
            and surface_token_estimate(surface) >= window * backend.trigger_ratio
        ):
            ctx.state[STATE_TRIGGERED] = True

        units = unitize(surface)
        ctx.state["original_units"] = list(units)
        tokens_before = ctx.tokens(flatten_units(units))
        report = ChainReport(
            messages=list(surface),
            tokens_before=tokens_before,
            messages_before=len(surface),
        )
        if not messages:
            return report

        force_surface = bool(ctx.state.get(STATE_TRIGGERED))
        new_units = units
        for processor in self.processors:
            over = ctx.tokens(flatten_units(new_units)) > budget
            surface_slot = (
                force_surface
                and isinstance(processor, CompressionProcessor)
                and processor.backend == "surface_replacement"
            )
            if processor.run_mode != "always" and not over and not surface_slot:
                continue
            if isinstance(processor, CompressionProcessor):
                forced = bool(node_config.get("context_force_degradation")) or surface_slot
                last_savings = self._last_compression_savings.get(session_id)
                new_messages = report.messages_before - self._messages_at_last_degradation.get(session_id, 0)
                if self.failure_policy.should_anti_thrash(processor.name, last_savings, new_messages, forced):
                    report.metadata["compression_anti_thrashed"] = True
                    continue
            if not self.failure_policy.allow(processor.name):
                self.failure_policy.record_fallback_success(processor.name)
                continue
            try:
                new_units, result = await processor.process(new_units, ctx)
            except Exception as e:  # noqa: BLE001
                logger.warning("Processor '%s' failed on node '%s': %s", processor.name, node_id, e)
                self.failure_policy.record_failure(processor.name)
                continue
            self.failure_policy.record_success(processor.name)
            if isinstance(processor, CompressionProcessor):
                savings = result.metadata.get("savings_pct")
                if savings is not None:
                    self._last_compression_savings[session_id] = float(savings)
                    self._messages_at_last_degradation[session_id] = report.messages_before
            if result.level and result.level not in report.levels:
                report.levels.append(result.level)
            if isinstance(processor, TerminationProcessor):
                report.stop_reason = result.metadata.get("stop_reason")
            report.metadata[processor.name] = result.metadata

        flat = flatten_units(new_units)
        report.messages = flat
        report.tokens_after = ctx.tokens(flat)
        report.messages_after = len(flat)
        if report.levels:
            await emit_budget_snapshot(
                execution_context=execution_context,
                node_id=node_id,
                report=report,
                budget=budget,
            )
        return report


def _resolve_tool_result_limit(node_config: dict[str, Any]) -> int:
    limit = node_config.get("tool_result_limit", _DEFAULT_TOOL_RESULT_LIMIT)
    if not isinstance(limit, int) or limit <= 0:
        return _DEFAULT_TOOL_RESULT_LIMIT
    return limit


def default_chain_processors() -> list[ContextProcessor]:
    """The default chain: legacy five-step equivalence (warn/hints opt-in).

    Order encodes the lossiness ladder — cheap/recoverable first:
    truncation → KV-cache guard → window selection → offload → compression →
    controlled termination → memory integration (prefetch + escalation hint;
    both self-disable outside their enabling conditions, so flag-off chains
    behave exactly as before).
    """
    return [
        ToolResultTruncationProcessor(),
        KVCacheAwareProcessor(),
        RoundWindowProcessor(),
        OffloadProcessor(),
        CompressionProcessor(),
        TerminationProcessor(),
        MemoryPrefetchProcessor(),
        RetrievalEscalationHintProcessor(),
    ]
