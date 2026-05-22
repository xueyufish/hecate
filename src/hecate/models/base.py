"""Abstract base ORM model providing UUID primary keys, timestamps, and soft delete.

Every concrete model (agent, session, message, etc.) inherits from
:class:`BaseModel` to gain a UUID ``id``, ``created_at`` / ``updated_at``
timestamps, and a ``deleted_at`` column for soft deletes.

The corresponding Pydantic schemas for API validation and serialization live
alongside each concrete model in its own module.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from hecate.core.database import Base

JSONType = JSON


class BaseModel(Base):
    """Abstract base model providing UUID primary key, timestamps, and soft delete.

    All concrete ORM models inherit from this class to gain:

    - **UUID primary key** — auto-generated via :func:`uuid.uuid4`.
    - **Timestamps** — ``created_at`` and ``updated_at`` are set by the
      database server via ``server_default=func.now()`` for consistency
      across application instances. ``updated_at`` is refreshed on every
      UPDATE via ``onupdate=func.now()``.
    - **Soft delete** — instead of issuing ``DELETE`` statements, rows are
      marked as deleted by setting ``deleted_at``. Queries apply a
      ``WHERE deleted_at IS NULL`` filter (enforced via partial indexes)
      so that "deleted" rows are excluded from results without being
      removed from the database.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
