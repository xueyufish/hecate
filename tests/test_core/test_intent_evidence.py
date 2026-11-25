"""Tests for the composition-root intent evidence provider (6.23 ⊕ 6.49).

The intent-package management service was retired in M1-T1; these tests
seed the read-only package tables directly, mirroring the row shape the
retired publish flow left behind (published version row plus draft
category and sample rows). Existing published graphs keep resolving
evidence through this provider.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from hecate.core.composition.intent_evidence import StudioIntentEvidenceProvider
from hecate.models.intent_package import (
    IntentPackageCategoryModel,
    IntentPackageModel,
    IntentPackageSampleModel,
    IntentPackageVersionModel,
)

WS = uuid.UUID("00000000-0000-0000-0000-000000000010")


async def _seed(db_session, *, published: bool, gated: bool = False):
    package = IntentPackageModel(name="pkg", workspace_id=WS)
    db_session.add(package)
    await db_session.flush()

    billing = IntentPackageCategoryModel(
        package_id=package.id,
        name="billing",
        description="Billing questions",
        domain="finance",
        policy_gated=gated,
        position=0,
    )
    tech = IntentPackageCategoryModel(package_id=package.id, name="tech", position=1)
    db_session.add_all([billing, tech])
    await db_session.flush()

    for position, utterance in enumerate(("refund my order", "invoice copy")):
        db_session.add(IntentPackageSampleModel(category_id=billing.id, utterance=utterance, position=position))
    await db_session.flush()

    version = IntentPackageVersionModel(
        package_id=package.id,
        name="v1",
        content={},
        content_hash="0" * 64,
        workspace_id=WS,
        published_at=datetime.now(UTC) if published else None,
    )
    db_session.add(version)
    await db_session.flush()
    return package, version


async def test_provider_loads_published_evidence(db_session, monkeypatch):
    from hecate.core import database
    from tests.conftest import test_session_factory

    package, version = await _seed(db_session, published=True)
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    provider = StudioIntentEvidenceProvider()
    payload = await provider.get_evidence(package.id, workspace_id=WS)

    assert payload.package_id == package.id
    assert payload.version_id == version.id
    assert payload.labels == ("billing", "tech")
    billing = payload.category("billing")
    assert billing.samples == ("refund my order", "invoice copy")
    assert billing.domain == "finance"


async def test_provider_rejects_unpublished_reference(db_session, monkeypatch):
    from hecate.core import database
    from tests.conftest import test_session_factory

    package, _version = await _seed(db_session, published=False)  # never published
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    provider = StudioIntentEvidenceProvider()
    with pytest.raises(LookupError):
        await provider.get_evidence(package.id, workspace_id=WS)


async def test_provider_respects_workspace_scope(db_session, monkeypatch):
    from hecate.core import database
    from tests.conftest import test_session_factory

    package, _version = await _seed(db_session, published=True)
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    provider = StudioIntentEvidenceProvider()
    other_ws = uuid.uuid4()
    with pytest.raises(LookupError):
        await provider.get_evidence(package.id, workspace_id=other_ws)
