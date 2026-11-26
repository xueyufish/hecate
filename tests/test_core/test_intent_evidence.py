"""Tests for the composition-root intent evidence provider (6.23 ⊕ 6.49).

The intent-package management service was retired in M1-T1; these tests
seed the read-only package tables directly, mirroring the row shape the
retired publish flow left behind (published snapshots and separate draft
rows). Existing published graphs keep resolving evidence through this
provider.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hecate.core.composition.intent_evidence import StudioIntentEvidenceProvider
from hecate.models.intent_package import (
    IntentPackageCategoryModel,
    IntentPackageModel,
    IntentPackageVersionModel,
)

WS = uuid.UUID("00000000-0000-0000-0000-000000000010")


async def _seed(db_session, *, published: bool, gated: bool = False):
    package = IntentPackageModel(name="pkg", workspace_id=WS)
    db_session.add(package)
    await db_session.flush()

    db_session.add(IntentPackageCategoryModel(package_id=package.id, name="unpublished-edit", position=0))

    version = IntentPackageVersionModel(
        package_id=package.id,
        name="v1",
        content={
            "categories": [
                {
                    "name": "billing",
                    "description": "Billing questions",
                    "domain": "finance",
                    "policy_gated": gated,
                    "samples": [{"utterance": "refund my order"}, {"utterance": "invoice copy"}],
                },
                {"name": "tech", "samples": []},
            ]
        },
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
    with pytest.raises(LookupError):
        await StudioIntentEvidenceProvider(other_ws).get_evidence(package.id)
    with pytest.raises(LookupError):
        await StudioIntentEvidenceProvider(other_ws).get_evidence(package.id, workspace_id=WS)
    with pytest.raises(LookupError):
        await provider.get_evidence(package.id)


async def test_explicit_pin_reads_its_own_published_snapshot(db_session, monkeypatch):
    from hecate.core import database
    from tests.conftest import test_session_factory

    package, first = await _seed(db_session, published=True, gated=True)
    assert first.published_at is not None
    latest = IntentPackageVersionModel(
        package_id=package.id,
        name="v2",
        content={"categories": [{"name": "support", "samples": [{"utterance": "connect me"}]}]},
        content_hash="1" * 64,
        workspace_id=WS,
        published_at=first.published_at + timedelta(seconds=1),
    )
    db_session.add(latest)
    # Each provider call uses its own session.
    await db_session.commit()
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    provider = StudioIntentEvidenceProvider(WS)
    pinned = await provider.get_evidence(package.id, first.id)
    assert pinned.version_id == first.id
    assert pinned.labels == ("billing", "tech")
    assert pinned.category("billing").policy_gated

    unpinned = await provider.get_evidence(package.id)
    assert unpinned.version_id == latest.id
    assert unpinned.labels == ("support",)
    assert unpinned.category("support").samples == ("connect me",)


async def test_saved_and_published_controller_workflow_resolves_scoped_evidence(db_session, monkeypatch):
    from hecate.core import database
    from hecate.models.workflow import WorkflowCreateSchema, WorkflowUpdateSchema
    from hecate.runtime.errors import GraphValidationError
    from hecate.runtime.eventstore import EventType, InMemoryEventStore
    from hecate.runtime.intent.cache import fingerprint
    from hecate.runtime.ports import StubRuntimePort
    from hecate.studio.workflows.execution_service import WorkflowExecutionService
    from hecate.studio.workflows.service import WorkflowService
    from tests.conftest import test_session_factory

    package, version = await _seed(db_session, published=True)
    graph = {
        "version": "1.0",
        "name": "published-controller",
        "state": {
            "messages": {"type": "topic", "default": []},
            "_route": {"type": "last_value", "default": ""},
            "_intent_state": {"type": "last_value", "default": None},
            "selected": {"type": "last_value", "default": ""},
        },
        "nodes": {
            "controller": {
                "type": "controller",
                "config": {
                    "intent_package": {"package_id": str(package.id), "version_id": str(version.id)},
                    "category_targets": {"billing": "billing_agent"},
                    "default_workflow": "fallback_agent",
                    "global_intent": {"enabled": False},
                },
            },
            "billing_agent": {"type": "variable-set", "config": {"variable_name": "selected", "value": "billing"}},
            "fallback_agent": {"type": "variable-set", "config": {"variable_name": "selected", "value": "fallback"}},
        },
        "edges": [{"source": "controller", "target": {"billing": "billing_agent", "default": "fallback_agent"}}],
        "entry": "controller",
    }
    service = WorkflowService(db_session)
    data = WorkflowCreateSchema(name="published-controller", graph_dsl=graph)
    with pytest.raises(GraphValidationError, match="no published version"):
        await service.create_workflow(data, workspace_id=uuid.uuid4())
    workflow = await service.create_workflow(data, workspace_id=WS)
    await service.update_workflow(workflow.id, WorkflowUpdateSchema(graph_dsl=graph))
    published = await service.publish_version(workflow.id, 2)
    assert published.published_version == 2
    await db_session.commit()
    monkeypatch.setattr(database, "async_session_factory", test_session_factory)

    store = InMemoryEventStore()
    executor = WorkflowExecutionService(port=StubRuntimePort(), db=db_session, event_store=store)
    session_id = uuid.uuid4()
    await executor.execute(
        agent_mode="workflow",
        workflow_id=workflow.id,
        session_id=session_id,
        messages=[{"role": "user", "content": "refund my order"}],
    )
    recognition = next(
        event for event in await store.get_events(session_id) if event.event_type == EventType.INTENT_RECOGNIZED
    )
    assert recognition.payload["evidence_available"] is True
    assert recognition.payload["evidence_ref"] == fingerprint(str(package.id), str(version.id))
