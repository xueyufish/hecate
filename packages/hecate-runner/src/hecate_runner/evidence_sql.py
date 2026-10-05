"""SQL evidence store: audit retention delegated to the host's local database.

Same surface and policy semantics as the JSONL store (see
:mod:`hecate_runner.evidence`); the declared unit is rows. The table lives
in the host's own database — in the durable profile the engine is shared
with the persistent task store — and is created via :meth:`create_schema`
alongside the durable tables. This is a host-local storage delegation,
never a platform-table write and never an upload path.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import (
    Column,
    Engine,
    Float,
    Integer,
    MetaData,
    Table,
    Text,
    delete,
    desc,
    func,
    insert,
    select,
)
from sqlalchemy.exc import SQLAlchemyError

from .evidence import (
    BOOKKEEPING_KINDS,
    GATE_KIND,
    OUTCOME_DENIED,
    OUTCOME_OK,
    RETENTION_KIND,
    SECONDS_PER_DAY,
    EvidenceCapacityError,
    EvidencePolicy,
    EvidenceRecord,
)

logger = logging.getLogger(__name__)

_METADATA = MetaData()
_EVIDENCE_RECORDS = Table(
    "evidence_records",
    _METADATA,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", Float, nullable=False, index=True),
    Column("kind", Text, nullable=False),
    Column("principal", Text, nullable=False),
    Column("ref", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("detail", Text, nullable=False),
)


class SqlEvidenceStore:
    """Evidence store over the host's local SQL database (unit: rows)."""

    backend = "durable"
    unit = "records"

    def __init__(
        self,
        database: Engine | str,
        policy: EvidencePolicy | None = None,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        if isinstance(database, Engine):
            self._engine = database
            self._owns_engine = False
        else:
            from sqlalchemy import create_engine

            self._engine = create_engine(database, future=True)
            self._owns_engine = True
        self._policy = policy
        self._now = now or time.time
        self._lock = threading.Lock()
        self._last_gate_failure: dict | None = None

    def create_schema(self) -> None:
        """Create the evidence table (host-local startup path)."""

        _METADATA.create_all(self._engine)

    def append(self, kind: str, principal: str, ref: str, outcome: str, detail: dict | None = None) -> None:
        self._write(kind, principal, ref, outcome, detail)
        self._enforce_retention()

    def query(
        self,
        outcome: str | None = None,
        principal: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        """Read records newest-first, filtered; evidence stays host-local."""

        statement = select(
            _EVIDENCE_RECORDS.c.ts,
            _EVIDENCE_RECORDS.c.kind,
            _EVIDENCE_RECORDS.c.principal,
            _EVIDENCE_RECORDS.c.ref,
            _EVIDENCE_RECORDS.c.outcome,
            _EVIDENCE_RECORDS.c.detail,
        ).order_by(desc(_EVIDENCE_RECORDS.c.ts), desc(_EVIDENCE_RECORDS.c.id))
        if outcome is not None:
            statement = statement.where(_EVIDENCE_RECORDS.c.outcome == outcome)
        if principal is not None:
            statement = statement.where(_EVIDENCE_RECORDS.c.principal == principal)
        if kind is not None:
            statement = statement.where(_EVIDENCE_RECORDS.c.kind == kind)
        statement = statement.limit(limit)
        with self._engine.connect() as connection:
            rows = connection.execute(statement).fetchall()
        return [
            EvidenceRecord(
                ts=row.ts,
                kind=row.kind,
                principal=row.principal,
                ref=row.ref,
                outcome=row.outcome,
                detail=json.loads(row.detail),
            )
            for row in rows
        ]

    def probe(self) -> None:
        """Verify the store accepts writes by appending a real probe record.

        Capacity (row count) is checked first: over the limit the store
        runs retention cleanup once and re-checks; still over, the probe
        raises :class:`EvidenceCapacityError` and the caller must stop new
        protected actions (SC06 local half).
        """

        try:
            self._enforce_capacity()
            self.append(GATE_KIND, "host", "probe", OUTCOME_OK, {"check": "writable"})
        except SQLAlchemyError as exc:
            raise OSError("local evidence database cannot accept writes") from exc

    def usage(self) -> int:
        """Current evidence usage in rows (bookkeeping kinds excluded)."""

        statement = select(func.count()).select_from(_EVIDENCE_RECORDS)
        if BOOKKEEPING_KINDS:
            statement = statement.where(_EVIDENCE_RECORDS.c.kind.not_in(BOOKKEEPING_KINDS))
        with self._engine.connect() as connection:
            return int(connection.execute(statement).scalar_one())

    def policy_summary(self) -> dict:
        """Live evidence policy report for health/readiness surfaces."""

        policy = self._policy or EvidencePolicy()
        summary: dict = {
            "evidence_backend": self.backend,
            "evidence_unit": self.unit,
            "evidence_retention_days": policy.retention_days,
            "evidence_capacity_limit": policy.capacity_limit,
            "evidence_usage": self.usage(),
        }
        if self._last_gate_failure is not None:
            summary["evidence_last_gate_failure"] = dict(self._last_gate_failure)
        return summary

    def record_gate_failure(self, category: str, detail: str) -> None:
        """Record an evidence-gate failure explicitly (best-effort trail)."""

        self._last_gate_failure = {"category": category, "detail": detail[:200], "ts": self._now()}
        try:
            self.append(GATE_KIND, "host", "gate", OUTCOME_DENIED, {"check": "writable", "category": category})
        except Exception:  # noqa: BLE001 — the store itself just failed
            logger.warning("evidence gate failure could not be appended: %s", detail[:200])

    def dispose(self) -> None:
        if self._owns_engine:
            self._engine.dispose()

    # -- policy enforcement ------------------------------------------------------

    def _enforce_retention(self) -> None:
        if self._policy is None or self._policy.retention_days is None:
            return
        cutoff = self._now() - self._policy.retention_days * SECONDS_PER_DAY
        with self._engine.begin() as connection:
            result = connection.execute(delete(_EVIDENCE_RECORDS).where(_EVIDENCE_RECORDS.c.ts < cutoff))
            deleted = result.rowcount
        if deleted:
            self._write(RETENTION_KIND, "host", "cleanup", OUTCOME_OK, {"deleted_records": int(deleted)})

    def _enforce_capacity(self) -> None:
        if self._policy is None or self._policy.capacity_limit is None:
            return
        if self.usage() <= self._policy.capacity_limit:
            return
        self._enforce_retention()
        used = self.usage()
        if used <= self._policy.capacity_limit:
            return
        raise EvidenceCapacityError(
            f"evidence over capacity after retention cleanup: {used} {self.unit} > {self._policy.capacity_limit}",
            usage=used,
            limit=self._policy.capacity_limit,
            unit=self.unit,
        )

    def _write(self, kind: str, principal: str, ref: str, outcome: str, detail: dict | None = None) -> None:
        """Insert one record without policy enforcement (cleanup tracing uses this)."""

        with self._lock, self._engine.begin() as connection:
            connection.execute(
                insert(_EVIDENCE_RECORDS).values(
                    ts=self._now(),
                    kind=kind,
                    principal=principal,
                    ref=ref,
                    outcome=outcome,
                    detail=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                )
            )
