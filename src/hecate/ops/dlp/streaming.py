"""Streaming DLP wrapper for incremental LLM output scanning.

LLM outputs are typically produced in chunks (tokens, sentences,
paragraphs). Scanning each chunk independently would miss entities
that span chunk boundaries — an email address split across two
chunks, for example.

``StreamingDLPWrapper`` accumulates output until a buffer threshold
(default ``300`` chars per design.md §D7) is reached, then asks the
:class:`DLPScanner` to scan the accumulated buffer. The last ``overlap``
characters (default ``10``) of the processed text are retained so the
next chunk can complete entities that started near the boundary.

Enforcement semantics per design.md §D7:

* ``BLOCK`` stops the stream immediately; further ``process_chunk``
  calls return ``None``.
* ``MASK`` replaces detected entities with ``[ENTITY_TYPE]`` place-
  holders. The split point between emitted text and retained overlap
  is chosen so placeholders are not cut in half.
* ``AUDIT`` and ``ALLOW`` keep the original text in the emit.
* ``finalize()`` drains whatever remains in the buffer, emitting the
  full processed remainder (no overlap retention since the stream is
  ending). Any streaming artifacts are recorded via :attr:`corrections`.
"""

from __future__ import annotations

from hecate.ops.dlp.result import DLPAction
from hecate.ops.dlp.scanner import DLPScanner

_DEFAULT_THRESHOLD = 300
_DEFAULT_OVERLAP = 10


class StreamingDLPWrapper:
    """Incrementally scan a stream of text chunks through a :class:`DLPScanner`."""

    def __init__(
        self,
        scanner: DLPScanner,
        *,
        threshold: int = _DEFAULT_THRESHOLD,
        overlap: int = _DEFAULT_OVERLAP,
        direction: str = "llm_output",
        agent_id: str | None = None,
        workspace_id: str | None = None,
        org_id: str | None = None,
    ) -> None:
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if overlap < 0:
            raise ValueError("overlap must be non-negative")
        if overlap >= threshold:
            raise ValueError("overlap must be smaller than threshold")
        self._scanner = scanner
        self._threshold = threshold
        self._overlap = overlap
        self._direction = direction
        self._agent_id = agent_id
        self._workspace_id = workspace_id
        self._org_id = org_id
        self._buffer = ""
        self._blocked = False
        self._final = False
        self._corrections: list[str] = []

    @property
    def threshold(self) -> int:
        return self._threshold

    @property
    def overlap(self) -> int:
        return self._overlap

    @property
    def is_blocked(self) -> bool:
        return self._blocked

    @property
    def is_finalized(self) -> bool:
        return self._final

    @property
    def buffer_length(self) -> int:
        return len(self._buffer)

    @property
    def corrections(self) -> list[str]:
        return list(self._corrections)

    def process_chunk(self, chunk: str) -> str | None:
        """Append ``chunk`` and return the next emit, or ``None`` if BLOCKed.

        Returns ``""`` when the buffer is still below threshold or after
        ``finalize()`` has been called.
        """
        if self._blocked:
            return None
        if self._final:
            return None
        if not chunk:
            return ""
        self._buffer += chunk
        if len(self._buffer) < self._threshold:
            return ""
        return self._scan_and_split()

    def finalize(self) -> str | None:
        """Drain the buffer and return the final emit, or ``None`` if BLOCKed.

        Idempotent — after the first call, returns ``""``. Emits the
        full processed remainder (no overlap retention since the
        stream is ending).
        """
        if self._blocked:
            return None
        if self._final:
            return ""
        self._final = True
        if not self._buffer:
            return ""
        return self._scan_and_emit_all()

    def _scan_and_split(self) -> str | None:
        """Scan the buffer and return the next safe-to-emit prefix."""
        result = self._scanner.scan(
            self._buffer,
            self._direction,
            agent_id=self._agent_id,
            workspace_id=self._workspace_id,
            org_id=self._org_id,
        )
        if result.action == DLPAction.BLOCK:
            self._blocked = True
            self._buffer = ""
            return None

        processed = result.text if result.text is not None else self._buffer
        split_point = self._safe_split_point(processed)
        emit = processed[:split_point]
        self._buffer = processed[split_point:]
        return emit

    def _scan_and_emit_all(self) -> str | None:
        """Scan and emit the entire processed buffer (no overlap retention)."""
        result = self._scanner.scan(
            self._buffer,
            self._direction,
            agent_id=self._agent_id,
            workspace_id=self._workspace_id,
            org_id=self._org_id,
        )
        if result.action == DLPAction.BLOCK:
            self._blocked = True
            self._buffer = ""
            return None
        processed = result.text if result.text is not None else self._buffer
        self._buffer = ""
        return processed

    def _safe_split_point(self, text: str) -> int:
        """Return the largest safe split index that does not break a placeholder.

        Placeholders look like ``[ENTITY_TYPE]``. We walk back from
        the natural split point (``len(text) - overlap``) and check
        whether any ``[`` before it has its matching ``]`` past it.
        If so, the split moves just after the closing ``]``. An unclosed
        ``[`` triggers a correction note and a back-off to before the
        open bracket.
        """
        if len(text) <= self._overlap:
            return 0
        candidate = len(text) - self._overlap
        last_open = text.rfind("[", 0, candidate + 1)
        if last_open == -1:
            return candidate
        close_for_open = text.find("]", last_open)
        if close_for_open == -1:
            self._corrections.append(f"unclosed placeholder at position {last_open}; deferred to next chunk")
            return last_open
        if close_for_open >= candidate:
            self._corrections.append(
                f"placeholder [{text[last_open : close_for_open + 1]}] straddled split; emitted whole placeholder"
            )
            return close_for_open + 1
        return candidate
