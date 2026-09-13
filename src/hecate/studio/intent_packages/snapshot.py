"""Intent package freeze serialization.

Single implementation point for "freeze a package's draft content" (6.49):
the frozen content payload shape, the content-field projection, and the
content hash. The hash uses the same canonical-JSON sha256 primitive as
evaluation dataset snapshots (:mod:`hecate.ops.evaluation.snapshot`) so
both asset families share one hashing algorithm.

Hash semantics mirror the evaluation snapshot: the hash covers recognition
content only — category definition (name, description, domain,
policy_gated) and sample utterances. Provenance is audit metadata and is
excluded, so provenance changes never surface as content drift.
"""

from __future__ import annotations

from hecate.core.canonical_hash import canonical_content_hash
from hecate.models.intent_package import (
    IntentPackageCategoryModel,
    IntentPackageSampleModel,
)


def serialize_package_content(
    categories: list[IntentPackageCategoryModel],
    samples: list[IntentPackageSampleModel],
) -> dict:
    """Build the frozen version content payload from draft rows.

    Callers must order both lists deterministically (``created_at, id``
    ascending, the same convention the dataset runner uses) so equal drafts
    produce identical payloads and hashes regardless of read order. Sample
    provenance is carried in the payload for audit trail but does not
    affect the content hash.
    """
    samples_by_category: dict[str, list[dict]] = {}
    for sample in samples:
        samples_by_category.setdefault(str(sample.category_id), []).append(
            {"utterance": sample.utterance, "provenance": dict(sample.provenance or {})}
        )
    return {
        "categories": [
            {
                "name": category.name,
                "description": category.description,
                "domain": category.domain,
                "policy_gated": bool(category.policy_gated),
                "samples": samples_by_category.get(str(category.id), []),
            }
            for category in categories
        ]
    }


def package_content_view(content: dict) -> list[dict]:
    """Project frozen content onto its hash-relevant fields.

    Provenance is dropped per-sample; category order and sample order are
    preserved (they are recognition-relevant ordering established by the
    freeze).
    """
    return [
        {
            "name": category.get("name"),
            "description": category.get("description"),
            "domain": category.get("domain"),
            "policy_gated": bool(category.get("policy_gated")),
            "samples": [{"utterance": sample.get("utterance")} for sample in category.get("samples", [])],
        }
        for category in content.get("categories", [])
    ]


def compute_package_content_hash(content: dict) -> str:
    """Canonical-JSON sha256 over the content projection of frozen content."""
    return canonical_content_hash(package_content_view(content))


def build_package_content(
    categories: list[IntentPackageCategoryModel],
    samples: list[IntentPackageSampleModel],
) -> tuple[dict, str]:
    """Freeze draft rows into (frozen content payload, content hash)."""
    content = serialize_package_content(categories, samples)
    return content, compute_package_content_hash(content)
