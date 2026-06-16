"""Audit service — facade for audit log operations.

Wraps :class:`AuditStore` and :class:`PolicyEngine` behind a single
service interface consumed by the API layer.
"""

from __future__ import annotations

import logging
from datetime import datetime

from hecate.models.audit import AuditLogQuerySchema, AuditLogReadSchema
from hecate.services.audit.policy import (
    BulkDeleteProtectionPolicy,
    OffHoursSensitiveOpsPolicy,
    PolicyEngine,
    UnusualIPDetectionPolicy,
)
from hecate.services.audit.store import AuditStore, DatabaseAuditStore

logger = logging.getLogger(__name__)


class AuditService:
    """Facade for audit log query, export, and archival operations.

    Args:
        store: Audit store implementation. Defaults to DatabaseAuditStore.
        policy_engine: Optional policy engine for security checks.
    """

    def __init__(
        self,
        store: AuditStore | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self._store = store or DatabaseAuditStore()
        self._policy_engine = policy_engine

    async def query(self, filters: AuditLogQuerySchema) -> tuple[list[AuditLogReadSchema], int]:
        """Query audit logs with pagination."""
        return await self._store.query(filters)

    async def export(self, fmt: str, filters: AuditLogQuerySchema) -> str | bytes:
        """Export audit logs as CSV or JSON."""
        return await self._store.export(fmt, filters)

    async def archive(self, before_date: datetime) -> int:
        """Archive audit logs older than the given date."""
        return await self._store.archive(before_date)

    @property
    def store(self) -> AuditStore:
        """Return the underlying audit store."""
        return self._store

    @property
    def policy_engine(self) -> PolicyEngine | None:
        """Return the policy engine, if configured."""
        return self._policy_engine


def create_default_audit_service() -> AuditService:
    """Create an AuditService with default policies registered."""
    engine = PolicyEngine()
    engine.register(BulkDeleteProtectionPolicy())
    engine.register(OffHoursSensitiveOpsPolicy())
    engine.register(UnusualIPDetectionPolicy())
    return AuditService(policy_engine=engine)
