"""Snapshot and content-hash semantics for intent package versions (6.49)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from hecate.core.canonical_hash import canonical_content_hash
from hecate.models.intent_package import (
    IntentPackageCategoryModel,
    IntentPackageSampleModel,
)
from hecate.ops.evaluation.snapshot import compute_content_hash
from hecate.studio.intent_packages.snapshot import (
    build_package_content,
    compute_package_content_hash,
    package_content_view,
    serialize_package_content,
)

NOW = datetime(2026, 9, 13, tzinfo=UTC)


def _category(package_id: uuid.UUID, name: str, **kw) -> IntentPackageCategoryModel:
    return IntentPackageCategoryModel(
        package_id=package_id,
        name=name,
        description=kw.get("description"),
        domain=kw.get("domain"),
        policy_gated=kw.get("policy_gated", False),
        created_at=kw.get("created_at", NOW),
        id=kw.get("id", uuid.uuid4()),
    )


def _sample(category_id: uuid.UUID, utterance: str, **kw) -> IntentPackageSampleModel:
    return IntentPackageSampleModel(
        category_id=category_id,
        utterance=utterance,
        provenance=kw.get("provenance", {}),
        created_at=kw.get("created_at", NOW),
        id=kw.get("id", uuid.uuid4()),
    )


def test_freeze_serializes_categories_and_samples():
    package_id = uuid.uuid4()
    billing = _category(package_id, "billing", description="Billing questions", domain="finance")
    tech = _category(package_id, "tech")
    samples = [
        _sample(billing.id, "refund my order"),
        _sample(tech.id, "app crashes on start"),
    ]

    content = serialize_package_content([billing, tech], samples)

    assert [c["name"] for c in content["categories"]] == ["billing", "tech"]
    assert content["categories"][0]["description"] == "Billing questions"
    assert content["categories"][0]["domain"] == "finance"
    assert content["categories"][0]["samples"] == [{"utterance": "refund my order", "provenance": {}}]
    assert content["categories"][1]["samples"] == [{"utterance": "app crashes on start", "provenance": {}}]


def test_provenance_excluded_from_content_hash():
    package_id = uuid.uuid4()
    cat = _category(package_id, "billing")
    sample_a = _sample(cat.id, "refund my order", provenance={"source_type": "manual"})
    sample_b = _sample(cat.id, "refund my order", provenance={"source_type": "correction", "reason": "misroute"})

    _, hash_a = build_package_content([cat], [sample_a])
    _, hash_b = build_package_content([cat], [sample_b])

    assert hash_a == hash_b


def test_content_change_moves_hash():
    package_id = uuid.uuid4()
    cat = _category(package_id, "billing")
    sample = _sample(cat.id, "refund my order")

    content_v1, hash_v1 = build_package_content([cat], [sample])
    mutated = {
        "categories": [
            dict(
                content_v1["categories"][0],
                samples=[
                    {"utterance": "refund my order", "provenance": {}},
                    {"utterance": "where is my invoice", "provenance": {}},
                ],
            )
        ]
    }

    assert compute_package_content_hash(mutated) != hash_v1


def test_hash_ignores_draft_row_identity():
    """Equal content frozen from different row ids produces the same hash."""
    package_id = uuid.uuid4()
    cat_a = _category(package_id, "billing", id=uuid.uuid4())
    cat_b = _category(package_id, "billing", id=uuid.uuid4())
    sample_a = _sample(cat_a.id, "refund my order", id=uuid.uuid4())
    sample_b = _sample(cat_b.id, "refund my order", id=uuid.uuid4())

    _, hash_a = build_package_content([cat_a], [sample_a])
    _, hash_b = build_package_content([cat_b], [sample_b])

    assert hash_a == hash_b


def test_same_primitive_as_evaluation_dataset_snapshots():
    """Both asset families hash through the shared canonical primitive."""
    package_id = uuid.uuid4()
    cat = _category(package_id, "billing", description="Billing")
    sample = _sample(cat.id, "refund my order")
    content, package_hash = build_package_content([cat], [sample])

    # Same algorithm, applied to the same projected entries.
    assert package_hash == canonical_content_hash(package_content_view(content))

    # The evaluation snapshot hash is also the shared primitive over its
    # content projection (delegated implementation, not a re-implementation).
    eval_entries = [{"query": "q", "expected_answer": "a", "context": [], "tags": [], "metadata": {}}]
    assert compute_content_hash(eval_entries) == canonical_content_hash(
        [{"query": "q", "expected_answer": "a", "context": [], "tags": [], "metadata": {}}]
    )
