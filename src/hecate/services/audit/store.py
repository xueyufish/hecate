"""Audit store abstraction and PostgreSQL implementation.

Provides:

- :class:`AuditEvent` — lightweight dataclass for in-transit audit data
- :class:`AuditStore` — abstract interface for audit persistence
- :class:`DatabaseAuditStore` — PostgreSQL-backed implementation
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Select, and_, func, select
from sqlalchemy import delete as sql_delete

from hecate.core.database import async_session_factory
from hecate.models.audit import AuditLogModel, AuditLogQuerySchema, AuditLogReadSchema

logger = logging.getLogger(__name__)


@dataclass
class AuditEvent:
    """Lightweight audit event for in-transit data.

    This is the payload that flows through the middleware → queue → writer
    pipeline before being persisted as an AuditLogModel.
    """

    org_id: uuid.UUID
    user_id: uuid.UUID
    action: str
    success: bool = True
    workspace_id: uuid.UUID | None = None
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    request_method: str | None = None
    request_path: str | None = None
    response_status: int | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict = field(default_factory=dict)
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        if self.timestamp is None:
            self.timestamp = datetime.now()


class AuditStore(ABC):
    """Abstract interface for audit log persistence."""

    @abstractmethod
    async def write(self, event: AuditEvent) -> None:
        """Persist a single audit event."""

    @abstractmethod
    async def query(self, filters: AuditLogQuerySchema) -> tuple[list[AuditLogReadSchema], int]:
        """Query audit logs with filters. Returns (items, total_count)."""

    @abstractmethod
    async def export(self, fmt: str, filters: AuditLogQuerySchema) -> str | bytes:
        """Export audit logs in the given format (csv or json)."""

    @abstractmethod
    async def archive(self, before_date: datetime) -> int:
        """Archive (delete) audit logs older than the given date. Returns count."""


def _build_query_with_filters(filters: AuditLogQuerySchema) -> Select:
    """Build a SELECT query with dynamic WHERE clauses from filter schema."""
    stmt = select(AuditLogModel)
    conditions = []

    if filters.org_id is not None:
        conditions.append(AuditLogModel.org_id == filters.org_id)
    if filters.workspace_id is not None:
        conditions.append(AuditLogModel.workspace_id == filters.workspace_id)
    if filters.user_id is not None:
        conditions.append(AuditLogModel.user_id == filters.user_id)
    if filters.action is not None:
        conditions.append(AuditLogModel.action == filters.action)
    if filters.resource_type is not None:
        conditions.append(AuditLogModel.resource_type == filters.resource_type)
    if filters.resource_id is not None:
        conditions.append(AuditLogModel.resource_id == filters.resource_id)
    if filters.success is not None:
        conditions.append(AuditLogModel.success == filters.success)
    if filters.start_time is not None:
        conditions.append(AuditLogModel.created_at >= filters.start_time)
    if filters.end_time is not None:
        conditions.append(AuditLogModel.created_at <= filters.end_time)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    return stmt


class DatabaseAuditStore(AuditStore):
    """PostgreSQL-backed audit store.

    Uses async SQLAlchemy sessions for all database operations.
    The ``write`` method creates a new session per write to avoid
    coupling to the request-scoped session lifecycle.
    """

    async def write(self, event: AuditEvent) -> None:
        """Persist a single audit event to the database."""
        async with async_session_factory() as session:
            model = AuditLogModel(
                org_id=event.org_id,
                workspace_id=event.workspace_id,
                user_id=event.user_id,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                request_method=event.request_method,
                request_path=event.request_path,
                response_status=event.response_status,
                ip_address=event.ip_address,
                user_agent=event.user_agent,
                success=event.success,
                error_code=event.error_code,
                error_message=event.error_message,
                metadata_=event.metadata,
            )
            session.add(model)
            await session.commit()

    async def query(self, filters: AuditLogQuerySchema) -> tuple[list[AuditLogReadSchema], int]:
        """Query audit logs with pagination."""
        async with async_session_factory() as session:
            stmt = _build_query_with_filters(filters)

            # Count total
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar_one()

            # Paginate
            offset = (filters.page - 1) * filters.page_size
            stmt = stmt.order_by(AuditLogModel.created_at.desc()).offset(offset).limit(filters.page_size)
            result = await session.execute(stmt)
            rows = result.scalars().all()

            items = [AuditLogReadSchema.model_validate(row) for row in rows]
            return items, total

    async def export(self, fmt: str, filters: AuditLogQuerySchema) -> str | bytes:
        """Export audit logs as CSV or JSON string."""
        async with async_session_factory() as session:
            stmt = _build_query_with_filters(filters).order_by(AuditLogModel.created_at.desc())
            result = await session.execute(stmt)
            rows = result.scalars().all()

            if fmt == "json":
                items = [AuditLogReadSchema.model_validate(row).model_dump(mode="json") for row in rows]
                return json.dumps(items, default=str, indent=2)

            if fmt == "csv":
                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "id",
                        "org_id",
                        "workspace_id",
                        "user_id",
                        "action",
                        "resource_type",
                        "resource_id",
                        "request_method",
                        "request_path",
                        "response_status",
                        "ip_address",
                        "user_agent",
                        "success",
                        "error_code",
                        "error_message",
                        "created_at",
                    ],
                )
                writer.writeheader()
                for row in rows:
                    schema = AuditLogReadSchema.model_validate(row)
                    writer.writerow(schema.model_dump(mode="json"))
                return output.getvalue()

            msg = f"Unsupported export format: {fmt!r}"
            raise ValueError(msg)

    async def archive(self, before_date: datetime) -> int:
        """Delete audit logs older than the given date.

        In production, this would be replaced by partition drop operations
        when using pg_partman for automatic monthly partitioning.
        """
        async with async_session_factory() as session:
            stmt = sql_delete(AuditLogModel).where(AuditLogModel.created_at < before_date)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount
