"""Platform control-command receipt model.

One row per control command (cancel/pause/resume/provide_input) recording
its independent receipt trail (contract ``ControlCommandRecord``): the
command id, kind, issuer, target task/run references, and the receipt
state. A transport-level success (HTTP 200) is only ever ``requested``;
``applied`` comes exclusively from an executor's actual-effect receipt.
Owned by the execution domain (single writer: the platform command
recorder adapter); other domains must not write this table.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class ControlCommandModel(BaseModel):
    """Durable receipt record for one control command (plan step6).

    Fields mirror the contract ``ControlCommandRecord``: ``command_id``
    (unique), ``kind`` (closed ``ControlCommandKind``), ``issuer``,
    target task ref (and optional run ref), ``issued_at``, receipt
    ``state`` (closed ``CommandState``; terminals absorb), optional
    ``expires_at`` / ``expected_revision``, and the payload with its
    schema reference (a non-empty payload always declares its schema).
    ``detail_ns`` carries backend-namespace detail (e.g. rejection
    reasons) without polluting the contract fields.
    """

    __tablename__ = "control_commands"
    __table_args__ = (
        Index("uq_control_commands_command_id", "command_id", unique=True),
        Index("ix_control_commands_task_ref", "task_issuer_domain", "task_ref_id"),
        Index("ix_control_commands_workspace_state", "workspace_id", "state"),
    )

    command_id: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    issuer: Mapped[str] = mapped_column(String(255), nullable=False)
    task_issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    task_ref_id: Mapped[str] = mapped_column(String(512), nullable=False)
    run_issuer_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    run_ref_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    issued_at: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload_schema_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_ns: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
