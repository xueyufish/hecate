"""Tests for the composition-root intent evidence provider (6.23 ⊕ 6.49)."""

from __future__ import annotations

import uuid

import pytest

from hecate.core.composition.intent_evidence import StudioIntentEvidenceProvider
from hecate.models.intent_package import IntentCategoryCreateSchema, IntentPackageVersionCreateSchema
from hecate.studio.intent_packages.service import IntentPackageService

WS = uuid.UUID("00000000-0000-0000-0000-000000000010")


async def _seed(db_session, gated: bool = False):
    service = IntentPackageService(db_session)
    package = await service.create_package(name="pkg", description=None, workspace_id=WS)
    category = await service.add_category(
        package.id,
        WS,
        IntentCategoryCreateSchema(
            name="billing",
            description="Billing questions",
            domain="finance",
            policy_gated=gated,
        ),
    )
    await service.add_sample(package.id, category.id, WS, "refund my order")
    await service.add_sample(package.id, category.id, WS, "invoice copy")
    await service.add_category(package.id, WS, IntentCategoryCreateSchema(name="tech"))
    version = await service.create_version(package.id, WS, IntentPackageVersionCreateSchema(name="v1"), created_by=None)
    return service, package, version


async def test_provider_loads_published_evidence(db_session, monkeypatch):
    from hecate.core import database
    from tests.conftest import test_session_factory

    service, package, version = await _seed(db_session)
    await service.publish_version(package.id, version.id, WS, None, linked_result=None)
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

    service, package, _version = await _seed(db_session)  # never published
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    provider = StudioIntentEvidenceProvider()
    with pytest.raises(LookupError):
        await provider.get_evidence(package.id, workspace_id=WS)


async def test_provider_respects_workspace_scope(db_session, monkeypatch):
    from hecate.core import database
    from tests.conftest import test_session_factory

    service, package, version = await _seed(db_session)
    await service.publish_version(package.id, version.id, WS, None, linked_result=None)
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    provider = StudioIntentEvidenceProvider()
    other_ws = uuid.uuid4()
    with pytest.raises(LookupError):
        await provider.get_evidence(package.id, workspace_id=other_ws)
