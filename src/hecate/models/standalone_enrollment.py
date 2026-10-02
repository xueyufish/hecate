"""Standalone deployment enrollment ORM model - a local host registering itself.

One row records a standalone execution host's enrollment with the platform
(plan step4 registration flow): the host identity and trust root it presents,
the versions/capabilities it has installed, and whether it has *explicitly*
opted in to receiving managed new runs. Opt-in defaults to false and only an
operator action with an admission result can flip it - a reconnecting host
never changes its own scheduling rights. The real enrollment/reconnect
protocol is the managed-runner work (step6/7); this table is the model face.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class AdmissionResult(StrEnum):
    """Outcome of an operator-reviewed enrollment transition."""

    PENDING = "pending"
    ADMITTED = "admitted"
    REJECTED = "rejected"


class StandaloneEnrollmentModel(BaseModel):
    """One standalone host's registration record (plan step4).

    Fields:

    - **host_identity_ref** — JSON reference to the presenting host identity
      (issuer domain + identity id); registration validates syntax only,
      admission requires trusted resolution.
    - **trust_root_ref** — JSON reference to the trust root the host
      validates against; missing references are rejected and named claims
      remain pending until trusted resolution succeeds.
    - **installed_versions** — JSON list of the deployment/contract versions
      the host reports installed, with capability summaries; recorded as
      reported-at-enrollment data, not a live capability probe.
    - **managed_new_runs** — explicit operator-controlled opt-in for
      receiving managed new runs; defaults false and is never flipped by
      network events.
    - **operator_id / admitted_at / admission** — who reviewed the
      enrollment, when, and with what outcome; pattern transitions carry
      these, reconnects do not.
    - **workspace_id** — owning workspace for isolation.
    """

    __tablename__ = "standalone_enrollments"
    __table_args__ = (
        CheckConstraint("NOT managed_new_runs OR admission = 'ADMITTED'", name="ck_enrollment_managed_admitted"),
    )

    host_identity_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    trust_root_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    installed_versions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    managed_new_runs: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    operator_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    admitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    admission: Mapped[AdmissionResult] = mapped_column(
        Enum(AdmissionResult, name="enrollment_admission_result"),
        nullable=False,
        default=AdmissionResult.PENDING,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
