"""DLP Recognizer ABC + Registry.

A :class:`DLPRecognizer` inspects a piece of text and emits
:class:`DLPFinding` records for the entity types it knows about.
Subclasses provide the detection logic (regex, secrets library,
NER model, dictionary lookup, etc.).

A :class:`DLPRecognizerRegistry` aggregates many recognizers, runs
them against the same text, and merges the results. Overlapping
findings are deduplicated by keeping the highest-scoring detection
per ``[start, end)`` range; ties keep the first one encountered
(deterministic order based on registry registration).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from hecate.services.security.dlp.result import DLPFinding


class DLPRecognizer(ABC):
    """Base class for all DLP recognizers.

    Subclasses MUST set the class attributes :attr:`name` and
    :attr:`supported_entities`, and implement :meth:`analyze`. The
    abstract ``analyze`` enforces this contract at instantiation
    time: attempting to instantiate :class:`DLPRecognizer` directly
    raises :class:`TypeError`.
    """

    name: str = ""
    supported_entities: list[str] = []

    @abstractmethod
    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        """Scan ``text`` and return all matching findings.

        Args:
            text: The text to scan.
            entities: Optional whitelist of entity types. ``None``
                means "all entities this recognizer supports".

        Returns:
            Findings detected by this recognizer. May be empty.
        """
        raise NotImplementedError


class DLPRecognizerRegistry:
    """Aggregates many :class:`DLPRecognizer` instances.

    The registry is the entry point :class:`DLPScanner` uses to
    collect detections across all configured detection strategies.
    """

    def __init__(self) -> None:
        self._recognizers: dict[str, DLPRecognizer] = {}

    def register(self, recognizer: DLPRecognizer) -> None:
        """Add ``recognizer`` to the registry.

        Overwrites any existing recognizer with the same name.
        Raises :class:`ValueError` if the recognizer has an empty name.
        """
        if not recognizer.name:
            raise ValueError("Recognizer must have a non-empty name")
        self._recognizers[recognizer.name] = recognizer

    def unregister(self, name: str) -> None:
        """Remove the recognizer registered as ``name``.

        No-op if no recognizer is registered under that name.
        """
        self._recognizers.pop(name, None)

    def get(self, name: str) -> DLPRecognizer | None:
        """Return the recognizer registered as ``name``, or ``None``."""
        return self._recognizers.get(name)

    def names(self) -> list[str]:
        """Return the names of all registered recognizers."""
        return list(self._recognizers.keys())

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        """Run every registered recognizer and return merged findings.

        Overlapping ranges (where two recognizers emit findings with
        intersecting ``[start, end)`` spans) are deduplicated by
        keeping the highest-scoring one. Ties keep the recognizer
        that was registered first (deterministic via insertion order).
        """
        all_findings: list[DLPFinding] = []
        for recognizer in self._recognizers.values():
            all_findings.extend(recognizer.analyze(text, entities))
        return self._dedupe_overlapping(all_findings)

    @staticmethod
    def _dedupe_overlapping(findings: list[DLPFinding]) -> list[DLPFinding]:
        """Drop lower-scoring findings whose ranges overlap a kept one.

        Sort key is ``(start asc, score desc, recognizer asc)`` so the
        highest-scoring, earliest-registered finding wins per range.
        After sorting, a finding is kept iff its range does not
        overlap any already-kept finding.
        """
        if not findings:
            return findings
        sorted_findings = sorted(
            findings,
            key=lambda f: (f.start, -f.score, f.recognizer),
        )
        kept: list[DLPFinding] = []
        for finding in sorted_findings:
            overlap = any(not (finding.end <= k.start or finding.start >= k.end) for k in kept)
            if not overlap:
                kept.append(finding)
        return kept
