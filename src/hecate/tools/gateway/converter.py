"""OpenAPI → MCP tool conversion.

Converts an OpenAPI 3.x document into gateway tool projections: one
``ToolModel`` row per supported operation (``source="rest"``, named
``<target>__<operation>``). The conversion supports the subset needed for
request/response tool calls — path/query parameters and JSON request
bodies — and reports anything it skips instead of failing silently. The
document itself is stored verbatim on the target; execution never
re-parses it (projections carry the resolved schema).
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.tool import ToolModel
from hecate.tools.gateway.errors import SpecConversionError

logger = logging.getLogger(__name__)

_SUPPORTED_METHODS = ("get", "post", "put", "patch", "delete")
_SEGMENT_CLEANER = re.compile(r"[^a-z0-9]+")
_ZERO_WORKSPACE = uuid.UUID("00000000-0000-0000-0000-000000000000")


@dataclass
class OperationSpec:
    """One projected operation, ready to become a tool row.

    Argument routing (path/query/body) is embedded in the projected
    schema's ``x-gateway`` extension so the executor never re-parses the
    OpenAPI document.
    """

    name: str
    method: str
    path: str
    description: str
    parameters_schema: dict


@dataclass
class ImportReport:
    """Outcome of a target tool import — imported, skipped, warnings."""

    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _derive_operation_name(method: str, path: str) -> str:
    """Derive a kebab-case operation name from method and path.

    ``GET /contacts/{contact_id}`` → ``get-contacts-by-contact-id``.
    """
    segments = []
    for raw in path.strip("/").split("/"):
        if not raw:
            continue
        if raw.startswith("{") and raw.endswith("}"):
            segments.append("by-" + raw[1:-1])
        else:
            segments.append(raw)
    slug = _SEGMENT_CLEANER.sub("-", f"{method}-{'-'.join(segments)}").strip("-")
    return slug[:200]


def _merge_request_body(op: dict, properties: dict, required: list[str], warnings: list[str]) -> list[str]:
    """Inline an ``application/json`` request body into the tool schema.

    Returns the names of the properties that came from the body.
    """
    body = op.get("requestBody")
    if not body:
        return []
    content = body.get("content", {})
    json_media = content.get("application/json")
    if json_media is None:
        media_type = next(iter(content), None)
        if media_type is not None:
            warnings.append(f"requestBody media type {media_type!r} unsupported; body ignored")
        return []
    schema = json_media.get("schema", {})
    if schema.get("type") == "object":
        body_props = list(schema.get("properties", {}).keys())
        properties.update(schema.get("properties", {}))
        required.extend(r for r in schema.get("required", []) if r not in required)
        return body_props
    properties["body"] = schema
    return ["body"]


def parse_openapi_operations(spec: dict) -> tuple[list[OperationSpec], list[str]]:
    """Parse an OpenAPI document into projected operations.

    Args:
        spec: Parsed OpenAPI document (dict).

    Returns:
        Tuple of (operations, warnings). Unsupported constructs produce
        warnings and are skipped; structurally invalid documents raise.

    Raises:
        SpecConversionError: If the document is not OpenAPI 3.x or has no
            parseable ``paths`` object.
    """
    if not isinstance(spec, dict):
        raise SpecConversionError("OpenAPI document must be a JSON object")
    openapi_version = str(spec.get("openapi", ""))
    if not openapi_version.startswith("3"):
        raise SpecConversionError(f"Unsupported OpenAPI version {openapi_version!r}; only 3.x is supported")
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise SpecConversionError("OpenAPI document has no paths")

    warnings: list[str] = []
    operations: list[OperationSpec] = []
    seen_names: set[str] = set()

    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            warnings.append(f"path {path!r} skipped: not an object")
            continue
        if path_item.get("servers"):
            warnings.append(f"path {path!r}: server overrides are ignored (base_url is pinned)")
        for method, op in path_item.items():
            if method not in _SUPPORTED_METHODS:
                if isinstance(op, dict):
                    warnings.append(f"{method.upper()} {path} skipped: unsupported method")
                continue
            if not isinstance(op, dict):
                warnings.append(f"{method.upper()} {path} skipped: operation not an object")
                continue
            if op.get("callbacks"):
                warnings.append(f"{method.upper()} {path}: callbacks skipped")
            if op.get("links") or path_item.get("links"):
                warnings.append(f"{method.upper()} {path}: links skipped")

            name = op.get("operationId") or _derive_operation_name(method, path)
            while name in seen_names:
                name = f"{name}-x"
            seen_names.add(name)

            properties: dict = {}
            required: list[str] = []
            path_params: list[str] = []
            query_params: list[str] = []
            for param in op.get("parameters", []):
                if not isinstance(param, dict) or param.get("in") not in ("path", "query"):
                    continue
                prop_schema = param.get("schema", {"type": "string"})
                properties[param.get("name", "")] = prop_schema
                if param.get("in") == "path":
                    path_params.append(param.get("name", ""))
                else:
                    query_params.append(param.get("name", ""))
                if param.get("required"):
                    required.append(param.get("name", ""))
            body_params = _merge_request_body(op, properties, required, warnings)

            operations.append(
                OperationSpec(
                    name=name,
                    method=method.upper(),
                    path=path,
                    description=op.get("description") or op.get("summary") or "",
                    parameters_schema={
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "x-gateway": {
                            "method": method.upper(),
                            "path": path,
                            "path_params": path_params,
                            "query_params": query_params,
                            "body_params": body_params,
                        },
                    },
                )
            )
    if not operations:
        raise SpecConversionError("No supported operations found in OpenAPI document")
    return operations, warnings


async def project_target_tools(db: AsyncSession, target) -> ImportReport:
    """(Re-)project a rest target's OpenAPI document into tool rows.

    Existing non-deleted tool rows for the target are soft-deleted first,
    so re-import replaces the projection atomically.

    Args:
        db: Async database session.
        target: A ``GatewayTargetModel`` of kind ``rest``.

    Returns:
        The import report (imported tool names, skipped, warnings).

    Raises:
        SpecConversionError: If the stored spec is invalid. No rows are
            written in that case.
    """
    operations, warnings = parse_openapi_operations(target.spec)

    existing = await db.execute(
        select(ToolModel).where(
            ToolModel.target_id == target.id,
            ~ToolModel.deleted,
        )
    )
    for row in existing.scalars().all():
        row.deleted = True
        row.deleted_at = _dt.datetime.now(_dt.timezone.utc)  # noqa: UP017

    report = ImportReport(warnings=warnings)
    for op in operations:
        tool_name = f"{target.name}__{op.name}"
        db.add(
            ToolModel(
                workspace_id=(target.workspace_id if target.workspace_id is not None else _ZERO_WORKSPACE),
                name=tool_name,
                description=op.description or f"{op.method} {op.path}",
                source="rest",
                parameters=op.parameters_schema,
                risk_level="LOW",
                approval_required=False,
                target_id=target.id,
            )
        )
        report.imported.append(tool_name)
    await db.flush()
    logger.info(
        "Projected %d tools for gateway target '%s' (%d skipped, %d warnings)",
        len(report.imported),
        target.name,
        len(report.skipped),
        len(report.warnings),
    )
    return report
