"""Task Memory ORM models and Pydantic schemas — 4.21 + KM6 (ADR-024 §6).

Defines the persistence layer for the self-improving work memory stack:

- **EpisodeModel** — one row per task-level execution. The episode record
  collects tool calls (``actions``) and their results (``outcomes``) plus
  ``situation`` / ``intent`` for the LLM-facing reflection prompt. The
  ``closed_at`` timestamp is the gate into the reflection candidate pool.
- **ReflectionModel** — typed reflection record produced by the
  ReflectionEngine from one or more closed episodes. Status flow:
  ``pending → approved | rejected | deprecated``. ``hints`` is hard-capped
  at ≤ 300 words (enforced at the service layer). ``isrel / issup / isuse``
  are the three Self-RAG-style quality tokens produced by the LLM-as-Judge
  gate; an entry only becomes consumable when all three clear 0.5.
- **ReflectionRunModel** — parallel audit table to ``consolidation_runs``;
  one row per reflection run on a single integration unit. Carries the
  candidate episode set, confidence distribution, and per-rejection-reason
  counters so the reflection layer can be monitored like consolidation.
- **WorkContextNodeModel** — KM6 node storage. Five node types
  (``method / outcome / correction / source / pattern``); aggregates of
  approved reflections over time. ``success_rate`` /
  ``usage_count`` / ``user_correction_count`` are populated by a background
  aggregation job, not on the read path.
- **WorkContextEdgeModel** — KM6 edge storage. Four edge types
  (``tried_before / led_to / corrected_by / validated_by``); weight is a
  confidence scalar the same background job recomputes.

All models carry the standard ``BaseModel`` columns (``id``,
``created_at``, ``updated_at``, ``deleted``, ``deleted_at``) and a
``workspace_id`` first-class isolation column. The reflection namespace
matches the existing ``memory-consolidation`` isolation unit
``(workspace_id, agent_id, actor_id | null, team_id | null)``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel as PydanticBase
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel

_DEFAULT_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _uuid_to_str(obj: Any) -> Any:
    """Recursively convert UUIDs in ``obj`` to strings; pass-through otherwise."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, list):
        return [_uuid_to_str(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _uuid_to_str(v) for k, v in obj.items()}
    return obj


def _json_column() -> Any:
    """JSONB-compatible JSON column.

    On Postgres the variant is JSONB; on SQLite (test conftest) it is
    plain JSON. Application code is responsible for pre-coercing
    ``uuid.UUID`` and ``datetime`` values to strings before bind
    (see ``ReflectionEngine._apply`` and ``ConsolidationScheduler``).
    """
    return JSON().with_variant(JSONB(), "postgresql")


# NOTE: SQLAlchemy 2.x's JSON type does not accept a custom serializer
# (the ``json_serializer`` kwarg was removed). We rely on the
# application layer pre-coercing UUIDs before bind. ReflectionEngine
# already does this for ``source_episode_ids``; reflection-run writes
# carry an empty list (no UUID coercion).


# ───────────────────────────── Episode ──────────────────────────────


class EpisodeModel(BaseModel):
    """ORM model for a task-level execution episode (4.21).

    One row per user-goal-level task executed by an agent. Tool calls made
    during the task land in ``actions`` (typed ``TOOL`` events with
    ``args`` / ``result_ref`` / ``ts``); tool results land in ``outcomes``.
    The ``closed_at`` timestamp is set by ``episode_close(episode_id)`` and
    is the only gate into the reflection candidate pool.

    Key fields:

    - **workspace_id** — tenant scope (first-class isolation).
    - **agent_id** — owning agent.
    - **actor_id** — optional user/team-member scope; mirrors the
      ``memory-consolidation`` namespace so consolidation units can line up.
    - **session_id** — optional conversation/session the episode ran in.
    - **task_type** — semantic label used to match reflection ``use_cases``
      at retrieval time.
    - **closed_at** — non-null once the calling agent calls
      ``episode_close``; only closed episodes enter the reflection queue.
    """

    __tablename__ = "episodes"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    task_type: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    situation: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    intent: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    outcomes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=list,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    __table_args__ = (
        Index(
            "idx_episodes_unit_closed",
            "workspace_id",
            "agent_id",
            "actor_id",
            "closed_at",
        ),
        Index("idx_episodes_task_type", "task_type"),
    )


class EpisodeCreateSchema(PydanticBase):
    """Pydantic schema for creating an episode row.

    ``workspace_id`` / ``agent_id`` come from the auth context, not the
    request body; ``closed_at`` is set later by ``episode_close``.
    """

    actor_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    task_type: str = ""
    situation: str | None = None
    intent: str | None = None
    actions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []


class EpisodeUpdateSchema(PydanticBase):
    """Pydantic schema for partial episode update (append actions / outcomes, close)."""

    actions: list[dict[str, Any]] | None = None
    outcomes: list[dict[str, Any]] | None = None
    closed_at: datetime | None = None


class EpisodeReadSchema(PydanticBase):
    """Pydantic schema for episode read; matches ``BaseModel`` columns plus task memory fields."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    task_type: str = ""
    situation: str | None = None
    intent: str | None = None
    actions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# ───────────────────────────── Reflection ───────────────────────────


# Status flow: pending → approved | rejected | deprecated
REFLECTION_STATUS_PENDING = "pending"
REFLECTION_STATUS_APPROVED = "approved"
REFLECTION_STATUS_REJECTED = "rejected"
REFLECTION_STATUS_DEPRECATED = "deprecated"
REFLECTION_STATUSES: tuple[str, ...] = (
    REFLECTION_STATUS_PENDING,
    REFLECTION_STATUS_APPROVED,
    REFLECTION_STATUS_REJECTED,
    REFLECTION_STATUS_DEPRECATED,
)

REFLECTION_OPERATOR_ADD = "add"
REFLECTION_OPERATOR_UPDATE = "update"
REFLECTION_OPERATORS: tuple[str, ...] = (REFLECTION_OPERATOR_ADD, REFLECTION_OPERATOR_UPDATE)

# Gate thresholds. Service-layer enforcement; declared here so the schema
# docs and the engine share the same constants.
REFLECTION_HINTS_WORD_CAP = 300
REFLECTION_GATE_ISREL_MIN = 0.5
REFLECTION_GATE_ISSUP_MIN = 0.5
REFLECTION_GATE_ISUSE_MIN = 0.5
REFLECTION_GATE_SOURCE_EPISODE_MIN = 2
REFLECTION_DEPRECATION_CONFIDENCE = 0.4
REFLECTION_DEPRECATION_RETRIES = 3


class ReflectionModel(BaseModel):
    """ORM model for a typed reflection record produced by ReflectionEngine.

    Status flow:
    ``pending → approved`` (cleared all four gates and indexed in Work
    Context Graph) / ``pending → rejected`` (any gate refused; remains
    queryable for audit but not retrievable) /
    ``approved → deprecated`` (``confidence < 0.4`` three times in a row).

    The four gates (model isolation, LLM-as-Judge three-token score,
    ``source_episode_ids >= 2``, low-confidence auto-deprecation) are
    defined in the ``task-memory`` capability spec; this model only
    persists the resulting state.

    Key fields:

    - **title** — short stable label; same-title entries get
      ``operator='update'`` with monotonic ``version``.
    - **use_cases** — list of ``task_type`` tags the reflection applies
      to; drives the ``reflection_search(task_type=...)`` index lookup.
    - **hints** — ≤ 300 words; the LLM-facing lesson to apply next time.
    - **confidence / isrel / issup / isuse** — quality signals.
    - **source_episode_ids** — at least 2 episodes by gate; single-episode
      reflections are refused at the gate (gate 3).
    - **status** — see constants above.
    """

    __tablename__ = "reflections"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    session_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    use_cases: Mapped[list[str]] = mapped_column(
        _json_column(),
        nullable=False,
        default=list,
        server_default="[]",
    )
    hints: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    source_episode_ids: Mapped[list[uuid.UUID]] = mapped_column(
        _json_column(),
        nullable=False,
        default=list,
    )
    operator: Mapped[str] = mapped_column(String(20), nullable=False, default=REFLECTION_OPERATOR_ADD)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=REFLECTION_STATUS_PENDING,
    )
    isrel: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    issup: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    isuse: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    last_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    deprecation_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index(
            "idx_reflections_status_use_cases",
            "workspace_id",
            "status",
        ),
        Index("idx_reflections_agent_status", "workspace_id", "agent_id", "status"),
    )


class ReflectionCreateSchema(PydanticBase):
    """Pydantic schema for creating a reflection row.

    Production code path: never call this directly. The ReflectionEngine
    emits reflection candidates after gate 1 (model isolation); service
    layer applies gates 2-4 and either persists the row or marks it
    rejected in ``reflection_runs``.
    """

    agent_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    title: str
    use_cases: list[str] = []
    hints: str = ""
    confidence: float = 0.5
    source_episode_ids: list[uuid.UUID] = []
    operator: str = REFLECTION_OPERATOR_ADD
    isrel: float | None = None
    issup: float | None = None
    isuse: float | None = None


class ReflectionUpdateSchema(PydanticBase):
    """Pydantic schema for partial reflection update (status transitions, version bump)."""

    status: str | None = None
    superseded_by: uuid.UUID | None = None
    version: int | None = None
    confidence: float | None = None
    last_confirmed_at: datetime | None = None
    deprecation_streak: int | None = None


class ReflectionReadSchema(PydanticBase):
    """Pydantic schema for reflection read."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    title: str
    use_cases: list[str] = []
    hints: str = ""
    confidence: float = 0.5
    source_episode_ids: list[uuid.UUID] = []
    operator: str = REFLECTION_OPERATOR_ADD
    superseded_by: uuid.UUID | None = None
    version: int = 1
    status: str = REFLECTION_STATUS_PENDING
    isrel: float | None = None
    issup: float | None = None
    isuse: float | None = None
    last_confirmed_at: datetime | None = None
    deprecation_streak: int = 0
    created_at: datetime
    updated_at: datetime


# ─────────────────────────── Reflection Run ──────────────────────────

REFLECTION_RUN_TRIGGER_REFLECTION = "reflection_trigger"
REFLECTION_RUN_TRIGGER_SCHEDULE = "fixed_interval"
REFLECTION_RUN_TRIGGER_IDLE = "idle"
REFLECTION_RUN_TRIGGER_PRESSURE = "pressure_flag"

REFLECTION_RUN_STATUS_RUNNING = "running"
REFLECTION_RUN_STATUS_SUCCESS = "success"
REFLECTION_RUN_STATUS_PARTIAL = "partial"
REFLECTION_RUN_STATUS_FAILED = "failed"

REFLECTION_RUN_REJECTION_HINTS_TOO_LONG = "hints_too_long"
REFLECTION_RUN_REJECTION_SINGLE_EPISODE = "single_episode_hallucination"
REFLECTION_RUN_REJECTION_LOW_ISREL = "low_isrel_score"
REFLECTION_RUN_REJECTION_LOW_ISSUP = "low_issup_score"
REFLECTION_RUN_REJECTION_LOW_ISUSE = "low_isuse_score"
REFLECTION_RUN_REJECTION_INJECTION = "injection_detected"
REFLECTION_RUN_REJECTION_FORBIDDEN_TOOL = "forbidden_tool_in_write"
REFLECTION_RUN_REJECTION_GATE = "gate_refused"


class ReflectionRunModel(BaseModel):
    """ORM model for a single reflection run on one integration unit.

    Parallel to ``ConsolidationRunModel``; same status vocabulary
    (``running / success / partial / failed``). The watermark for the
    next reflection run is the latest ``success`` row's ``window_end`` —
    same shape as consolidation so the two schedulers share their
    advisory-lock plumbing.

    Key fields:

    - **trigger** — see ``REFLECTION_RUN_TRIGGER_*`` constants.
    - **candidate_episode_ids** — episodes considered for reflection.
    - **adopted_count / rejected_count** — outcome counters.
    - **confidence_distribution** — JSONB histogram for observability.
    - **rejection_reasons** — JSONB counts per rejection reason code.
    - **status** — run state machine.
    """

    __tablename__ = "reflection_runs"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    team_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    candidate_episode_ids: Mapped[list[uuid.UUID]] = mapped_column(
        _json_column(),
        nullable=False,
        default=list,
    )
    adopted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    confidence_distribution: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
        default=None,
    )
    rejection_reasons: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=True,
        default=None,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=REFLECTION_RUN_STATUS_RUNNING,
    )
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    __table_args__ = (
        Index(
            "idx_reflection_runs_unit",
            "workspace_id",
            "agent_id",
            "created_at",
        ),
        Index("idx_reflection_runs_status", "status"),
    )


class ReflectionRunCreateSchema(PydanticBase):
    """Pydantic schema for creating a reflection run row."""

    agent_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    trigger: str = REFLECTION_RUN_TRIGGER_REFLECTION
    window_start: datetime
    window_end: datetime
    candidate_episode_ids: list[uuid.UUID] = []


class ReflectionRunUpdateSchema(PydanticBase):
    """Pydantic schema for updating a reflection run (status transitions)."""

    adopted_count: int | None = None
    rejected_count: int | None = None
    confidence_distribution: dict[str, Any] | None = None
    rejection_reasons: dict[str, Any] | None = None
    status: str | None = None
    llm_calls: int | None = None
    error: str | None = None


class ReflectionRunReadSchema(PydanticBase):
    """Pydantic schema for reflection run read."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    actor_id: uuid.UUID | None = None
    team_id: uuid.UUID | None = None
    trigger: str
    window_start: datetime
    window_end: datetime
    candidate_episode_ids: list[uuid.UUID] = []
    adopted_count: int = 0
    rejected_count: int = 0
    confidence_distribution: dict[str, Any] | None = None
    rejection_reasons: dict[str, Any] | None = None
    status: str = REFLECTION_RUN_STATUS_RUNNING
    llm_calls: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime


# ───────────────────────── Work Context Node ─────────────────────────


WORK_CONTEXT_NODE_TYPE_METHOD = "method"
WORK_CONTEXT_NODE_TYPE_OUTCOME = "outcome"
WORK_CONTEXT_NODE_TYPE_CORRECTION = "correction"
WORK_CONTEXT_NODE_TYPE_SOURCE = "source"
WORK_CONTEXT_NODE_NODE_TYPE_PATTERN = "pattern"
WORK_CONTEXT_NODE_TYPES: tuple[str, ...] = (
    WORK_CONTEXT_NODE_TYPE_METHOD,
    WORK_CONTEXT_NODE_TYPE_OUTCOME,
    WORK_CONTEXT_NODE_TYPE_CORRECTION,
    WORK_CONTEXT_NODE_TYPE_SOURCE,
    WORK_CONTEXT_NODE_NODE_TYPE_PATTERN,
)


class WorkContextNodeModel(BaseModel):
    """ORM model for KM6 Work Context Graph nodes.

    Aggregates over approved reflections. ``success_rate``,
    ``usage_count``, ``user_correction_count`` are recomputed by a
    background aggregation job from the ``episodes`` / ``reflections``
    tables; they are **never** updated on the read path or by the
    ReflectionEngine's transactional path.

    Key fields:

    - **node_type** — one of ``method / outcome / correction / source /
      pattern`` (ADR-024 §6).
    - **content** — the LLM-facing summary of what this node encodes.
    - **active** — false once the linked reflection is superseded; the
      node remains queryable for audit but is excluded from
      ``work_context_query`` results.
    - **linked_reflection_id** — back-pointer to the originating
      ``reflections.id`` row.
    """

    __tablename__ = "work_context_nodes"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    user_correction_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_reliability: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    linked_reflection_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, default=None)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        Index(
            "idx_work_context_nodes_type_active",
            "workspace_id",
            "node_type",
            "active",
        ),
        Index("idx_work_context_nodes_agent", "workspace_id", "agent_id"),
    )


class WorkContextNodeCreateSchema(PydanticBase):
    """Pydantic schema for creating a work context graph node."""

    agent_id: uuid.UUID
    node_type: str
    content: str
    success_rate: float = 0.5
    usage_count: int = 0
    last_used_at: datetime | None = None
    user_correction_count: int = 0
    source_reliability: float | None = None
    linked_reflection_id: uuid.UUID | None = None
    active: bool = True


class WorkContextNodeUpdateSchema(PydanticBase):
    """Pydantic schema for partial update of a work context graph node."""

    content: str | None = None
    success_rate: float | None = None
    usage_count: int | None = None
    last_used_at: datetime | None = None
    user_correction_count: int | None = None
    source_reliability: float | None = None
    active: bool | None = None


class WorkContextNodeReadSchema(PydanticBase):
    """Pydantic schema for work context graph node read."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    agent_id: uuid.UUID
    node_type: str
    content: str
    success_rate: float = 0.5
    usage_count: int = 0
    last_used_at: datetime | None = None
    user_correction_count: int = 0
    source_reliability: float | None = None
    linked_reflection_id: uuid.UUID | None = None
    active: bool = True
    created_at: datetime
    updated_at: datetime


# ───────────────────────── Work Context Edge ─────────────────────────


WORK_CONTEXT_EDGE_TYPE_TRIED_BEFORE = "tried_before"
WORK_CONTEXT_EDGE_TYPE_LED_TO = "led_to"
WORK_CONTEXT_EDGE_TYPE_CORRECTED_BY = "corrected_by"
WORK_CONTEXT_EDGE_TYPE_VALIDATED_BY = "validated_by"
WORK_CONTEXT_EDGE_TYPES: tuple[str, ...] = (
    WORK_CONTEXT_EDGE_TYPE_TRIED_BEFORE,
    WORK_CONTEXT_EDGE_TYPE_LED_TO,
    WORK_CONTEXT_EDGE_TYPE_CORRECTED_BY,
    WORK_CONTEXT_EDGE_TYPE_VALIDATED_BY,
)


class WorkContextEdgeModel(BaseModel):
    """ORM model for KM6 Work Context Graph edges.

    Four edge types (ADR-024 §6). ``weight`` is a confidence scalar
    recomputed by the background aggregation job. Both endpoints must
    point at active nodes at insertion time; the service layer
    double-checks the referential invariant before committing.
    """

    __tablename__ = "work_context_edges"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        nullable=False,
        default=_DEFAULT_WORKSPACE,
    )
    source_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    target_node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    edge_type: Mapped[str] = mapped_column(String(30), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    __table_args__ = (
        Index(
            "idx_work_context_edges_source",
            "workspace_id",
            "source_node_id",
        ),
        Index(
            "idx_work_context_edges_target",
            "workspace_id",
            "target_node_id",
        ),
    )


class WorkContextEdgeCreateSchema(PydanticBase):
    """Pydantic schema for creating a work context graph edge."""

    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    edge_type: str
    weight: float = 0.5


class WorkContextEdgeReadSchema(PydanticBase):
    """Pydantic schema for work context graph edge read."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID
    edge_type: str
    weight: float = 0.5
    created_at: datetime
    updated_at: datetime
