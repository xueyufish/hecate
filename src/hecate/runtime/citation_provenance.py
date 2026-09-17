"""In-band citation provenance for tool-grounded agent output (1.3.5e Stage 1).

Tool results entering conversation history are deterministically chunked and
prefixed with session-scoped citation markers (``【N-M】``: N = per-session
tool-result sequence number, M = chunk index within that result). A
session-scoped append-only registry resolves every issued marker for the
lifetime of the session — regardless of projection state, so window
selection, offloading, or compression never invalidate a citation. Model
responses are back-mapped to the chunks they cite via a deterministic
pattern scan, and the ratio of factual sentences lacking any citation is
recorded as a warn-level risk signal.

The layer is observational: it never intercepts, blocks, or rewrites a
response, and it invokes no model. It is the grounding-source substrate a
later scoring stage (claim → entailment → confidence) builds on.

Audit trail: chunk registrations, response citation maps, and risk signals
are appended to the EventStore as additive CUSTOM events (``CITATION_*``
``event_name`` payloads, mirroring the ``BUDGET_SNAPSHOT`` pattern). A
registry rebuilt from those events resolves markers after process restarts;
unresolvable markers are recorded as unresolved, never an error.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Marker syntax: 【N-M】 with N = tool-result sequence, M = chunk index.
MARKER_PATTERN = re.compile(r"【(\d+)-(\d+)】")
CITATIONS_METADATA_KEY = "citations"

# Execution-context key carrying the CitationProvenanceManager (aligned with
# ``context_chain`` / ``context_offloader`` passthroughs).
EXECUTION_CONTEXT_KEY = "citation_provenance"

INSTRUCTION_TAG = "[citation_instructions]"
CITATION_INSTRUCTION = (
    f"{INSTRUCTION_TAG} When you state facts derived from tool results, end the "
    "sentence with the supporting chunk marker, e.g. 【2-3】. Cite each factual "
    "sentence individually; never invent marker numbers — only use markers "
    "present in this conversation. Statements not derived from tool results "
    "need no citation."
)

# Event names (EventType.CUSTOM payloads, BUDGET_SNAPSHOT pattern).
EVENT_NAME_REGISTERED = "CITATION_REGISTERED"
EVENT_NAME_MAP = "CITATION_MAP"
EVENT_NAME_RISK = "CITATION_RISK"

RISK_LEVEL_WARN = "warn"


@dataclass
class ChunkEntry:
    """One registered chunk: a marker bound to its source text."""

    marker: str
    result_seq: int
    chunk_index: int
    text: str
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "result_seq": self.result_seq,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChunkEntry:
        return cls(
            marker=str(data["marker"]),
            result_seq=int(data["result_seq"]),
            chunk_index=int(data["chunk_index"]),
            text=str(data["text"]),
            truncated=bool(data.get("truncated", False)),
        )


@dataclass
class CitationMap:
    """Back-mapped citations of one response."""

    cited: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"cited": list(self.cited), "unresolved": list(self.unresolved)}


class CitationRegistry:
    """Session-scoped, append-only registry of issued citation markers.

    Identifiers are never reused within a session. Entries stay resolvable
    for the session's lifetime regardless of projection state — dropping,
    offloading, or compressing the underlying message in a projection never
    invalidates an entry. The registry never mutates channel state or the
    event log; it is an in-memory index over what was marked, rebuildable
    from ``CITATION_REGISTERED`` events.
    """

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._entries: dict[str, ChunkEntry] = {}
        self._next_result_seq = 0

    def next_result_seq(self) -> int:
        """Allocate the next tool-result sequence number."""
        seq = self._next_result_seq
        self._next_result_seq += 1
        return seq

    def register(self, result_seq: int, chunks: list[dict[str, Any]]) -> list[ChunkEntry]:
        """Register the chunks of one marked tool result (append-only)."""
        entries: list[ChunkEntry] = []
        for chunk_index, text in enumerate(chunks):
            entry = ChunkEntry(
                marker=f"{result_seq}-{chunk_index}",
                result_seq=result_seq,
                chunk_index=chunk_index,
                text=text,
            )
            self._entries[entry.marker] = entry
            entries.append(entry)
        return entries

    def resolve(self, marker: str) -> ChunkEntry | None:
        """Resolve a marker to its chunk, or None when never issued."""
        return self._entries.get(marker)

    def mark_truncated(self, marker: str) -> bool:
        """Record that truncation shortened a chunk (partial presence)."""
        entry = self._entries.get(marker)
        if entry is None:
            return False
        entry.truncated = True
        return True

    def entries(self) -> dict[str, ChunkEntry]:
        return dict(self._entries)


def chunk_text(content: str, granularity: int) -> list[str]:
    """Split string content into deterministic chunks of ~granularity chars.

    Splits at whitespace boundaries when possible so chunks are readable;
    oversized unbroken runs (no whitespace) are hard-split. Never returns
    empty chunks.
    """
    if granularity <= 0:
        return [content]
    chunks: list[str] = []
    start = 0
    total = len(content)
    while start < total:
        end = min(start + granularity, total)
        if end < total:
            # Prefer a whitespace cut inside the last 30% of the window so
            # the next chunk starts on a word boundary.
            cut = content.rfind(" ", start + granularity * 7 // 10, end)
            if cut > start:
                end = cut + 1
        chunk = content[start:end]
        if chunk.strip():
            chunks.append(chunk)
        start = end
    return chunks or ([content] if content else [])


def mark_tool_result(
    content: str,
    registry: CitationRegistry,
    granularity: int,
    min_chars: int,
) -> tuple[str, list[ChunkEntry]]:
    """Mark one tool result: chunk, prefix markers, register the chunks.

    Returns the marked content (unchanged when below ``min_chars``) and the
    registered entries (empty when unmarked).
    """
    if len(content) < min_chars:
        return content, []
    result_seq = registry.next_result_seq()
    parts = chunk_text(content, granularity)
    entries = registry.register(result_seq, parts)
    marked = "".join(f"【{e.marker}】{e.text}" for e in entries)
    return marked, entries


class CitationBackMapper:
    """Deterministic citation extraction from a response (no model call)."""

    def map_response(self, response_text: str, registry: CitationRegistry) -> CitationMap:
        """Extract all markers in the response and classify against the registry.

        References to markers never issued in the session are recorded as
        unresolved rather than dropped; the response itself is never modified.
        """
        cited: list[str] = []
        unresolved: list[str] = []
        for match in MARKER_PATTERN.finditer(response_text):
            # Registry keys are bare ids; the recorded form keeps brackets so
            # citation maps echo exactly what appeared in the response.
            bare = f"{match.group(1)}-{match.group(2)}"
            marker = match.group(0)
            if registry.resolve(bare) is not None:
                if marker not in cited:
                    cited.append(marker)
            elif marker not in unresolved:
                unresolved.append(marker)
        return CitationMap(cited=cited, unresolved=unresolved)


# D8 heuristic exclusion lists (see change design.md).
_QUESTION_PREFIXES = (
    "什么",
    "如何",
    "怎么",
    "是否",
    "哪",
    "吗",
    "what",
    "how",
    "why",
    "when",
    "where",
    "which",
    "who",
)
_PROCEDURAL_PREFIXES = (
    "请提供",
    "请确认",
    "让我",
    "我将",
    "我会",
    "let me",
    "let's",
    "i'll",
    "i am going to",
    "i'm going to",
)
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?.])\s*|\n+")
_MIN_FACTUAL_CHARS = 15
_LIST_LINE = re.compile(r"^[\s\-\*\d\.\)]*$")


def factual_sentences(response_text: str) -> list[str]:
    """Extract factual-eligible sentences (the risk signal's denominator).

    Excludes code blocks, inline code spans, questions, procedural/intent
    statements, and bare list/number/date lines per the D8 heuristic. Purely
    a denominator filter — citation mapping is unaffected by misclassification.
    """
    # Drop fenced code blocks entirely before splitting.
    outside_code: list[str] = []
    in_fence = False
    for line in response_text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            outside_code.append(line)
    cleaned = "\n".join(outside_code)

    sentences: list[str] = []
    for raw in _SENTENCE_SPLIT.split(cleaned):
        sentence = raw.strip()
        if not sentence or len(sentence) < _MIN_FACTUAL_CHARS:
            continue
        if _LIST_LINE.match(sentence):
            continue
        lowered = sentence.lower()
        if sentence.endswith(("?", "？")) or lowered.startswith(_QUESTION_PREFIXES):
            continue
        if lowered.startswith(_PROCEDURAL_PREFIXES):
            continue
        sentences.append(sentence)
    return sentences


def uncited_ratio(response_text: str) -> tuple[float, int, int]:
    """Ratio of factual sentences lacking any citation marker.

    Returns (ratio, factual_count, uncited_count); (0.0, 0, 0) when the
    response has no factual sentences.
    """
    sentences = factual_sentences(response_text)
    if not sentences:
        return 0.0, 0, 0
    uncited = sum(1 for s in sentences if not MARKER_PATTERN.search(s))
    return uncited / len(sentences), len(sentences), uncited


def rebuild_registry_from_events(events: list[Any]) -> CitationRegistry:
    """Rebuild a registry from ``CITATION_REGISTERED`` event payloads.

    Tolerant by design: malformed payloads are skipped with a warning.
    After a rebuild, markers never issued before the restart resolve to
    None and are recorded as unresolved by the back mapper.
    """
    highest_seq = -1
    registry = CitationRegistry()
    for event in events:
        payload = getattr(event, "payload", None) or {}
        if payload.get("event_name") != EVENT_NAME_REGISTERED:
            continue
        try:
            result_seq = int(payload["result_seq"])
            chunks = payload["chunks"]
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed CITATION_REGISTERED payload during rebuild")
            continue
        texts = [str(c.get("text", "")) for c in chunks if isinstance(c, dict)]
        registry.register(result_seq, texts)
        for c in chunks:
            if isinstance(c, dict) and c.get("truncated"):
                registry.mark_truncated(f"{result_seq}-{c.get('chunk_index', 0)}")
        highest_seq = max(highest_seq, result_seq)
    registry._next_result_seq = highest_seq + 1
    return registry


class CitationProvenanceManager:
    """Per-session registry holder placed in the execution context.

    Lifecycle mirrors ``ContextChainFactory``: one instance per
    WorkflowExecutionService, handed to ``PregelRuntime``, mirrored into
    every execution context so workers reach the session's registry. The
    optional ``agent_policy`` is the agent-level citation configuration —
    node configuration takes precedence over it, and both over the
    disabled default.
    """

    def __init__(self, agent_policy: dict[str, Any] | None = None) -> None:
        self.agent_policy = agent_policy
        self._registries: dict[str, CitationRegistry] = {}

    def registry_for(self, session_id: str | None) -> CitationRegistry:
        key = str(session_id) if session_id is not None else ""
        registry = self._registries.get(key)
        if registry is None:
            registry = CitationRegistry(session_id=key)
            self._registries[key] = registry
        return registry


async def emit_citation_event(
    *,
    execution_context: dict[str, Any] | None,
    node_id: str,
    event_name: str,
    payload: dict[str, Any],
) -> None:
    """Append one ``CITATION_*`` audit event to the EventStore (fold-skipped).

    Best-effort by contract: a failure to append is logged and never fails
    the invocation.
    """
    if not execution_context:
        return
    event_store = execution_context.get("event_store")
    if event_store is None:
        return
    try:
        from hecate.runtime.eventstore import CURRENT_LOG_SCHEMA_VERSION, Event, EventType

        await event_store.append(
            Event(
                session_id=execution_context["session_id"],
                superstep=execution_context.get("superstep", 0),
                event_type=EventType.CUSTOM,
                node_id=node_id,
                trace_id=execution_context.get("trace_id"),
                payload={
                    "event_name": event_name,
                    "log_schema_version": CURRENT_LOG_SCHEMA_VERSION,
                    **payload,
                },
            )
        )
    except Exception:  # noqa: BLE001
        logger.warning("Citation event %s emission failed on node '%s'", event_name, node_id, exc_info=True)
