"""Platform Task ORM model - the responsibility record for one business goal.

One row records what a business task asks for (goal, initiator identity-chain
reference, acceptance criteria, responsible party) and where it belongs
(workspace, issuing domain). It deliberately carries **no execution state**:
backend status, events, and checkpoints belong to the run rows or the
executing side (plan step4; the task workflow state machine - queued /
waiting_approval / ... - is step6 scope and must not be pre-empted here).
All writes go through the execution-domain task-run registry; other domains
must not write this table.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.models.base import BaseModel


class TaskModel(BaseModel):
    """Platform-side task responsibility record (plan step4).

    Fields:

    - **goal** — the business objective in the creator's words; free text on
      purpose, never parsed for authorization decisions.
    - **initiator_ref** — JSON reference to the initiator identity chain
      (``contracts/execution/identity.IdentityChain`` shape: human initiator,
      agent principal, workload identity, optional on-behalf-of delegation).
      Stored as a reference, not a snapshot: the run pins the frozen chain.
    - **acceptance** — acceptance criteria for the task outcome; the shape is
      owned by whoever creates the task, validated as a JSON object only.
    - **responsibility** — the accountable party (free-text classification:
      team, queue, or human owner reference); governance data, not execution.
    - **workspace_id** — owning workspace; every registry read is scoped by
      it so missing and foreign rows are indistinguishable.
    - **issuer_domain** — issuing domain that makes the row id resolvable as
      an execution-contract task ``BackendRef``.
    """

    __tablename__ = "tasks"

    goal: Mapped[str] = mapped_column(String(4096), nullable=False)
    initiator_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    acceptance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    responsibility: Mapped[str | None] = mapped_column(String(512), nullable=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    issuer_domain: Mapped[str] = mapped_column(String(255), nullable=False)
