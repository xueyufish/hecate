"""Intent recognition types and the evidence boundary port (6.23 ⊕ 1.3.10).

The recognition engine produces a **layered result** per user turn:

- **L1 atomic** — single-turn classification over the evidence categories,
  via an ordered fast-path stack (decision cache → patterns → few-shot →
  LLM fallback).
- **L2 workflow** — multi-turn task detection over the session's recent
  atomic-intent window.
- **L3 session** — the overall dialogue goal, carried as durable session
  state and updated by an explicit-shift / drift policy.
- **L4 domain** — an optional label carried on the category itself.
- **L5 policy** — not a recognition level: a category flag marking routes
  that the deterministic approval path must gate regardless of the
  recognition output.

``IntentEvidencePort`` is the cross-layer boundary (runtime ← studio)
through which the engine consumes **published** intent package evidence
only; composition roots supply the concrete provider.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum


class DecisionSource(StrEnum):
    """Which fast path produced the atomic decision."""

    CACHE = "cache"
    PATTERN = "pattern"
    FEW_SHOT = "few_shot"
    LLM = "llm"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class EvidenceCategory:
    """One intent category inside published evidence.

    Attributes:
        name: Category label; the recognition result references this name.
        description: Classification guidance ("what + when").
        domain: Optional L4 domain label.
        policy_gated: L5 marker — routes to this category require the
            deterministic approval path.
        patterns: Optional fast-path regex/keyword patterns.
        samples: Published sample utterances used as few-shot evidence.
    """

    name: str
    description: str | None = None
    domain: str | None = None
    policy_gated: bool = False
    patterns: tuple[str, ...] = ()
    samples: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidencePayload:
    """Frozen evidence from one published intent package version.

    The engine never reads draft content: the port resolves the requested
    version, or the package's latest published version when no pin is given.
    """

    package_id: uuid.UUID
    version_id: uuid.UUID
    version_name: str
    categories: tuple[EvidenceCategory, ...] = ()

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(category.name for category in self.categories)

    def category(self, name: str) -> EvidenceCategory | None:
        for category in self.categories:
            if category.name == name:
                return category
        return None


@dataclass
class AtomicIntent:
    """L1 result: the per-turn classification."""

    label: str | None
    confidence: float
    source: DecisionSource
    gated: bool = False


@dataclass
class WorkflowIntent:
    """L2 result: multi-turn task detection."""

    label: str | None
    active: bool


class IntentRecognizer(ABC):
    """Runtime-internal extension point for intent recognition.

    Implementations turn one user turn plus session context into a layered
    :class:`IntentRecognitionResult`. The default implementation is
    :class:`hecate.runtime.intent.engine.IntentRecognitionEngine`.
    """

    @abstractmethod
    async def recognize(self, request: IntentRequest) -> IntentRecognitionResult:
        """Recognize one user turn."""


@dataclass
class IntentRequest:
    """Input to one recognition call.

    Attributes:
        utterance: The raw user turn text.
        evidence: Published evidence payload (may be ``None`` when the
            evidence port failed — the engine then degrades to LLM
            classification over ``fallback_labels``).
        session_intent: The persisted L3 state dict (see
            :data:`IntentRecognitionResult.session_intent_update` for the
            shape); ``{}`` for a fresh session.
        fallback_labels: Label space used when evidence is unavailable
            (the config-known category names, which need no evidence).
        recognition_model: Optional model override — the recognition model
            may differ from the workflow execution model.
    """

    utterance: str
    evidence: EvidencePayload | None
    session_intent: dict = field(default_factory=dict)
    fallback_labels: tuple[str, ...] = ()
    recognition_model: str | None = None


@dataclass
class IntentRecognitionResult:
    """The layered recognition output for one user turn.

    ``session_intent_update`` is the new L3 state the caller must persist:
    ``{"goal": str | None, "turn_labels": [str], "shift": bool}``.
    """

    atomic: AtomicIntent
    workflow: WorkflowIntent
    domain: str | None
    gated: bool
    evidence_ref: str | None
    evidence_available: bool
    cache_hit: bool
    latency_ms: float
    session_intent_update: dict
    utterance: str = ""
    goal_shift: bool = False


class IntentEvidencePort(ABC):
    """Cross-layer boundary port: published intent evidence into runtime.

    The engine consumes few-shot evidence exclusively from published
    intent package versions. Composition roots wire a provider backed by
    the studio package store; implementations resolve ``version_id=None``
    to the package's latest published version and raise when the reference
    is unresolvable so the engine can degrade gracefully.
    """

    @abstractmethod
    async def get_evidence(
        self,
        package_id: uuid.UUID,
        version_id: uuid.UUID | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> EvidencePayload:
        """Load published evidence for one package version.

        Args:
            package_id: The intent package to resolve.
            version_id: Optional published-version pin; ``None`` resolves
                the package's latest published version.
            workspace_id: Optional tenant scope — implementations SHOULD
                filter by it when provided.

        Raises:
            LookupError: If the package or (pinned) version does not exist
                or has no published version.
        """


__all__ = [
    "AtomicIntent",
    "DecisionSource",
    "EvidenceCategory",
    "EvidencePayload",
    "IntentEvidencePort",
    "IntentRecognitionResult",
    "IntentRecognizer",
    "IntentRequest",
    "WorkflowIntent",
]
