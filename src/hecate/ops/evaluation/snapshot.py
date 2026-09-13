"""Shared dataset-snapshot serialization (7.3 / 7.3b / 7.3c).

Single implementation point for "freeze a dataset's items": the per-item
canonical entry shape, the content-field projection, and the sha256 content
hash. Two consumers share it so their hashes are always comparable:

- the offline task runner's ``dataset_snapshot`` (frozen at run start)
- named dataset versions (7.3b), whose ``content_hash`` must equal the hash
  the same items would produce in a run snapshot

Hash semantics (7.3c): the hash covers evaluation content only —
``query / expected_answer / context / tags / metadata``. Known-bad marker
fields ride on snapshot entries sparsely (only when set) and are excluded
from the hash, so marking never surfaces as ``dataset_drift``.
"""

from __future__ import annotations

import hashlib
import json

from hecate.models.evaluation import EvaluationItemModel

# Content fields the hash covers; everything else on a snapshot entry is
# metadata (known-bad markers, version identity) and must not affect it.
_CONTENT_FIELDS = ("query", "expected_answer", "context", "tags", "metadata")


def serialize_snapshot_item(item: EvaluationItemModel) -> dict:
    """Build the frozen snapshot entry for one live item.

    The entry shape is the run-snapshot format: explicit field list,
    sparse known-bad keys (present only when marked) so unmarked items
    serialize byte-identically to the pre-7.3c format.
    """
    entry: dict = {
        "id": str(item.id),
        "query": item.query,
        "expected_answer": item.expected_answer,
        "context": item.context or [],
        "tags": list(item.tags or []),
        "metadata": dict(item.metadata_ or {}),
    }
    if item.known_bad:
        entry["known_bad"] = True
        entry["known_bad_reason"] = item.known_bad_reason
        entry["known_bad_marked_by"] = str(item.known_bad_marked_by) if item.known_bad_marked_by else None
        entry["known_bad_marked_at"] = item.known_bad_marked_at.isoformat() if item.known_bad_marked_at else None
    return entry


def content_view(entry: dict) -> dict:
    """Project a snapshot entry onto its hash-relevant content fields."""
    return {k: entry.get(k) for k in _CONTENT_FIELDS}


def compute_content_hash(entries: list[dict]) -> str:
    """Canonical-JSON sha256 over the content projection of snapshot entries."""
    canonical = json.dumps(
        [content_view(entry) for entry in entries],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_snapshot(items: list[EvaluationItemModel]) -> tuple[list[dict], str]:
    """Freeze ORM items into (snapshot entries, content hash).

    Callers must order items deterministically before passing them (the
    runner uses ``created_at, id`` ascending) so equal item sets produce
    identical hashes regardless of read order.
    """
    snapshot = [serialize_snapshot_item(item) for item in items]
    return snapshot, compute_content_hash(snapshot)
