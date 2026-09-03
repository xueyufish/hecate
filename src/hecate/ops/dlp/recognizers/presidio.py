"""Presidio-based DLP recognizer.

presidio-analyzer (plus presidio-anonymizer and spaCy model assets)
is an optional dependency declared in the ``[security]`` extra (see
tasks.md §16.3). The recognizer raises :class:`ImportError` at
construction time when ``presidio_analyzer`` is not importable so
callers can decide whether to fail-fast or skip registration.

The Presidio ``AnalyzerEngine`` is loaded lazily on the first
``analyze()`` call so importing this module is cheap even when
Presidio is unused. The heavy spaCy NLP model is only instantiated
when an actual scan runs.

Presidio entity types are mapped to canonical DLP names (see
``_PRESIDIO_TO_CANONICAL``) where overlap exists. Unknown Presidio
entities pass through under their native name so callers retain full
visibility into what Presidio detected.
"""

from __future__ import annotations

import logging

from hecate.ops.dlp.recognizer import DLPRecognizer
from hecate.ops.dlp.result import DLPFinding

logger = logging.getLogger(__name__)

try:
    import presidio_analyzer  # noqa: F401

    _HAS_PRESIDIO = True
except ImportError:
    _HAS_PRESIDIO = False


_PRESIDIO_TO_CANONICAL: dict[str, str] = {
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CREDIT_CARD",
    "US_SSN": "SSN",
    "IP_ADDRESS": "IP_ADDRESS",
    "PERSON": "PERSON",
    "LOCATION": "LOCATION",
    "URL": "URL",
}


class PresidioRecognizer(DLPRecognizer):
    """Wrap ``presidio_analyzer.AnalyzerEngine`` as DLP findings."""

    name = "presidio"
    supported_entities: list[str] = sorted(set(_PRESIDIO_TO_CANONICAL.values()))

    def __init__(self, language: str = "en") -> None:
        if not _HAS_PRESIDIO:
            raise ImportError(
                "PresidioRecognizer requires the 'presidio-analyzer' package. "
                "Install with: uv pip install -e '.[security]'"
            )
        self._language = language
        self._engine = None

    def _get_engine(self) -> object:
        if self._engine is None:
            from presidio_analyzer import AnalyzerEngine

            self._engine = AnalyzerEngine()
        return self._engine

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
    ) -> list[DLPFinding]:
        engine = self._get_engine()
        try:
            results = engine.analyze(text=text, language=self._language)
        except Exception:
            logger.warning("Presidio AnalyzerEngine.analyze failed", exc_info=True)
            return []
        findings: list[DLPFinding] = []
        for result in results:
            raw_entity_type = result.entity_type
            if not raw_entity_type:
                continue
            entity_type = _PRESIDIO_TO_CANONICAL.get(raw_entity_type, raw_entity_type)
            if entities is not None and entity_type not in entities:
                continue
            try:
                start = int(result.start)
                end = int(result.end)
                value = text[start:end]
            except (IndexError, TypeError, ValueError):
                continue
            findings.append(
                DLPFinding(
                    entity_type=entity_type,
                    value=value,
                    start=result.start,
                    end=result.end,
                    score=float(result.score) if result.score is not None else 0.5,
                    recognizer=self.name,
                )
            )
        return findings
