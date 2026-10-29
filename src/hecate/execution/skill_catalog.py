"""Task-bound Skill surfaces and governed L2 loading for platform Controllers."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any

from hecate_runtime.harness_turn import HarnessTurnPipeline, ModelCallRejectedError, PreparedModelCall
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.agent import AgentModel
from hecate.models.plugin import PluginModel
from hecate.models.skill import ControllerSkillCatalogModel, SkillModel
from hecate.models.skill_version import SkillVersionModel
from hecate.models.task import TaskModel
from hecate.tools.skill.loader import CHARS_PER_TOKEN, SkillLoader, SkillNotAdvertisedError, SkillPinResolutionError
from hecate.tools.skill.versioning import snapshot_skill

DEFAULT_CONTROLLER_TOTAL_BUDGET = 4000
DEFAULT_CONTROLLER_CATALOG_BUDGET = 2000


class SkillCatalogFreezeError(ValueError):
    """Refuse unavailable or corrupt task-bound Skill state before model usage."""

    def __init__(self, message: str, *, reconciliation: bool = False) -> None:
        super().__init__(message)
        self.reconciliation = reconciliation


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _binding(row: ControllerSkillCatalogModel) -> dict[str, Any]:
    return {
        "task": str(row.task_id),
        "workspace": str(row.workspace_id),
        "agent": str(row.agent_id),
        "agent_version": str(row.agent_version_id),
        "rendered": row.catalog_rendered,
        "advertised": row.advertised_names,
        "pins": row.pins,
        "snapshots": row.unversioned_snapshots,
        "budgets": row.budgets,
    }


def verify_catalog(row: ControllerSkillCatalogModel) -> None:
    """Check identity-bound, versioned catalog state before any use."""
    try:
        names = row.advertised_names
        if (
            row.deleted
            or row.budgets.get("schema_version") != 1
            or not isinstance(names, list)
            or any(not isinstance(name, str) or not name for name in names)
            or len(set(names)) != len(names)
            or set(names) != set(row.pins) | set(row.unversioned_snapshots)
            or set(row.pins) & set(row.unversioned_snapshots)
        ):
            raise ValueError("invalid catalog shape")
        for name in names:
            item = row.pins.get(name) or row.unversioned_snapshots[name]
            uuid.UUID(item["skill_id"])
            uuid.UUID(item["workspace_id"])
            if not isinstance(item["rendered"], str) or item["content"]["name"] != name:
                raise ValueError("invalid resource snapshot")
        if _digest(_binding(row)) != row.content_sha256:
            raise ValueError("catalog digest mismatch")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SkillCatalogFreezeError(
            "Frozen Skill catalog is corrupt; reconciliation required", reconciliation=True
        ) from exc


async def freeze_controller_skill_catalog(
    db: AsyncSession,
    *,
    task_id: uuid.UUID,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    agent_version_snapshot: dict[str, Any],
    session_id: uuid.UUID | None = None,
    require_existing: bool = False,
) -> ControllerSkillCatalogModel:
    """Resolve once before model usage; the caller commits before proceeding.

    The platform Task lock serializes first creation on PostgreSQL. The
    unique Task key remains the backstop for SQLite or competing writers.
    A savepoint conflict never rolls back unrelated dispatcher state.
    """
    task = (
        await db.execute(
            select(TaskModel)
            .where(
                TaskModel.id == task_id,
                TaskModel.workspace_id == workspace_id,
                TaskModel.deleted.is_(False),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if task is None:
        raise SkillCatalogFreezeError("Skill catalog Task is not visible in this workspace")
    try:
        version_id = uuid.UUID(str(agent_version_snapshot["agent_version_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise SkillCatalogFreezeError("Skill catalog requires a frozen AgentVersion identity") from exc
    existing = await _existing(db, task_id)
    if existing is not None:
        _verify_identity(existing, workspace_id, agent_id, version_id)
        verify_catalog(existing)
        return existing
    if require_existing:
        raise SkillCatalogFreezeError("Accepted Controller history lost its Skill catalog", reconciliation=True)
    agent = await db.get(AgentModel, agent_id)
    if agent is None or agent.deleted or agent.workspace_id != workspace_id:
        raise SkillCatalogFreezeError("Skill catalog Agent is not visible in this workspace")
    names = agent_version_snapshot.get("skills") or []
    if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip() for name in names):
        raise SkillCatalogFreezeError("Invalid frozen AgentVersion Skill membership")
    manifest = agent_version_snapshot.get("ref_manifest") or []
    try:
        loader = SkillLoader(db, ref_manifest=manifest, explicit_skill_names=names, strict_pins=True)
        rendered = await loader.format_skills(
            agent_id,
            workspace_id,
            total_budget=DEFAULT_CONTROLLER_TOTAL_BUDGET,
            catalog_budget=DEFAULT_CONTROLLER_CATALOG_BUDGET,
            session_id=session_id,
        )
        # Structured selection comes from the renderer AFTER budget clipping;
        # Skill-authored text is never parsed as an authorization membership list.
        members = loader.catalog_members
        pins: dict[str, Any] = {}
        snapshots: dict[str, Any] = {}
        for skill in members:
            text = loader._format_single_skill(skill)
            if len(text) // CHARS_PER_TOKEN > skill.max_tokens:
                text = loader._truncate_to_tokens(text, skill.max_tokens)
            item: dict[str, Any] = {
                "skill_id": str(skill.id),
                "workspace_id": str(skill.workspace_id),
                "provider": skill.provider,
                "plugin_id": str(skill.plugin_id) if skill.plugin_id else None,
                "discovered": skill.name in loader.catalog_discovered,
                "auto_load": bool(skill.auto_load),
                "content": snapshot_skill(skill),
                "rendered": text,
            }
            pin = loader._pins_by_name.get(skill.name)
            if pin is not None:
                version = (
                    await db.execute(
                        select(SkillVersionModel).where(
                            SkillVersionModel.skill_id == pin[0],
                            SkillVersionModel.version == pin[1],
                            SkillVersionModel.workspace_id == skill.workspace_id,
                            SkillVersionModel.deleted.is_(False),
                        )
                    )
                ).scalar_one()
                item.update(version=pin[1], version_sha256=_digest(version.config_snapshot))
                pins[skill.name] = item
            else:
                # Even a live row with published versions needs a snapshot if
                # THIS AgentVersion did not select one of those versions.
                snapshots[skill.name] = item
    except (SkillPinResolutionError, TypeError, ValueError) as exc:
        raise SkillCatalogFreezeError(str(exc)) from exc
    row = ControllerSkillCatalogModel(
        task_id=task_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        agent_version_id=version_id,
        catalog_rendered=rendered,
        advertised_names=[skill.name for skill in members],
        pins=pins,
        unversioned_snapshots=snapshots,
        deleted=False,
        budgets={
            "total": DEFAULT_CONTROLLER_TOTAL_BUDGET,
            "catalog": DEFAULT_CONTROLLER_CATALOG_BUDGET,
            "schema_version": 1,
            "format_version": 1,
        },
    )
    row.content_sha256 = _digest(_binding(row))
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError as exc:
        existing = await _existing(db, task_id)
        if existing is None:
            raise SkillCatalogFreezeError("Concurrent Skill catalog creation could not be verified") from exc
        _verify_identity(existing, workspace_id, agent_id, version_id)
        verify_catalog(existing)
        return existing
    return row


async def _existing(db: AsyncSession, task_id: uuid.UUID) -> ControllerSkillCatalogModel | None:
    return (
        await db.execute(
            select(ControllerSkillCatalogModel)
            .where(
                ControllerSkillCatalogModel.task_id == task_id,
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def _verify_identity(
    row: ControllerSkillCatalogModel, workspace: uuid.UUID, agent: uuid.UUID, version: uuid.UUID
) -> None:
    if (row.workspace_id, row.agent_id, row.agent_version_id) != (workspace, agent, version):
        raise SkillCatalogFreezeError("Frozen Skill catalog ownership mismatch", reconciliation=True)


class ControllerSkillLoader(SkillLoader):
    """Serve exact frozen L2 bytes while checking the resource's live governance."""

    def __init__(self, db: AsyncSession, catalog: ControllerSkillCatalogModel) -> None:
        super().__init__(db)
        verify_catalog(catalog)
        self._task_id = catalog.task_id
        self._workspace_id = catalog.workspace_id
        self._agent_id = catalog.agent_id
        self._expected_digest = catalog.content_sha256

    async def load_skill_content(
        self,
        skill_name: str,
        agent_id: uuid.UUID,
        workspace_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> str:
        """Load one advertised identity; discovery never broadens this Task's set."""
        if (agent_id, workspace_id) != (self._agent_id, self._workspace_id):
            raise SkillNotAdvertisedError("Skill load identity does not match the frozen Task")
        row = await _existing(self._db, self._task_id)
        if row is None:
            raise SkillNotAdvertisedError("Frozen Skill catalog is unavailable")
        verify_catalog(row)
        if row.content_sha256 != self._expected_digest or (row.agent_id, row.workspace_id) != (agent_id, workspace_id):
            raise SkillNotAdvertisedError("Frozen Skill catalog changed")
        if skill_name not in row.advertised_names:
            raise SkillNotAdvertisedError(f"Skill '{skill_name}' is not advertised by this Task")
        item = copy.deepcopy(row.pins.get(skill_name) or row.unversioned_snapshots[skill_name])
        live = await self._validate_member(skill_name, item, workspace_id, agent_id)
        await self._record_usage(
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_id=session_id,
            skills=[live],
            event_type="skill_loaded",
        )
        return item["rendered"]

    async def validate_resources(self) -> None:
        """Recheck live governance before fresh model exposure, including auto-load."""
        row = await _existing(self._db, self._task_id)
        if row is None:
            raise SkillNotAdvertisedError("Frozen Skill catalog is unavailable")
        verify_catalog(row)
        if row.content_sha256 != self._expected_digest:
            raise SkillNotAdvertisedError("Frozen Skill catalog changed")
        for name in row.advertised_names:
            item = row.pins.get(name) or row.unversioned_snapshots[name]
            await self._validate_member(name, item, self._workspace_id, self._agent_id)

    async def _validate_member(
        self,
        skill_name: str,
        item: dict[str, Any],
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
    ) -> SkillModel:
        live = (
            await self._db.execute(
                select(SkillModel)
                .where(
                    SkillModel.id == uuid.UUID(item["skill_id"]),
                    SkillModel.workspace_id == uuid.UUID(item["workspace_id"]),
                    SkillModel.workspace_id.in_([workspace_id, uuid.UUID(int=0)]),
                    SkillModel.name == skill_name,
                    SkillModel.deleted.is_(False),
                    SkillModel.model_invocable.is_(True),
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        agent = (
            await self._db.execute(
                select(AgentModel)
                .where(
                    AgentModel.id == agent_id,
                    AgentModel.workspace_id == workspace_id,
                    AgentModel.deleted.is_(False),
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if (
            live is None
            or agent is None
            or live.provider != item["provider"]
            or (str(live.plugin_id) if live.plugin_id else None) != item["plugin_id"]
        ):
            raise SkillNotAdvertisedError("Frozen Skill resource is revoked or unavailable")
        if item["auto_load"] and not live.auto_load:
            raise SkillNotAdvertisedError("Skill automatic exposure was withdrawn")
        if item["discovered"]:
            self._discovery_policy = None
            policy = await self._resolve_discovery_policy(agent, workspace_id)
            if not policy.enabled or not policy.admits(live.trust_tier):
                raise SkillNotAdvertisedError("Skill discovery authorization was withdrawn")
        if live.plugin_id is not None:
            plugin = (
                await self._db.execute(
                    select(PluginModel)
                    .where(
                        PluginModel.id == live.plugin_id,
                        PluginModel.status == "enabled",
                        PluginModel.deleted.is_(False),
                        PluginModel.deleted_at.is_(None),
                    )
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if plugin is None or plugin.workspace_id not in (None, uuid.UUID(int=0), workspace_id):
                raise SkillNotAdvertisedError("Skill plugin is disabled or outside this workspace")
        if "version" in item:
            version = (
                await self._db.execute(
                    select(SkillVersionModel)
                    .where(
                        SkillVersionModel.skill_id == live.id,
                        SkillVersionModel.version == item["version"],
                        SkillVersionModel.workspace_id == live.workspace_id,
                        SkillVersionModel.deleted.is_(False),
                    )
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if (
                version is None
                or version.schema_version != 1
                or _digest(version.config_snapshot) != item["version_sha256"]
            ):
                raise SkillNotAdvertisedError("Frozen Skill version is missing or corrupt")
        return live


class SkillHarnessTurnPipeline(HarnessTurnPipeline):
    """Platform pre-call check for the Controller's frozen Skill resources."""

    def __init__(self, *, skill_loader: ControllerSkillLoader, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skill_loader = skill_loader

    async def prepare(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> PreparedModelCall:
        """Keep historical replay intact and refuse fresh exposure after withdrawal."""
        try:
            await self._skill_loader.validate_resources()
        except (SkillNotAdvertisedError, SkillCatalogFreezeError) as exc:
            raise ModelCallRejectedError("pre_llm", str(exc)) from exc
        return await super().prepare(messages, tools)
