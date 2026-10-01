"""AgentDeployment ORM model - one AgentVersion bound to one execution backend.

Registration rows for the plan's deployment governance (step4): the same
immutable ``AgentVersionModel`` can be registered against several backends;
each row pins the backend type/version, access mode, contract version,
capability snapshot, and the issuing domain that makes its id resolvable as
an execution-contract deployment ``BackendRef``. Hosted-backend records carry
the dual-axis harness/environment configuration with an explicit
``unverified`` sentinel - absence of verifiable data never defaults to
private deployment or enforced governance. All writes go through the
execution-domain deployment registry; other domains must not write this
table. Implementation language is registration metadata only and never
feeds authorization or capability decisions.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class BackendType(StrEnum):
    """Registered execution backend families (plan step4/step8)."""

    BUILTIN = "builtin"
    SELF_HOSTED = "self_hosted"
    HOSTED = "hosted"


class AccessMode(StrEnum):
    """How the platform reaches the backend (plan step4)."""

    IN_PROCESS = "in_process"
    LOCAL_PROCESS = "local_process"
    REMOTE_SERVICE = "remote_service"


class AccessLevel(StrEnum):
    """Admission level granted to this deployment (plan step8 grading)."""

    UNVERIFIED = "unverified"
    COOPERATIVE = "cooperative"
    ENFORCED = "enforced"


class HealthState(StrEnum):
    """Last observed health; registry-written, never self-reported by requests."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNREACHABLE = "unreachable"


class AgentDeploymentModel(BaseModel):
    """One registered binding of an AgentVersion to a backend.

    Key fields:

    - **agent_id / agent_version_id** — the owning agent and the immutable
      version snapshot this deployment serves; version rows are never
      mutated, deployments reference them.
    - **issuer_domain** — issuing domain for this registration; together
      with the row id it forms the contract ``deployment`` BackendRef.
      Unique per (domain, backend_type, agent_version) so a backfill replay
      cannot produce a second builtin deployment.
    - **access_mode / transport_contract_version** — how the platform talks
      to the backend and which wire contract it speaks.
    - **capability_snapshot** — JSON copy of the declared
      ``BackendCapabilities`` at registration time (axes, levels,
      verification entries); the registry validates the shape on write.
    - **axes columns** — denormalized harness/environment/tool-execution
      enum strings for query filtering; must stay consistent with the
      snapshot (registry enforces on write).
    - **access_level** — admission grading; ``enforced`` only after
      platform-verified evidence exists, never inferred from config.
    - **hosted_config** — hosted-backend dual-axis configuration: harness /
      environment providers, service region, residency and retention
      conditions, vendor internal-tool scope, enterprise gateway path,
      source + verification time. Missing verifiable values stay
      ``{"verification": "unverified"}``.
    - **is_default** — at most one default deployment per agent (unique
      partial-style index enforced via the registry + unique constraint on
      the backfill-relevant subset).
    - **endpoint** / **config_ref** / **credential_ref** — where to reach
      the backend and indirection handles for configuration and secrets;
      never inline secrets.
    """

    __tablename__ = "agent_deployments"
    __table_args__ = (
        # One builtin deployment per (agent, version): makes the step4
        # backfill idempotent. Self-hosted/hosted rows of the same version
        # are legitimately distinct (different endpoints/regions), so their
        # dedup policy lives in the registry, not in a blanket constraint.
        Index(
            "uq_agent_deployments_builtin_version",
            "agent_id",
            "agent_version_id",
            unique=True,
            sqlite_where=text("backend_type = 'BUILTIN'"),
            postgresql_where=text("backend_type = 'BUILTIN'"),
        ),
        Index("ix_agent_deployments_agent_id", "agent_id"),
        Index("ix_agent_deployments_version_id", "agent_version_id"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    backend_type: Mapped[BackendType] = mapped_column(
        Enum(BackendType, name="deployment_backend_type"),
        nullable=False,
    )
    backend_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    access_mode: Mapped[AccessMode] = mapped_column(
        Enum(AccessMode, name="deployment_access_mode"),
        nullable=False,
    )
    transport_contract_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    capability_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    axes_harness: Mapped[str] = mapped_column(String(32), nullable=False)
    axes_environment: Mapped[str] = mapped_column(String(32), nullable=False)
    axes_tool_execution: Mapped[str] = mapped_column(String(32), nullable=False)
    access_level: Mapped[AccessLevel] = mapped_column(
        Enum(AccessLevel, name="deployment_access_level"),
        nullable=False,
        default=AccessLevel.UNVERIFIED,
    )
    health: Mapped[HealthState] = mapped_column(
        Enum(HealthState, name="deployment_health_state"),
        nullable=False,
        default=HealthState.UNKNOWN,
    )
    implementation_language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hosted_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    registered_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
