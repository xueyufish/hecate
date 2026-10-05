"""Workspace trust root ORM model - managed runner enrollment (step6/7).

One row is a workspace-scoped, named trust material registration: the
named reference an enrollment's ``trust_root_ref`` /
``host_identity_ref`` must resolve to, the HMAC signing material for that
host's deployment-domain credentials, and revocation state. Admission
(``TaskRunRegistry.set_managed_opt_in``) requires the enrollment's named
references to resolve through :class:`hecate.execution.trust_roots.TrustRootRegistry`
to a non-revoked row here — an unresolvable or revoked root blocks
managed opt-in and later re-verification.
"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from hecate.models.base import BaseModel


class TrustRootModel(BaseModel):
    """One workspace-scoped named trust material (plan step6/7 managed slice).

    Fields:

    - **name** — the named reference enrollments cite (unique per
      workspace); resolution matches by exact name.
    - **material_digest** — SHA-256 over the registered HMAC signing
      material; the raw secret is never stored server-side (the operator
      provisions it to the host out of band, mirroring the plan's
      "凭据只以引用传递" rule).
    - **issuer_domain** — the deployment-domain issuer this root signs for
      (the ``iss`` a host's credentials and leases must present).
    - **revoked** — revocation stops resolution immediately; reconnecting
      hosts fail the re-verification chain and stop receiving new work.
    - **config_fingerprint** — the expected-configuration digest checked at
      reconnect; a mismatch blocks new deliveries until an operator
      refreshes it (the plan's 期望配置指纹).
    - **meta** — free-form registration metadata (provisioned by, notes);
      never interpreted for authorization.
    """

    __tablename__ = "trust_roots"
    __table_args__ = (Index("uq_trust_roots_workspace_name", "workspace_id", "name", unique=True),)

    name: Mapped[str] = mapped_column(String(256), nullable=False)
    material_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer_domain: Mapped[str] = mapped_column(String(256), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False, index=True)
