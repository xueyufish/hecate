"""AgentPrincipal ORM model - the governed identity behind an agent.

One row binds an existing :class:`AgentModel` to a responsible owner (a
resolvable organization + user pair) and a lifecycle state. The persona
string on ``agents`` keeps describing behaviour; it never carries governance
meaning. Registration and lifecycle transitions belong exclusively to the
execution-domain registry service; other domains must not write this table.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class PrincipalLifecycle(StrEnum):
    """Explicit lifecycle states for an agent principal."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class AgentPrincipalModel(BaseModel):
    """Governed identity entity for one agent (plan step4).

    Fields:

    - **agent_id** — the registered agent; unique among live principals so
      an agent has at most one principal at a time.
    - **workspace_id** — copied from the agent at registration; queries go
      through the registry service, never raw table access.
    - **organization_id** / **owner_user_id** — the resolvable responsibility
      subject; the registry validates both resolve to live rows (owner to an
      active user) before insert — user-organization membership lives at the
      workspace layer, so no direct org-membership FK exists here.
    - **lifecycle** — explicit state machine (``active``/``suspended``/
      ``revoked``); a revoked principal must not back new deployments.
    - **identity_provider** / **idp_subject** — IdP mapping (e.g. OIDC
      issuer URL and subject claim) for cross-system correlation; both
      nullable when no external identity exists yet.
    - **registered_by** — who created the mapping (event-level audit lives
      in ``audit_logs`` via the registry; this column keeps the durable
      actor on the row itself).
    """

    __tablename__ = "agent_principals"
    __table_args__ = (UniqueConstraint("agent_id", name="uq_agent_principals_agent_live"),)

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id"),
        nullable=False,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
    )
    lifecycle: Mapped[PrincipalLifecycle] = mapped_column(
        Enum(PrincipalLifecycle, name="principal_lifecycle"),
        nullable=False,
        default=PrincipalLifecycle.ACTIVE,
    )
    identity_provider: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idp_subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    registered_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("extra_metadata", JSON, nullable=False, default=dict)
