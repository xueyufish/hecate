"""Studio-side existence validation for intent package references (5.1/6.49).

The runtime compiler validates *shape* (non-empty mappings, declared
targets); this module validates *existence* — that referenced intent
packages exist in the workspace, that version pins resolve to published
versions, and that mapping/category keys exist in the referenced version.
It runs on the workflow save path, which owns DB access.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.intent_package import IntentPackageVersionModel
from hecate.studio.workflows.graph_dsl import GraphValidationError

logger = logging.getLogger(__name__)


async def _version_categories(
    db: AsyncSession,
    package_id: uuid.UUID,
    version_id: uuid.UUID | None,
    workspace_id: uuid.UUID,
) -> tuple[list[str], uuid.UUID]:
    """Resolve a published version's category names; raises if unresolvable."""
    stmt = select(IntentPackageVersionModel).where(
        IntentPackageVersionModel.package_id == package_id,
        IntentPackageVersionModel.published_at.is_not(None),
        ~IntentPackageVersionModel.deleted,
        IntentPackageVersionModel.workspace_id == workspace_id,
    )
    if version_id is not None:
        stmt = stmt.where(IntentPackageVersionModel.id == version_id)
    stmt = stmt.order_by(
        IntentPackageVersionModel.published_at.desc(),
        IntentPackageVersionModel.id.desc(),
    ).limit(1)
    version = (await db.execute(stmt)).scalar_one_or_none()
    if version is None:
        msg = f"Intent package {package_id} has no published version"
        if version_id is not None:
            msg += f" matching pin {version_id}"
        raise ValueError(msg)
    names = [c.get("name") for c in (version.content or {}).get("categories", []) if c.get("name")]
    return names, version.id


async def validate_intent_package_ref(
    db: AsyncSession,
    package_ref: dict,
    expected_categories: list[str] | None,
    workspace_id: uuid.UUID,
    *,
    field: str = "intent_package",
) -> uuid.UUID:
    """Validate one ``{"package_id", "version_id"?}`` reference.

    Args:
        expected_categories: Mapping/category keys that must exist in the
            referenced version (``None`` skips the key check).

    Returns:
        The resolved published version id.

    Raises:
        ValueError: When the reference is unresolvable or a key is absent.
    """
    try:
        package_id = uuid.UUID(str((package_ref or {}).get("package_id")))
    except (ValueError, AttributeError) as e:
        raise ValueError(f"{field}: invalid package_id") from e
    pin = (package_ref or {}).get("version_id")
    version_id = uuid.UUID(str(pin)) if pin else None
    names, resolved = await _version_categories(db, package_id, version_id, workspace_id)
    if expected_categories:
        missing = [key for key in expected_categories if key not in names]
        if missing:
            raise ValueError(f"{field}: categories absent from the referenced package version: {missing}")
    return resolved


async def validate_graph_intent_references(
    db: AsyncSession,
    graph_config,
    workspace_id: uuid.UUID,
) -> None:
    """Validate all intent package references in a parsed graph.

    Covers CONTROLLER node configs (2.6a) and CONDITION nodes with
    package-backed intent routing (6.23). Called from the workflow save
    path; failures surface as :class:`GraphValidationError` so the API
    contract matches other graph validation errors.
    """
    from hecate.runtime.types import NodeType

    for node_id, node in graph_config.nodes.items():
        if node.type == NodeType.CONTROLLER:
            ref = node.config.get("intent_package") or {}
            category_targets = list((node.config.get("category_targets") or {}).keys())
            try:
                await validate_intent_package_ref(db, ref, category_targets, workspace_id)
            except ValueError as e:
                raise GraphValidationError(
                    f"CONTROLLER node '{node_id}': {e}",
                    field=f"nodes[{node_id}].config.intent_package",
                ) from e
        elif node.type == NodeType.CONDITION:
            routing_mode = node.config.get("routing_mode")
            if routing_mode != "intent":
                continue
            routing_config = node.config.get("routing_config") or {}
            ref = routing_config.get("intent_package")
            if not ref:
                continue
            category_targets = list((routing_config.get("category_targets") or {}).keys())
            try:
                await validate_intent_package_ref(db, ref, category_targets, workspace_id)
            except ValueError as e:
                raise GraphValidationError(
                    f"CONDITION node '{node_id}': {e}",
                    field=f"nodes[{node_id}].config.routing_config.intent_package",
                ) from e


__all__ = ["validate_graph_intent_references", "validate_intent_package_ref"]
