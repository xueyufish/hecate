"""Intent package API endpoints (6.49).

CRUD for packages, draft categories and samples, bulk CSV/JSON
import/export, named versions with a deterministic publish gate, and
correction backflow:

- ``POST /api/intent-packages`` — Create a package
- ``GET /api/intent-packages`` — List packages (paginated)
- ``GET /api/intent-packages/{id}`` — Get package
- ``PUT /api/intent-packages/{id}`` — Update package metadata
- ``DELETE /api/intent-packages/{id}`` — Soft delete package
- ``GET /api/intent-packages/{id}/categories`` — List draft categories
- ``POST /api/intent-packages/{id}/categories`` — Add category
- ``PUT /api/intent-packages/{id}/categories/{cid}`` — Update category
- ``DELETE /api/intent-packages/{id}/categories/{cid}`` — Soft delete category
- ``GET /api/intent-packages/{id}/categories/{cid}/samples`` — List samples
- ``POST /api/intent-packages/{id}/categories/{cid}/samples`` — Add sample
- ``DELETE /api/intent-packages/{id}/categories/{cid}/samples/{sid}`` — Soft delete sample
- ``POST /api/intent-packages/{id}/import`` — Bulk import (csv/json, merge/replace)
- ``GET /api/intent-packages/{id}/export`` — Bulk export (csv/json)
- ``POST /api/intent-packages/{id}/corrections`` — Correction backflow submission
- ``GET /api/intent-packages/{id}/versions`` — List versions
- ``POST /api/intent-packages/{id}/versions`` — Freeze draft into a named version
- ``GET /api/intent-packages/{id}/versions/{vid}`` — Get version (soft-deleted readable)
- ``DELETE /api/intent-packages/{id}/versions/{vid}`` — Soft delete version
- ``POST /api/intent-packages/{id}/versions/{vid}/publish`` — Publish with gate
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.intent_package import (
    IntentCategoryCreateSchema,
    IntentCategoryUpdateSchema,
    IntentCorrectionCreateSchema,
    IntentPackageCreateSchema,
    IntentPackageUpdateSchema,
    IntentPackageVersionCreateSchema,
    IntentSampleCreateSchema,
    IntentVersionGateRequestSchema,
)
from hecate.studio.intent_packages.service import (
    CategoryNotFoundError,
    ImportValidationError,
    IntentPackageService,
    IntentPublishBlockedError,
    PackageNameConflictError,
    PackageNotFoundError,
    VersionNameConflictError,
    VersionNotFoundError,
)

router = APIRouter()

_NOT_FOUND_ERRORS = (PackageNotFoundError, CategoryNotFoundError, VersionNotFoundError)
_CONFLICT_ERRORS = (PackageNameConflictError, VersionNameConflictError)


def _workspace_id(ctx: AuthContext) -> uuid.UUID:
    return ctx.workspace_id or uuid.UUID(int=0)


def _conflict(message: str, details: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": {"code": "CONFLICT", "message": message, "details": details}},
    )


def _gate_blocked(exc: IntentPublishBlockedError) -> HTTPException:
    gate = exc.gate_result
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error": {
                "code": "INTENT_PACKAGE_GATE_BLOCKED",
                "message": "publish rejected by intent package gate",
                "details": {"gate": gate.to_report_payload(bypassed=False)},
            }
        },
    )


# ---------------------------------------------------------------------------
# Packages
# ---------------------------------------------------------------------------


@router.post("/intent-packages", status_code=status.HTTP_201_CREATED)
async def create_intent_package(
    data: IntentPackageCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Create a new intent package."""
    service = IntentPackageService(db)
    try:
        package = await service.create_package(data.name, data.description, _workspace_id(ctx))
    except PackageNameConflictError as e:
        raise _conflict(str(e)) from e
    return _package_payload(package)


@router.get("/intent-packages")
async def list_intent_packages(
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    """List the workspace's intent packages."""
    service = IntentPackageService(db)
    packages, total = await service.list_packages(_workspace_id(ctx), page=page, page_size=page_size)
    return {"items": [_package_payload(package) for package in packages], "total": total}


@router.get("/intent-packages/{package_id}")
async def get_intent_package(
    package_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get one intent package."""
    service = IntentPackageService(db)
    try:
        package = await service.get_package(package_id, _workspace_id(ctx))
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    return _package_payload(package)


@router.put("/intent-packages/{package_id}")
async def update_intent_package(
    package_id: uuid.UUID,
    data: IntentPackageUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update intent package metadata."""
    service = IntentPackageService(db)
    try:
        package = await service.update_package(
            package_id, _workspace_id(ctx), name=data.name, description=data.description
        )
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    except PackageNameConflictError as e:
        raise _conflict(str(e)) from e
    return _package_payload(package)


@router.delete("/intent-packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_intent_package(
    package_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete an intent package and its draft."""
    service = IntentPackageService(db)
    try:
        await service.delete_package(package_id, _workspace_id(ctx))
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e


# ---------------------------------------------------------------------------
# Draft categories & samples
# ---------------------------------------------------------------------------


@router.get("/intent-packages/{package_id}/categories")
async def list_categories(
    package_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """List the draft's categories in freeze order."""
    service = IntentPackageService(db)
    try:
        categories = await service.list_categories(package_id, _workspace_id(ctx))
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    return {"items": [_category_payload(category) for category in categories]}


@router.post("/intent-packages/{package_id}/categories", status_code=status.HTTP_201_CREATED)
async def add_category(
    package_id: uuid.UUID,
    data: IntentCategoryCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Add a category to the draft."""
    service = IntentPackageService(db)
    try:
        category = await service.add_category(package_id, _workspace_id(ctx), data)
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    except PackageNameConflictError as e:
        raise _conflict(str(e)) from e
    return _category_payload(category)


@router.put("/intent-packages/{package_id}/categories/{category_id}")
async def update_category(
    package_id: uuid.UUID,
    category_id: uuid.UUID,
    data: IntentCategoryUpdateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Update a draft category."""
    service = IntentPackageService(db)
    try:
        category = await service.update_category(package_id, category_id, _workspace_id(ctx), data)
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e
    except PackageNameConflictError as e:
        raise _conflict(str(e)) from e
    return _category_payload(category)


@router.delete("/intent-packages/{package_id}/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    package_id: uuid.UUID,
    category_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a draft category and its samples."""
    service = IntentPackageService(db)
    try:
        await service.delete_category(package_id, category_id, _workspace_id(ctx))
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e


@router.get("/intent-packages/{package_id}/categories/{category_id}/samples")
async def list_samples(
    package_id: uuid.UUID,
    category_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """List a category's samples in freeze order."""
    service = IntentPackageService(db)
    try:
        samples = await service.list_samples(package_id, category_id, _workspace_id(ctx))
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e
    return {"items": [_sample_payload(sample) for sample in samples]}


@router.post("/intent-packages/{package_id}/categories/{category_id}/samples", status_code=status.HTTP_201_CREATED)
async def add_sample(
    package_id: uuid.UUID,
    category_id: uuid.UUID,
    data: IntentSampleCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Add a sample utterance to a draft category."""
    service = IntentPackageService(db)
    try:
        sample = await service.add_sample(package_id, category_id, _workspace_id(ctx), data.utterance, data.provenance)
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e
    return _sample_payload(sample)


@router.delete(
    "/intent-packages/{package_id}/categories/{category_id}/samples/{sample_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_sample(
    package_id: uuid.UUID,
    category_id: uuid.UUID,
    sample_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a draft sample."""
    service = IntentPackageService(db)
    try:
        await service.delete_sample(package_id, category_id, sample_id, _workspace_id(ctx))
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e


# ---------------------------------------------------------------------------
# Import / export
# ---------------------------------------------------------------------------


@router.post("/intent-packages/{package_id}/import")
async def import_content(
    package_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    body: dict | None = None,
) -> dict:
    """Bulk-import categories and samples (all-or-nothing).

    Body: ``{"format": "csv"|"json", "mode": "merge"|"replace", "payload": str}``.
    """
    body = body or {}
    content_format = body.get("format", "json")
    mode = body.get("mode", "merge")
    payload = body.get("payload")
    if payload is None or not isinstance(payload, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": "body.payload (string) is required"}},
        )
    service = IntentPackageService(db)
    try:
        report = await service.import_content(package_id, _workspace_id(ctx), content_format, payload, mode=mode)
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    except ImportValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "IMPORT_VALIDATION_ERROR",
                    "message": str(e),
                    "details": {"errors": e.errors},
                }
            },
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e
    return report.model_dump()


@router.get("/intent-packages/{package_id}/export")
async def export_content(
    package_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    format: Annotated[str, Query(pattern="^(csv|json)$")] = "json",
) -> dict:
    """Export the draft as csv or json (string payload)."""
    service = IntentPackageService(db)
    try:
        payload = await service.export_content(package_id, _workspace_id(ctx), format)
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e
    return {"format": format, "payload": payload}


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


@router.post("/intent-packages/{package_id}/corrections", status_code=status.HTTP_201_CREATED)
async def submit_correction(
    package_id: uuid.UUID,
    data: IntentCorrectionCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Append a corrected sample to the draft with mandatory provenance."""
    service = IntentPackageService(db)
    try:
        sample = await service.submit_correction(package_id, _workspace_id(ctx), data, submitted_by=ctx.user_id)
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e
    return _sample_payload(sample)


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


@router.get("/intent-packages/{package_id}/versions")
async def list_versions(
    package_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    published_only: Annotated[bool, Query()] = False,
) -> dict:
    """List the package's versions, newest first."""
    service = IntentPackageService(db)
    try:
        versions, total = await service.list_versions(
            package_id, _workspace_id(ctx), page=page, page_size=page_size, published_only=published_only
        )
    except PackageNotFoundError as e:
        raise _not_found(str(e)) from e
    return {
        "items": [_version_payload(version) for version in versions],
        "total": total,
    }


@router.post("/intent-packages/{package_id}/versions", status_code=status.HTTP_201_CREATED)
async def create_version(
    package_id: uuid.UUID,
    data: IntentPackageVersionCreateSchema,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Freeze the draft into a named, unpublished version."""
    service = IntentPackageService(db)
    try:
        version = await service.create_version(package_id, _workspace_id(ctx), data, created_by=ctx.user_id)
    except (PackageNotFoundError, VersionNotFoundError) as e:
        raise _not_found(str(e)) from e
    except VersionNameConflictError as e:
        raise _conflict(str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e
    return _version_payload(version)


@router.get("/intent-packages/{package_id}/versions/{version_id}")
async def get_version(
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Get one version (soft-deleted versions stay readable)."""
    service = IntentPackageService(db)
    try:
        version = await service.get_version(package_id, version_id, _workspace_id(ctx))
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e
    return _version_payload(version)


@router.delete("/intent-packages/{package_id}/versions/{version_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_version(
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> None:
    """Soft-delete a version; its name stays reserved."""
    service = IntentPackageService(db)
    try:
        await service.delete_version(package_id, version_id, _workspace_id(ctx))
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e


@router.post("/intent-packages/{package_id}/versions/{version_id}/publish")
async def publish_version(
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    request: IntentVersionGateRequestSchema | None = None,
) -> dict:
    """Publish a version after deterministic gate evaluation.

    Request body (optional): ``{"gate": {mode, min_pass_rate?,
    min_samples_per_category?}, "force": bool}``. A require-mode rejection
    returns 409 ``INTENT_PACKAGE_GATE_BLOCKED`` with the full gate report.
    """
    request = request or IntentVersionGateRequestSchema()
    service = IntentPackageService(db)
    gate_raw = request.gate.model_dump(mode="json") if request.gate else None
    try:
        version = await service.publish_version(
            package_id,
            version_id,
            _workspace_id(ctx),
            gate_raw,
            force=request.force,
            actor_user_id=ctx.user_id,
            linked_result=None,
        )
    except _NOT_FOUND_ERRORS as e:
        raise _not_found(str(e)) from e
    except IntentPublishBlockedError as e:
        raise _gate_blocked(e) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "VALIDATION_ERROR", "message": str(e), "details": None}},
        ) from e
    return _version_payload(version)


@router.post("/intent-packages/{package_id}/versions/{version_id}/eval-dataset", status_code=201)
async def generate_eval_dataset(
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    heldout_ratio: Annotated[float, Query(gt=0, le=1)] = 1.0,
) -> dict:
    """Generate a recognition-accuracy evaluation dataset from the version's samples (6.49)."""
    from hecate.studio.intent_packages.eval_linkage import IntentEvalLinkageService

    service = IntentEvalLinkageService(db)
    try:
        dataset = await service.generate_eval_dataset(
            package_id, version_id, _workspace_id(ctx), heldout_ratio=heldout_ratio, created_by=ctx.user_id
        )
    except ValueError as e:
        raise _not_found(str(e)) from e
    return {"dataset_id": str(dataset.id), "name": dataset.name}


@router.post("/intent-packages/{package_id}/versions/{version_id}/eval-runs", status_code=202)
async def trigger_recognition_run(
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    heldout_ratio: Annotated[float, Query(gt=0, le=1)] = 1.0,
) -> dict:
    """Trigger a recognition-accuracy run (7.2c machinery, background 202)."""
    from hecate.studio.intent_packages.eval_linkage import IntentEvalLinkageService

    service = IntentEvalLinkageService(db)
    try:
        run = await service.trigger_recognition_run(
            package_id, version_id, _workspace_id(ctx), created_by=ctx.user_id, heldout_ratio=heldout_ratio
        )
    except ValueError as e:
        raise _not_found(str(e)) from e
    return {"run_id": str(run.id), "status": run.status}


@router.get("/intent-packages/{package_id}/versions/{version_id}/eval-results")
async def get_eval_results(
    package_id: uuid.UUID,
    version_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
) -> dict:
    """Latest recognition-accuracy result for the version (gate-shaped)."""
    from hecate.studio.intent_packages.eval_linkage import IntentEvalLinkageService

    service = IntentEvalLinkageService(db)
    result = await service.latest_recognition_result(package_id, version_id, _workspace_id(ctx))
    return {"linked_result": result}


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "NOT_FOUND", "message": message, "details": None}},
    )


def _package_payload(package) -> dict:
    from hecate.models.intent_package import IntentPackageReadSchema

    return IntentPackageReadSchema.model_validate(package).model_dump(mode="json")


def _category_payload(category) -> dict:
    from hecate.models.intent_package import IntentCategoryReadSchema

    return IntentCategoryReadSchema.model_validate(category).model_dump(mode="json")


def _sample_payload(sample) -> dict:
    from hecate.models.intent_package import IntentSampleReadSchema

    return IntentSampleReadSchema.model_validate(sample).model_dump(mode="json")


def _version_payload(version) -> dict:
    from hecate.models.intent_package import IntentPackageVersionReadSchema

    return IntentPackageVersionReadSchema.model_validate(version).model_dump(mode="json")
