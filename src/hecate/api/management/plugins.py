"""Plugin management REST API."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel as PydanticBase
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.auth_context import AuthContext
from hecate.core.database import get_db
from hecate.core.deps_workspace import get_auth_context
from hecate.models.plugin import PluginModel
from hecate.services.plugin.service import AGENT_PLUGIN_TYPE, PluginService

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginReadSchema(PydanticBase):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    type: str
    version: str
    status: str
    entry: str
    manifest_: dict[str, Any]
    config: dict[str, Any]
    workspace_id: uuid.UUID | None
    origin: str | None = None
    content_hash: str | None = None
    scan_result: dict[str, Any] | None = None


class PluginConfigUpdateSchema(PydanticBase):
    config: dict[str, Any]


class AgentPluginInstallSchema(PydanticBase):
    """Source descriptor for Agent Plugins 1.0 installation (5.5c)."""

    source_type: str = Field(pattern="^(dir|git|zip)$")
    location: str
    ref: str | None = None
    workspace_id: uuid.UUID | None = None


@router.post("/agent-plugins/install", status_code=status.HTTP_201_CREATED)
async def install_agent_plugin(
    body: AgentPluginInstallSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    ctx: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> PluginReadSchema:
    """Install an Agent Plugins 1.0 package from dir/git/zip source."""
    from hecate.core.config import settings
    from hecate.services.plugin.service import FeatureDisabledError, PluginService

    if not settings.AGENT_PLUGINS_INGESTION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent Plugins ingestion is disabled",
        )

    from hecate.services.plugin.service import ScanBlockedError

    service = PluginService(db)
    try:
        plugin = await service.install_agent_plugin(
            source_type=body.source_type,
            location=body.location,
            plugins_dir=settings.PLUGINS_DIR,
            ref=body.ref,
            workspace_id=body.workspace_id,
            installer=str(ctx.user_id),
        )
    except FeatureDisabledError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ScanBlockedError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(e), "findings": e.findings},
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return PluginReadSchema.model_validate(plugin)


@router.get("")
async def list_plugins(
    workspace_id: uuid.UUID | None = Query(None),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> list[PluginReadSchema]:
    service = PluginService(db)
    plugins = await service.list_plugins(workspace_id)
    return [PluginReadSchema.model_validate(p) for p in plugins]


@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PluginReadSchema:
    service = PluginService(db)
    plugin = await service.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    return PluginReadSchema.model_validate(plugin)


@router.post("/{plugin_id}/enable")
async def enable_plugin(
    plugin_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PluginReadSchema:
    from hecate.services.plugin.service import ScanBlockedError

    service = PluginService(db)
    try:
        plugin = await service.enable_plugin(plugin_id)
    except ScanBlockedError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": str(e), "findings": e.findings},
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return PluginReadSchema.model_validate(plugin)


@router.get("/{plugin_id}/scan")
async def get_plugin_scan(
    plugin_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, Any]:
    """Return the package's current content-scan state (5.13a)."""
    service = PluginService(db)
    plugin = await service.get_plugin(plugin_id)
    if plugin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin not found")
    if plugin.type != AGENT_PLUGIN_TYPE:
        return {
            "plugin_id": str(plugin_id),
            "applicable": False,
            "reason": "content scanning applies to agent-plugin packages only",
        }
    scan_result = plugin.scan_result or {}
    return {
        "plugin_id": str(plugin_id),
        "applicable": True,
        "verdict": scan_result.get("verdict"),
        "findings": scan_result.get("findings", []),
        "scanner_version": scan_result.get("scanner_version"),
        "scanned_at": scan_result.get("scanned_at"),
        "acked_suppressed": scan_result.get("acked_suppressed", 0),
        "note": None if scan_result else "not yet scanned; a rescan runs on next enable",
    }


@router.post("/{plugin_id}/disable")
async def disable_plugin(
    plugin_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PluginReadSchema:
    service = PluginService(db)
    try:
        plugin = await service.disable_plugin(plugin_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return PluginReadSchema.model_validate(plugin)


@router.put("/{plugin_id}/config")
async def update_config(
    plugin_id: uuid.UUID,
    body: PluginConfigUpdateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PluginReadSchema:
    service = PluginService(db)
    try:
        plugin = await service.update_config(plugin_id, body.config)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return PluginReadSchema.model_validate(plugin)


class PluginCreateSchema(PydanticBase):
    manifest: dict[str, Any]


@router.post("/create")
async def create_plugin(
    body: PluginCreateSchema,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PluginReadSchema:
    m = body.manifest
    name = m.get("name")
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    plugin = PluginModel(
        name=name,
        type=m.get("type", "tool"),
        version=m.get("version", "0.1.0"),
        status="installed",
        entry=m.get("api_endpoint", ""),
        manifest_=m,
        config={},
    )
    db.add(plugin)
    await db.flush()
    return PluginReadSchema.model_validate(plugin)


@router.post("/upload")
async def upload_plugin(
    file: UploadFile = File(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> PluginReadSchema:
    import tempfile
    from pathlib import Path

    from hecate.core.config import settings

    if not file.filename or not file.filename.endswith(".hecate-plugin"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File must be a .hecate-plugin bundle")

    with tempfile.NamedTemporaryFile(delete=False, suffix=".hecate-plugin") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        service = PluginService(db)
        plugin = await service.install_plugin_from_bundle(tmp_path, settings.PLUGINS_DIR)
        return PluginReadSchema.model_validate(plugin)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@router.delete("/{plugin_id}")
async def delete_plugin(
    plugin_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    from hecate.core.config import settings

    service = PluginService(db)
    try:
        await service.uninstall_plugin_by_id(plugin_id, settings.PLUGINS_DIR)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    return {"status": "uninstalled"}
