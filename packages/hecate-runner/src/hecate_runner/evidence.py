"""Local append-only evidence store with an explicit retention policy.

Two backends share one surface (see ``EVIDENCE_IMPLEMENTATIONS``): the
default day-rolled JSONL file store and a SQL store that delegates audit
retention to the host's own local durable database. Both enforce the same
policy semantics — a retention age that deletes expired records, and a
capacity limit that first triggers cleanup and then refuses new protected
actions (fail-closed, SC06 local half) when still over. Units differ per
backend and are declared by the backend (files: bytes; SQL: rows).

Writing fails closed: if the evidence store cannot accept a record for a
new protected action, the action is refused — an execution without locally
retained evidence is not offered in the preview profile. Gate failures
carry an explicit category (unwritable vs over-capacity) so operators can
tell a disk fault from a policy limit; uploads to any central target do
not exist (central export is a later step, step10).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_FAILED = "failed"

CATEGORY_UNWRITABLE = "unwritable"
CATEGORY_CAPACITY = "capacity"

GATE_KIND = "evidence_gate"
RETENTION_KIND = "evidence_retention"

# Host bookkeeping (gate probes/denials, cleanup traces) is auditable but
# must not feed the capacity meter: a cleanup trace would otherwise keep
# the store over its limit forever and the gate could never reopen. The
# records still age out through retention like any other kind.
BOOKKEEPING_KINDS = frozenset({GATE_KIND, RETENTION_KIND})

SECONDS_PER_DAY = 86400


class EvidenceCapacityError(OSError):
    """The store is over its configured capacity even after retention cleanup."""

    category = CATEGORY_CAPACITY

    def __init__(self, message: str, *, usage: int, limit: int, unit: str) -> None:
        super().__init__(message)
        self.usage = usage
        self.limit = limit
        self.unit = unit


@dataclass(frozen=True)
class EvidencePolicy:
    """Retention policy limits; ``None`` means the limit is not configured."""

    retention_days: int | None = None
    capacity_limit: int | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    ts: float
    kind: str  # "execution" | "denial" | gate/retention bookkeeping kinds
    principal: str
    ref: str
    outcome: str
    detail: dict


def evidence_backend(name: str) -> type:
    """Resolve a backend name to its store class (assembly dispatch).

    The durable import is lazy to keep the JSONL module free of the SQL
    dependency path; profile validation restricts names beforehand.
    """

    if name == "jsonl":
        return EvidenceStore
    if name == "durable":
        from .evidence_sql import SqlEvidenceStore

        return SqlEvidenceStore
    raise ValueError(f"unknown evidence backend: {name}")


class EvidenceStore:
    """Append-only JSONL evidence with read-side filtering and policy."""

    backend = "jsonl"
    unit = "bytes"

    def __init__(
        self,
        evidence_dir: Path,
        policy: EvidencePolicy | None = None,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._dir = evidence_dir
        self._policy = policy
        self._now = now or time.time
        self._lock = threading.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._last_gate_failure: dict | None = None

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
        """Read records newest-first, filtered; evidence stays local-only."""

        matches: list[EvidenceRecord] = []
        for path in sorted(self._dir.glob("evidence-*.jsonl"), reverse=True):
            with self._lock:
                lines = path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                if not line.strip():
                    continue
                record = json.loads(line)
                if outcome is not None and record["outcome"] != outcome:
                    continue
                if principal is not None and record["principal"] != principal:
                    continue
                if kind is not None and record["kind"] != kind:
                    continue
                matches.append(
                    EvidenceRecord(
                        ts=record["ts"],
                        kind=record["kind"],
                        principal=record["principal"],
                        ref=record["ref"],
                        outcome=record["outcome"],
                        detail=record.get("detail", {}),
                    )
                )
                if len(matches) >= limit:
                    return matches
        return matches

    def probe(self) -> None:
        """Verify the store accepts writes by appending a real probe record.

        The probe itself is auditable evidence (kind ``evidence_gate``), not
        a synthetic health flag. Capacity is checked first: over the limit
        the store runs retention cleanup once and re-checks; still over, the
        probe raises :class:`EvidenceCapacityError` and the caller must stop
        new protected actions (SC06 local half). Other failures raise
        ``OSError`` with the ``unwritable`` category.
        """

        self._enforce_capacity()
        self.append(GATE_KIND, "host", "probe", OUTCOME_OK, {"check": "writable"})

    def usage(self) -> int:
        """Current evidence usage in the backend's declared unit.

        Bookkeeping kinds are excluded (see ``BOOKKEEPING_KINDS``); lines
        that cannot be parsed still count, so a torn tail cannot hide use.
        """

        total = 0
        for path in self._dir.glob("evidence-*.jsonl"):
            with self._lock:
                lines = path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    kind = json.loads(line).get("kind")
                except ValueError:
                    kind = None
                if kind in BOOKKEEPING_KINDS:
                    continue
                total += len(line.encode("utf-8")) + 1
        return total

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
        """Record an evidence-gate failure explicitly.

        The durable trail is best-effort (the store itself just failed);
        the in-memory last-failure state stays authoritative for health
        reporting, so the category is never silently lost.
        """

        self._last_gate_failure = {"category": category, "detail": detail[:200], "ts": self._now()}
        try:
            self.append(GATE_KIND, "host", "gate", OUTCOME_DENIED, {"check": "writable", "category": category})
        except OSError:
            logger.warning("evidence gate failure could not be appended: %s", detail[:200])

    # -- policy enforcement ----------------------------------------------------

    def _enforce_retention(self) -> None:
        if self._policy is None or self._policy.retention_days is None:
            return
        cutoff = self._now() - self._policy.retention_days * SECONDS_PER_DAY
        deleted = 0
        for path in sorted(self._dir.glob("evidence-*.jsonl")):
            day = self._file_day(path)
            if day is None or day + SECONDS_PER_DAY > cutoff:
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError:
                # Cleanup failure must not block evidence writes; capacity
                # re-checking still fails closed if the limit stays exceeded.
                logger.warning("evidence retention could not delete %s", path.name)
        if deleted:
            self._write(RETENTION_KIND, "host", "cleanup", OUTCOME_OK, {"deleted_files": deleted})

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

    # -- storage primitives ------------------------------------------------------

    def _write(self, kind: str, principal: str, ref: str, outcome: str, detail: dict | None = None) -> None:
        """Append one record without policy enforcement (cleanup tracing uses this)."""

        record = {
            "ts": self._now(),
            "kind": kind,
            "principal": principal,
            "ref": ref,
            "outcome": outcome,
            "detail": detail or {},
        }
        path = self._day_file(record["ts"])
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _day_file(self, ts: float) -> Path:
        day = time.strftime("%Y%m%d", time.gmtime(ts))
        return self._dir / f"evidence-{day}.jsonl"

    @staticmethod
    def _file_day(path: Path) -> float | None:
        try:
            day = datetime.strptime(path.stem.removeprefix("evidence-"), "%Y%m%d").replace(tzinfo=UTC)
        except ValueError:
            return None
        return day.timestamp()
