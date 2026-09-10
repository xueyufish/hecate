"""AI dataset synthesis pipeline (7.2b).

Exports the public entry points used by the API layer:

- :class:`DatasetSynthesisService` — high-level façade
- :class:`DatasetSynthesisJobService` — async job lifecycle
- :class:`SynthesisStrategy` (and concrete subclasses) — generation / evolution /
  adversarial
- :class:`SynthesisFilter` (and concrete subclasses) — embedding dedupe /
  quality / DLP
- :class:`CandidateItem` — internal pipeline item

Internal modules:

- :mod:`hecate.ops.evaluation.synthesis.strategies`
- :mod:`hecate.ops.evaluation.synthesis.filters`
- :mod:`hecate.ops.evaluation.synthesis.service`
- :mod:`hecate.ops.evaluation.synthesis.job`
- :mod:`hecate.ops.evaluation.synthesis.prompts`
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CandidateItem:
    """A single item flowing through the synthesis pipeline.

    Carries everything a filter might need (text fields + provenance tags)
    plus the parent seed for downstream traceability. Fields that survive
    the filter pipeline are persisted to ``EvaluationItemModel``.
    """

    query: str
    expected_answer: str | None = None
    context: list[str] | None = None
    seed_item_id: str | None = None
    strategy: str = ""
    extra_tags: list[str] = field(default_factory=list)

    def tags(self) -> list[str]:
        """Compose the final tag list: provenance + strategy + extras."""
        out: list[str] = ["synthetic", f"strategy:{self.strategy}"]
        out.extend(self.extra_tags)
        if self.seed_item_id:
            out.append(f"seed_item:{self.seed_item_id}")
        return out


__all__ = [
    "CandidateItem",
]


# Late-bound re-exports — avoid circular imports at module load.
def __getattr__(name: str) -> Any:  # pragma: no cover - import surface
    if name in {"DatasetSynthesisService"}:
        from hecate.ops.evaluation.synthesis.service import (
            DatasetSynthesisService,
        )

        return DatasetSynthesisService
    if name in {"DatasetSynthesisJobService"}:
        from hecate.ops.evaluation.synthesis.job import (
            DatasetSynthesisJobService,
        )

        return DatasetSynthesisJobService
    if name in {"GenerationStrategy", "EvolutionStrategy", "AdversarialStrategy"}:
        from hecate.ops.evaluation.synthesis.strategies import (
            AdversarialStrategy,
            EvolutionStrategy,
            GenerationStrategy,
        )

        return {
            "GenerationStrategy": GenerationStrategy,
            "EvolutionStrategy": EvolutionStrategy,
            "AdversarialStrategy": AdversarialStrategy,
        }[name]
    if name in {"EmbeddingDedupeFilter", "QualityFilter", "DLPFilter"}:
        from hecate.ops.evaluation.synthesis.filters import (
            DLPFilter,
            EmbeddingDedupeFilter,
            QualityFilter,
        )

        return {
            "EmbeddingDedupeFilter": EmbeddingDedupeFilter,
            "QualityFilter": QualityFilter,
            "DLPFilter": DLPFilter,
        }[name]
    raise AttributeError(name)
