"""Local append-only evidence store.

Evidence is written as day-rolled JSONL files under the profile's
``evidence_dir``; every execution and every denial is appended with a
timestamp, principal, reference, outcome class, and detail. Writing
fails closed: if the evidence store cannot accept a record for a new
protected action, the action is refused — an execution without locally
retained evidence is not offered in the preview profile. Uploads to any
central target do not exist; evidence stays local (central export is a
later step, step10).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_FAILED = "failed"


@dataclass(frozen=True)
class EvidenceRecord:
    ts: float
    kind: str  # "execution" | "denial"
    principal: str
    ref: str
    outcome: str
    detail: dict


class EvidenceStore:
    """Append-only JSONL evidence with read-side filtering."""

    def __init__(self, evidence_dir: Path) -> None:
        self._dir = evidence_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def append(self, kind: str, principal: str, ref: str, outcome: str, detail: dict | None = None) -> None:
        record = {
            "ts": time.time(),
            "kind": kind,
            "principal": principal,
            "ref": ref,
            "outcome": outcome,
            "detail": detail or {},
        }
        path = self._day_file(record["ts"])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

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
            for line in path.read_text(encoding="utf-8").splitlines():
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

    def _day_file(self, ts: float) -> Path:
        day = time.strftime("%Y%m%d", time.gmtime(ts))
        return self._dir / f"evidence-{day}.jsonl"
