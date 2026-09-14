"""Intent package service tests (6.49): CRUD, import/export, versions, gate, corrections."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.audit import AuditLogModel
from hecate.models.intent_package import (
    GateMode,
    IntentCorrectionCreateSchema,
    IntentPackageCategoryModel,
    IntentPackageModel,
)
from hecate.studio.intent_packages.publish_gate import evaluate_gate, resolve_gate_config
from hecate.studio.intent_packages.service import (
    CategoryNotFoundError,
    ImportValidationError,
    IntentPackageService,
    IntentPublishBlockedError,
    PackageNameConflictError,
    PackageNotFoundError,
    VersionNameConflictError,
)

WS_A = uuid.UUID("00000000-0000-0000-0000-000000000010")
WS_B = uuid.UUID("00000000-0000-0000-0000-000000000020")


async def _make_package(db: AsyncSession, workspace_id: uuid.UUID = WS_A, name: str = "pkg") -> IntentPackageModel:
    service = IntentPackageService(db)
    return await service.create_package(name=name, description=None, workspace_id=workspace_id)


async def _add_category_with_samples(
    db: AsyncSession,
    service: IntentPackageService,
    package_id: uuid.UUID,
    name: str,
    utterances: list[str],
    **category_kw,
) -> IntentPackageCategoryModel:
    from hecate.models.intent_package import IntentCategoryCreateSchema

    category = await service.add_category(package_id, WS_A, IntentCategoryCreateSchema(name=name, **category_kw))
    for utterance in utterances:
        await service.add_sample(package_id, category.id, WS_A, utterance)
    return category


# ---------------------------------------------------------------------------
# CRUD & workspace isolation
# ---------------------------------------------------------------------------


async def test_create_and_get_package(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await service.create_package(name="billing-intents", description="d", workspace_id=WS_A)
    fetched = await service.get_package(package.id, WS_A)
    assert fetched.name == "billing-intents"


async def test_duplicate_name_conflict(db_session: AsyncSession):
    await _make_package(db_session, name="pkg")
    with pytest.raises(PackageNameConflictError):
        await _make_package(db_session, name="pkg")


async def test_name_reusable_after_delete(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session, name="pkg")
    await service.delete_package(package.id, WS_A)
    recreated = await service.create_package(name="pkg", description=None, workspace_id=WS_A)
    assert recreated.name == "pkg"


async def test_cross_workspace_access_denied(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session, workspace_id=WS_A)
    with pytest.raises(PackageNotFoundError):
        await service.get_package(package.id, WS_B)
    with pytest.raises(PackageNotFoundError):
        await service.update_package(package.id, WS_B, name="hijack")


async def test_delete_package_soft_deletes_draft(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "billing", ["refund please"])
    await service.delete_package(package.id, WS_A)
    assert package.deleted is True
    rows = await db_session.execute(
        select(IntentPackageCategoryModel).where(IntentPackageCategoryModel.package_id == package.id)
    )
    assert all(category.deleted for category in rows.scalars())


# ---------------------------------------------------------------------------
# Bulk import / export
# ---------------------------------------------------------------------------


JSON_PAYLOAD = json.dumps(
    {
        "categories": [
            {"name": "billing", "description": "Billing", "samples": ["refund my order", "invoice copy"]},
            {"name": "tech", "samples": ["app crashes"]},
        ]
    }
)


async def test_json_import_merge(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    report = await service.import_content(package.id, WS_A, "json", JSON_PAYLOAD, mode="merge")
    assert report.categories_created == 2
    assert report.samples_created == 3
    names = [category.name for category in await service.list_categories(package.id, WS_A)]
    assert names == ["billing", "tech"]


async def test_import_all_or_nothing_on_invalid_rows(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    bad_payload = json.dumps({"categories": [{"name": "good", "samples": ["ok"]}, {"name": "", "samples": ["x"]}]})
    with pytest.raises(ImportValidationError) as excinfo:
        await service.import_content(package.id, WS_A, "json", bad_payload, mode="merge")
    assert any("name is required" in error["error"] for error in excinfo.value.errors)
    names = [category.name for category in await service.list_categories(package.id, WS_A)]
    assert names == []  # no partial write


async def test_csv_import_and_export_round_trip(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    csv_payload = (
        "category,category_description,domain,policy_gated,utterance\n"
        "billing,Billing stuff,finance,true,refund my order\n"
        "billing,Billing stuff,finance,true,invoice copy\n"
    )
    report = await service.import_content(package.id, WS_A, "csv", csv_payload, mode="merge")
    assert report.categories_created == 1
    assert report.samples_created == 2

    exported = await service.export_content(package.id, WS_A, "csv")
    other = await _make_package(db_session, name="other")
    report2 = await service.import_content(other.id, WS_A, "csv", exported, mode="merge")
    assert report2.samples_created == 2
    categories = await service.list_categories(other.id, WS_A)
    assert categories[0].policy_gated is True
    assert categories[0].domain == "finance"


async def test_import_replace_mode_clears_draft(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "old", ["old utterance"])
    await service.import_content(package.id, WS_A, "json", JSON_PAYLOAD, mode="replace")
    names = [category.name for category in await service.list_categories(package.id, WS_A)]
    assert names == ["billing", "tech"]


async def test_import_conflicting_category_definitions_rejected(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    bad = json.dumps(
        {
            "categories": [
                {"name": "billing", "description": "One", "samples": ["a"]},
                {"name": "billing", "description": "Two", "samples": ["b"]},
            ]
        }
    )
    with pytest.raises(ImportValidationError):
        await service.import_content(package.id, WS_A, "json", bad, mode="merge")


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


async def _freeze(db_session: AsyncSession, package_id: uuid.UUID, name: str = "v1"):
    from hecate.models.intent_package import IntentPackageVersionCreateSchema

    service = IntentPackageService(db_session)
    return await service.create_version(package_id, WS_A, IntentPackageVersionCreateSchema(name=name), created_by=None)


async def test_version_freeze_captures_draft_then_draft_diverges(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "billing", ["refund my order"])
    version = await _freeze(db_session, package.id)

    original_hash = version.content_hash
    await service.add_sample(package.id, (await service.list_categories(package.id, WS_A))[0].id, WS_A, "new one")

    assert version.content_hash == original_hash
    assert len(version.content["categories"][0]["samples"]) == 1
    refreshed = await service.get_version(package.id, version.id, WS_A)
    assert refreshed.content_hash == original_hash


async def test_version_name_reserved_across_soft_delete(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "billing", ["x"])
    version = await _freeze(db_session, package.id, "v1")
    await service.delete_version(package.id, version.id, WS_A)
    with pytest.raises(VersionNameConflictError):
        await _freeze(db_session, package.id, "v1")


async def test_empty_draft_cannot_freeze(db_session: AsyncSession):
    package = await _make_package(db_session)
    with pytest.raises(ValueError, match="no categories"):
        await _freeze(db_session, package.id)


# ---------------------------------------------------------------------------
# Publish gate
# ---------------------------------------------------------------------------


def test_gate_coverage_signal_blocks_on_shortfall():
    content = {"categories": [{"name": "a", "samples": [{"utterance": "x"}]}, {"name": "b", "samples": []}]}
    config = resolve_gate_config({"mode": "require", "min_samples_per_category": 5})
    result = evaluate_gate(config, content)
    assert result.blocking
    assert result.signals[0].detail["shortfall"] == {"a": 1, "b": 0}


def test_gate_requires_linked_run_for_pass_rate():
    content = {"categories": [{"name": "a", "samples": [{"utterance": "x"}]}]}
    config = resolve_gate_config({"mode": "require", "min_pass_rate": 0.8})
    result = evaluate_gate(config, content, linked_result=None)
    assert result.blocking
    assert result.signals[0].detail["reason"] == "no_linked_run"


def test_gate_pass_rate_signal_reads_deterministic_result():
    content = {"categories": [{"name": "a", "samples": [{"utterance": "x"}]}]}
    config = resolve_gate_config({"mode": "require", "min_pass_rate": 0.8})
    ok = evaluate_gate(config, content, linked_result={"run_id": "r", "pass_rate": 0.9, "per_category": {"a": 0.9}})
    assert not ok.blocking
    bad = evaluate_gate(config, content, linked_result={"run_id": "r", "pass_rate": 0.6, "per_category": {}})
    assert bad.blocking


def test_gate_warn_mode_never_blocks():
    content = {"categories": [{"name": "a", "samples": []}]}
    config = resolve_gate_config({"mode": "warn", "min_samples_per_category": 5, "min_pass_rate": 0.99})
    result = evaluate_gate(config, content, linked_result=None)
    assert not result.blocking
    assert all(not signal.passed for signal in result.signals)


def test_gate_require_mode_without_signals_rejected():
    with pytest.raises(ValueError, match="at least one enabled signal"):
        from hecate.studio.intent_packages.publish_gate import validate_gate_config

        validate_gate_config({"mode": "require"})


def test_gate_unknown_mode_rejected():
    with pytest.raises(ValueError, match="must be 'warn' or 'require'"):
        resolve_gate_config({"mode": "off"})


async def test_publish_blocked_then_forced_with_audit(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "billing", ["refund my order"])
    version = await _freeze(db_session, package.id)

    gate_config = {"mode": GateMode.REQUIRE.value, "min_samples_per_category": 5}
    with pytest.raises(IntentPublishBlockedError):
        await service.publish_version(
            package.id, version.id, WS_A, gate_config, force=False, actor_user_id=None, linked_result=None
        )
    stored = await service.get_version(package.id, version.id, WS_A)
    assert stored.published_at is None  # untouched by the rejection

    published = await service.publish_version(
        package.id, version.id, WS_A, gate_config, force=True, actor_user_id=uuid.uuid4(), linked_result=None
    )
    assert published.published_at is not None
    assert published.gate_report["bypassed_by_force"] is True

    audits = (
        (await db_session.execute(select(AuditLogModel).where(AuditLogModel.action == "INTENT_PACKAGE_GATE_BYPASS")))
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_["failing_signals"] == ["min_samples_per_category"]


async def test_latest_published_version_resolution(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "billing", ["x"])
    v1 = await _freeze(db_session, package.id, "v1")
    v2 = await _freeze(db_session, package.id, "v2")

    assert await service.latest_published_version(package.id) is None
    await service.publish_version(package.id, v1.id, WS_A, None, force=False, linked_result=None)
    assert (await service.latest_published_version(package.id)).id == v1.id
    await service.publish_version(package.id, v2.id, WS_A, None, force=False, linked_result=None)
    assert (await service.latest_published_version(package.id)).id == v2.id


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


async def test_correction_appends_draft_with_provenance(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    await _add_category_with_samples(db_session, service, package.id, "billing", ["refund my order"])
    version = await _freeze(db_session, package.id)
    await service.publish_version(package.id, version.id, WS_A, None, linked_result=None)

    submitter = uuid.uuid4()
    sample = await service.submit_correction(
        package.id,
        WS_A,
        IntentCorrectionCreateSchema(
            utterance="cancel my subscription",
            category_name="billing",
            reason="routed to default",
            source_session_id=uuid.uuid4(),
        ),
        submitted_by=submitter,
    )
    assert sample.provenance["source_type"] == "correction"
    assert sample.provenance["reason"] == "routed to default"
    assert sample.provenance["created_by"] == str(submitter)

    # Published version unchanged; draft carries the correction.
    stored = await service.get_version(package.id, version.id, WS_A)
    assert len(stored.content["categories"][0]["samples"]) == 1
    draft_samples = await service.list_samples(
        package.id, (await service.list_categories(package.id, WS_A))[0].id, WS_A
    )
    assert len(draft_samples) == 2


async def test_correction_unknown_category_rejected(db_session: AsyncSession):
    service = IntentPackageService(db_session)
    package = await _make_package(db_session)
    with pytest.raises(CategoryNotFoundError):
        await service.submit_correction(
            package.id,
            WS_A,
            IntentCorrectionCreateSchema(utterance="x", category_name="nope", reason="r"),
            submitted_by=None,
        )
