"""Canonical-JSON content hashing shared by asset snapshot implementations.

Single primitive for "hash a frozen content projection": sort-keys canonical
JSON, sha256 over the UTF-8 encoding. Asset-specific modules (evaluation
dataset snapshots, intent package versions) project their entries onto
content fields first, then call this — hashes produced by different asset
types are therefore produced by the same algorithm and are directly
comparable within an asset family.
"""

from __future__ import annotations

import hashlib
import json


def canonical_content_hash(entries: list[dict]) -> str:
    """Canonical-JSON sha256 over the given (already projected) entries.

    Callers must project each entry onto its hash-relevant content fields
    and order the entry list deterministically before calling, so equal
    content sets produce identical hashes regardless of read order.
    """
    canonical = json.dumps(
        entries,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
