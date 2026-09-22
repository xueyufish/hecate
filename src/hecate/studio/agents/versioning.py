"""Agent versioning service (1.3.20).

Implements the agent-version lifecycle on top of immutable
``agent_versions`` snapshots:

- **commit** — freeze the live agent row into a new version, pinning the
  referenced workflow to a specific workflow version and recording a
  reference manifest (content hashes) for not-yet-versioned resources.
- **publish** — move the ``agents.published_version`` pointer, optionally
  gated by the same evaluation-gate machinery workflows use (7.3a).
- **rollback** — restore a target version's content as a *new* version;
  going live still requires an explicit publish.
- **diff / list / rename / delete** — version management with deletion
  constraints (published versions are undeletable).
- **resolve** — the single seam every invocation path goes through:
  ``version=None`` yields the live draft, a version number yields the
  frozen snapshot with its pinned workflow version.
- **drift** — compare a snapshot's reference manifest against live
  content so "resolved live" resources stay visible when they change.

The immutability promise is deliberately *configuration-level*: unpinned
resources resolve to their current content at execution time, and model
weights may change behind a model id. The manifest makes that drift
visible instead of pretending it cannot happen.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.canonical_hash import canonical_hash
from hecate.models.agent import AgentModel
from hecate.models.agent_version import AgentVersionModel
from hecate.models.evaluation import EvaluationRunModel

logger = logging.getLogger(__name__)

#: Bump when the snapshot shape changes; resolvers ignore unknown fields.
SNAPSHOT_SCHEMA_VERSION = 1

#: Own-config fields captured in every snapshot (alias keys, matching the
#: API's ``model_config`` spelling for the LLM config dict).
OWN_CONFIG_FIELDS: tuple[str, ...] = (
    "name",
    "persona",
    "model_config",
    "mode",
    "workflow_id",
    "tools",
    "skills",
    "skill_ids",
    "knowledge_base_ids",
    "risk_level",
    "opening_remarks",
    "enable_suggestions",
    "guardrail_config",
)


class AgentPublishEvaluationGateBlockedError(Exception):
    """Raised when a require-mode gate rejects an agent publish (1.3.20).

    Carries the gate verdict and evaluation report so the API layer can
    return them in the 409 body without re-querying.
    """

    def __init__(self, message: str, report: dict, gate_result) -> None:
        super().__init__(message)
        self.report = report
        self.gate_result = gate_result


@dataclass
class ResolvedAgentConfig:
    """Effective agent configuration produced by :meth:`resolve`.

    ``source="live"`` means the editable draft row; ``source="version"``
    means a frozen snapshot. ``workflow_version`` is the pinned workflow
    version for snapshot resolution (``None`` for live — callers load the
    workflow's current behavior, same as pre-versioning).
    """

    agent_id: uuid.UUID
    workspace_id: uuid.UUID
    source: str
    version: int | None
    config: dict[str, Any]
    workflow_version: int | None = None
    pinned_refs: list[dict[str, Any]] = field(default_factory=list)
    ref_manifest: list[dict[str, Any]] = field(default_factory=list)


# `canonical_hash` lives in `hecate.core.canonical_hash` and is re-exported
# here: stored `AgentVersionModel.content_hash` values were produced by this
# name, and the skill registry hashes the same content-field sets for
# drift/pin comparability (5.9-enh).
__all__ = ["AgentVersionService", "canonical_hash", "snapshot_own_config"]


def snapshot_own_config(agent: AgentModel) -> dict[str, Any]:
    """Extract the agent's own config fields as a snapshot dict."""
    return {
        "name": agent.name,
        "persona": agent.persona,
        "model_config": agent.model_config_db,
        "mode": agent.mode,
        "workflow_id": str(agent.workflow_id) if agent.workflow_id else None,
        "tools": agent.tools or [],
        "skills": agent.skills or [],
        "skill_ids": agent.skill_ids or [],
        "knowledge_base_ids": agent.knowledge_base_ids or [],
        "risk_level": agent.risk_level,
        "opening_remarks": agent.opening_remarks,
        "enable_suggestions": agent.enable_suggestions,
        "guardrail_config": agent.guardrail_config,
    }


class AgentVersionService:
    """Version lifecycle + resolution seam for agents (1.3.20)."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- snapshot construction -------------------------------------------

    async def _build_pinned_refs(self, agent: AgentModel) -> list[dict[str, Any]]:
        """Pin the referenced workflow to a concrete version at commit time.

        The workflow's ``published_version`` is pinned when set, falling
        back to its highest version — a workflow that never published
        keeps today's behavior (its latest graph) at the moment of the
        commit.
        """
        if agent.mode != "workflow" or not agent.workflow_id:
            return []
        from hecate.models.workflow import WorkflowModel

        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == agent.workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            return []
        pinned = workflow.published_version
        if pinned is None:
            from hecate.models.workflow import WorkflowVersionModel

            latest = await self.db.execute(
                select(func.max(WorkflowVersionModel.version)).where(
                    WorkflowVersionModel.workflow_id == workflow.id,
                    ~WorkflowVersionModel.deleted,
                )
            )
            pinned = latest.scalar_one()
        return [
            {
                "resource_type": "workflow",
                "resource_id": str(workflow.id),
                "version": pinned,
            }
        ]

    async def _build_ref_manifest(self, agent: AgentModel) -> list[dict[str, Any]]:
        """Record references at commit time.

        Skills are versioned resources (5.9d): same-name candidates resolve
        through the provider registry (the winner must match what the
        loader serves — storage order decides nothing), and a skill with
        committed versions pins to its latest version
        ``(name, skill_id, provider, version, content_hash)``. Skills
        never committed (and plugin-sourced rows) stay unpinned with a
        live content hash so drift stays detectable. Tool entries are
        hashed as they appear on the agent row. Knowledge bases
        deliberately get no hash — their corpus is mutable data (the
        snapshot references the KB, it does not freeze its documents).
        """
        manifest: list[dict[str, Any]] = []

        skill_names = [s for s in (agent.skills or []) if isinstance(s, str)]
        if skill_names:
            manifest.extend(await self._skill_manifest_entries(agent, skill_names))

        for tool in agent.tools or []:
            manifest.append(
                {
                    "resource_type": "tool",
                    "resource_id": tool if isinstance(tool, str) else canonical_hash(tool)[:16],
                    "version": None,
                    "content_hash": canonical_hash(tool),
                }
            )

        for kb_id in agent.knowledge_base_ids or []:
            manifest.append(
                {
                    "resource_type": "knowledge_base",
                    "resource_id": str(kb_id),
                    "version": None,
                    "content_hash": None,
                }
            )

        return manifest

    async def _skill_manifest_entries(self, agent: AgentModel, skill_names: list[str]) -> list[dict[str, Any]]:
        """Build the skill entries of the reference manifest (5.9d).

        A pinned entry carries ``(resource_type, resource_id=name,
        skill_id, provider, version, content_hash)`` where content_hash
        is the pinned version's own hash; an unpinned entry (version
        ``None``) hashes the live row so post-commit changes stay
        drift-detectable. Missing skills keep the legacy hash-less entry
        shape (drift reports them through the live-manifest comparison).
        """
        from hecate.models.skill import SkillModel
        from hecate.models.skill_version import SkillVersionModel

        zero_uuid = uuid.UUID(int=0)
        result = await self.db.execute(
            select(SkillModel).where(
                SkillModel.name.in_(skill_names),
                SkillModel.workspace_id.in_([agent.workspace_id, zero_uuid]),
                ~SkillModel.deleted,
            )
        )
        # Lazy: cross-domain function-level import (see gate glue below).
        from hecate.tools.skill.provider_registry import resolve_precedence_map

        winners = resolve_precedence_map(list(result.scalars().all()))

        latest_by_skill: dict[uuid.UUID, SkillVersionModel] = {}
        winner_ids = [s.id for s in winners.values()]
        if winner_ids:
            rows = await self.db.execute(
                select(SkillVersionModel)
                .where(
                    SkillVersionModel.skill_id.in_(winner_ids),
                    ~SkillVersionModel.deleted,
                )
                .order_by(SkillVersionModel.version.desc())
            )
            for row in rows.scalars().all():
                latest_by_skill.setdefault(row.skill_id, row)

        entries: list[dict[str, Any]] = []
        for name in skill_names:
            skill = winners.get(name)
            if skill is None:
                entries.append(
                    {
                        "resource_type": "skill",
                        "resource_id": name,
                        "version": None,
                        "content_hash": None,
                    }
                )
                continue
            latest = latest_by_skill.get(skill.id)
            if latest is not None:
                entries.append(
                    {
                        "resource_type": "skill",
                        "resource_id": name,
                        "skill_id": str(skill.id),
                        "provider": skill.provider,
                        "version": latest.version,
                        "content_hash": latest.content_hash,
                    }
                )
            else:
                entries.append(
                    {
                        "resource_type": "skill",
                        "resource_id": name,
                        "skill_id": str(skill.id),
                        "provider": skill.provider,
                        "version": None,
                        "content_hash": canonical_hash(
                            {
                                "name": skill.name,
                                "instructions": skill.instructions,
                                "allowed_tools": skill.allowed_tools,
                                "scripts": skill.scripts,
                                "references": skill.references,
                            }
                        ),
                    }
                )
        return entries

    # --- lifecycle ---------------------------------------------------------

    async def commit_version(
        self,
        agent_id: uuid.UUID,
        *,
        name: str = "",
        change_summary: str = "",
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Freeze the live agent row into a new immutable version.

        Raises:
            ValueError: If the agent does not exist.
        """
        agent = await self._get_agent(agent_id)

        config = snapshot_own_config(agent)
        pinned_refs = await self._build_pinned_refs(agent)
        ref_manifest = await self._build_ref_manifest(agent)
        publish_warnings = await self._model_publish_warnings(agent)

        latest = await self.db.execute(
            select(func.max(AgentVersionModel.version)).where(
                AgentVersionModel.agent_id == agent.id,
                ~AgentVersionModel.deleted,
            )
        )
        next_version = (latest.scalar_one() or 0) + 1

        version = AgentVersionModel(
            agent_id=agent.id,
            version=next_version,
            name=name,
            change_summary=change_summary,
            config_snapshot=config,
            pinned_refs=pinned_refs,
            ref_manifest=ref_manifest,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            content_hash=canonical_hash(config),
            created_by=actor_user_id,
            workspace_id=agent.workspace_id,
        )
        self.db.add(version)
        await self.db.flush()
        await self.refresh_version(version)
        logger.info("Committed version %s for agent %s", next_version, agent_id)
        detail = self._version_detail(version, agent.published_version)
        if publish_warnings:
            # Warn, never block (6.47): reference gating hides unpublished
            # models from pickers but does not police existing bindings.
            detail["warnings"] = publish_warnings
        return detail

    async def list_versions(self, agent_id: uuid.UUID) -> list[dict[str, Any]]:
        """List an agent's versions, newest first, with publish flags."""
        agent = await self._get_agent(agent_id)
        result = await self.db.execute(
            select(AgentVersionModel)
            .where(
                AgentVersionModel.agent_id == agent.id,
                ~AgentVersionModel.deleted,
            )
            .order_by(AgentVersionModel.version.desc())
        )
        return [self._version_read(v, agent.published_version) for v in result.scalars().all()]

    async def get_version(
        self,
        agent_id: uuid.UUID,
        version: int,
        *,
        include_snapshot: bool = False,
    ) -> dict[str, Any]:
        """Fetch one version; optionally include the full snapshot."""
        agent = await self._get_agent(agent_id)
        record = await self._get_version(agent.id, version)
        if record is None:
            raise ValueError(f"Version {version} not found for agent {agent_id}")
        if include_snapshot:
            return self._version_detail(record, agent.published_version)
        return self._version_read(record, agent.published_version)

    async def update_version(
        self,
        agent_id: uuid.UUID,
        version: int,
        *,
        name: str | None = None,
        change_summary: str | None = None,
    ) -> dict[str, Any]:
        """Rename a version / edit its release notes (metadata only).

        The snapshot content columns are never touched.
        """
        agent = await self._get_agent(agent_id)
        record = await self._get_version(agent.id, version)
        if record is None:
            raise ValueError(f"Version {version} not found for agent {agent_id}")
        if name is not None:
            record.name = name
        if change_summary is not None:
            record.change_summary = change_summary
        await self.db.flush()
        await self.refresh_version(record)
        return self._version_detail(record, agent.published_version)

    async def publish_version(
        self,
        agent_id: uuid.UUID,
        version: int,
        *,
        force: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Publish a committed version: move the ``published_version`` pointer.

        With ``evaluation_gate`` in ``mode=require``, a failing enabled
        signal raises :class:`AgentPublishEvaluationGateBlockedError`
        unless ``force`` bypasses it (bypass recorded in the audit log).
        Publish never mutates the snapshot.

        Raises:
            ValueError: If agent or version not found.
            AgentPublishEvaluationGateBlockedError: On a require-gate
                rejection.
        """
        agent = await self._get_agent(agent_id)
        record = await self._get_version(agent.id, version)
        if record is None:
            raise ValueError(f"Version {version} not found for agent {agent_id}")

        gate_result = None
        gate_blocking = False
        gate_config = self._resolve_gate_config(agent.evaluation_gate)
        if gate_config.mode != "off":
            candidate_run = await self._latest_completed_run(agent.id)
            gate_result = await self._evaluate_gate(gate_config, candidate_run)
            gate_blocking = gate_result.blocking
            if gate_blocking and not force:
                report = self._gate_report(gate_result, bypassed=False)
                raise AgentPublishEvaluationGateBlockedError(
                    "publish rejected by evaluation_gate",
                    report,
                    gate_result,
                )

        agent.published_version = version
        await self.db.flush()

        if gate_blocking and force and actor_user_id is not None:
            await self._record_gate_bypass(agent, version, actor_user_id, gate_result)

        logger.info("Published version %s for agent %s", version, agent_id)
        return self._version_detail(record, agent.published_version)

    async def rollback_to_version(
        self,
        agent_id: uuid.UUID,
        target_version: int,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Create a new version carrying a target version's frozen content.

        Mirrors workflow rollback: the pointer is untouched — the new
        version must be published to go live.
        """
        agent = await self._get_agent(agent_id)
        target = await self._get_version(agent.id, target_version)
        if target is None:
            raise ValueError(f"Version {target_version} not found for agent {agent_id}")

        latest = await self.db.execute(
            select(func.max(AgentVersionModel.version)).where(
                AgentVersionModel.agent_id == agent.id,
                ~AgentVersionModel.deleted,
            )
        )
        new_version_num = (latest.scalar_one() or 0) + 1

        version = AgentVersionModel(
            agent_id=agent.id,
            version=new_version_num,
            name=target.name,
            change_summary=f"Rollback to version {target_version}",
            config_snapshot=target.config_snapshot,
            pinned_refs=target.pinned_refs,
            ref_manifest=target.ref_manifest,
            schema_version=target.schema_version,
            content_hash=target.content_hash,
            created_by=actor_user_id,
            workspace_id=agent.workspace_id,
        )
        self.db.add(version)
        await self.db.flush()
        await self.refresh_version(version)
        logger.info(
            "Rolled back agent %s to version %s (new version %s)",
            agent_id,
            target_version,
            new_version_num,
        )
        return self._version_detail(version, agent.published_version)

    async def diff_versions(self, agent_id: uuid.UUID, v1: int, v2: int) -> dict[str, Any]:
        """Compare two versions' snapshots (own config + reference changes)."""
        agent = await self._get_agent(agent_id)
        ver1 = await self._get_version(agent.id, v1)
        if ver1 is None:
            raise ValueError(f"Version {v1} not found for agent {agent_id}")
        ver2 = await self._get_version(agent.id, v2)
        if ver2 is None:
            raise ValueError(f"Version {v2} not found for agent {agent_id}")

        identical = ver1.content_hash == ver2.content_hash
        ref_changes = self._ref_manifest_diff(ver1, ver2)
        details: dict[str, Any] = {"reference_changes": ref_changes}
        summary: dict[str, Any]
        try:
            from deepdiff import DeepDiff

            diff = DeepDiff(ver1.config_snapshot, ver2.config_snapshot, ignore_order=True)
            identical = identical and not bool(diff)
            details["config_changes"] = json.loads(diff.to_json())
            summary = {
                "values_changed": len(diff.get("values_changed", {})),
                "dictionary_item_added": len(diff.get("dictionary_item_added", {})),
                "dictionary_item_removed": len(diff.get("dictionary_item_removed", {})),
                "type_changes": len(diff.get("type_changes", {})),
                "reference_changes": len(ref_changes),
            }
        except ImportError:
            identical = identical and ver1.config_snapshot == ver2.config_snapshot
            details["config_changes"] = {}
            summary = {"error": "deepdiff not installed", "reference_changes": len(ref_changes)}

        return {"v1": v1, "v2": v2, "identical": identical, "summary": summary, "details": details}

    async def delete_version(self, agent_id: uuid.UUID, version: int) -> None:
        """Soft-delete a version, unless a constraint forbids it.

        Refused: the currently published version, and any version a
        channel pins in ``pinned`` bind mode.
        """
        agent = await self._get_agent(agent_id)
        record = await self._get_version(agent.id, version)
        if record is None:
            raise ValueError(f"Version {version} not found for agent {agent_id}")
        if agent.published_version == version:
            raise ValueError(f"Version {version} is published and cannot be deleted")

        from hecate.models.channels import ChannelModel

        pinned = await self.db.execute(
            select(ChannelModel).where(
                ChannelModel.agent_id == agent.id,
                ChannelModel.bind_mode == "pinned",
                ChannelModel.pinned_version == version,
                ~ChannelModel.deleted,
            )
        )
        channel = pinned.scalar_one_or_none()
        if channel is not None:
            raise ValueError(f"Version {version} is pinned by channel '{channel.name}' and cannot be deleted")

        from datetime import UTC, datetime

        record.deleted = True
        record.deleted_at = datetime.now(UTC)
        await self.db.flush()

    # --- resolution seam ---------------------------------------------------

    async def resolve(self, agent_id: uuid.UUID, version: int | None = None) -> ResolvedAgentConfig:
        """Resolve the effective configuration for an invocation.

        ``version=None`` resolves the live draft (studio semantics —
        editors must be able to test unreleased work). A version number
        resolves the frozen snapshot: own config from the snapshot, the
        workflow at its pinned version, unpinned resources left for live
        resolution by the caller.
        """
        if version is None:
            agent = await self._get_agent(agent_id)
            return ResolvedAgentConfig(
                agent_id=agent.id,
                workspace_id=agent.workspace_id,
                source="live",
                version=None,
                config=snapshot_own_config(agent),
            )

        record = await self._get_version(agent_id, version)
        if record is None:
            raise ValueError(f"Version {version} not found for agent {agent_id}")
        pinned_workflow = next(
            (ref for ref in (record.pinned_refs or []) if ref.get("resource_type") == "workflow"),
            None,
        )
        workflow_version = pinned_workflow.get("version") if pinned_workflow else None
        config = dict(record.config_snapshot)
        if pinned_workflow:
            config["workflow_id"] = pinned_workflow.get("resource_id")
        return ResolvedAgentConfig(
            agent_id=record.agent_id,
            workspace_id=record.workspace_id,
            source="version",
            version=record.version,
            config=config,
            workflow_version=workflow_version,
            pinned_refs=list(record.pinned_refs or []),
            ref_manifest=list(record.ref_manifest or []),
        )

    # --- status / drift ------------------------------------------------------

    async def get_version_status(self, agent_id: uuid.UUID) -> dict[str, Any]:
        """Badge state: latest version, published pointer, dirty flag.

        ``has_uncommitted_changes`` compares the live row's content hash
        against the latest snapshot's — no snapshot means "never
        committed", which counts as uncommitted whenever the agent
        exists.
        """
        agent = await self._get_agent(agent_id)
        latest = await self.db.execute(
            select(func.max(AgentVersionModel.version)).where(
                AgentVersionModel.agent_id == agent.id,
                ~AgentVersionModel.deleted,
            )
        )
        latest_version = latest.scalar_one()
        has_changes = True
        if latest_version is not None:
            record = await self._get_version(agent.id, latest_version)
            has_changes = record is None or record.content_hash != canonical_hash(snapshot_own_config(agent))
        return {
            "agent_id": agent.id,
            "latest_version": latest_version,
            "published_version": agent.published_version,
            "has_uncommitted_changes": has_changes,
        }

    async def version_drift(self, agent_id: uuid.UUID, version: int) -> dict[str, Any]:
        """Report manifest entries whose live content differs from the snapshot.

        Knowledge-base entries (``content_hash=None``) are skipped —
        their corpus is mutable data by design. Pinned skill entries
        (``version`` set, 5.9d) are skipped too — a pin freezes its
        content, so there is nothing to drift. Entries without a
        ``version`` field (legacy snapshots) resolve as unpinned.
        """
        record = await self.get_version(agent_id, version, include_snapshot=True)
        drifted: list[dict[str, Any]] = []
        live_manifest = await self._build_ref_manifest(await self._get_agent(agent_id))
        live_by_key = {(entry["resource_type"], str(entry["resource_id"])): entry for entry in live_manifest}
        for entry in record["ref_manifest"]:
            if entry.get("content_hash") is None:
                continue
            if entry.get("version") is not None:
                continue
            key = (entry["resource_type"], str(entry["resource_id"]))
            live = live_by_key.get(key)
            if live is None or live.get("content_hash") != entry.get("content_hash"):
                drifted.append(
                    {
                        "resource_type": entry["resource_type"],
                        "resource_id": entry["resource_id"],
                        "committed_hash": entry.get("content_hash"),
                        "live_hash": (live or {}).get("content_hash"),
                        "missing": live is None,
                    }
                )
        return {"agent_id": uuid.UUID(str(record["agent_id"])), "version": version, "drifted": drifted}

    # --- internals -----------------------------------------------------------

    async def _model_publish_warnings(self, agent: AgentModel) -> list[str]:
        """Warn when the committed model exists in the registry but is unpublished (6.47).

        Models absent from the registry (dynamic/gateway-discovered) carry no
        publish state, so they are silently accepted — the warning is only for
        rows the lifecycle explicitly gates.
        """
        model_name = (agent.model_config_db or {}).get("model")
        if not isinstance(model_name, str) or not model_name:
            return []
        from hecate.models.model_provider import ModelRegistryModel

        result = await self.db.execute(
            select(ModelRegistryModel.is_published).where(
                ModelRegistryModel.model_id == model_name,
                ~ModelRegistryModel.deleted,
            )
        )
        states = result.scalars().all()
        if states and not any(states):
            return [f"Model '{model_name}' is not published"]
        return []

    async def _get_agent(self, agent_id: uuid.UUID) -> AgentModel:
        result = await self.db.execute(select(AgentModel).where(AgentModel.id == agent_id, ~AgentModel.deleted))
        agent = result.scalar_one_or_none()
        if agent is None:
            raise ValueError(f"Agent {agent_id} not found")
        return agent

    async def _get_version(self, agent_id: uuid.UUID, version: int) -> AgentVersionModel | None:
        result = await self.db.execute(
            select(AgentVersionModel).where(
                AgentVersionModel.agent_id == agent_id,
                AgentVersionModel.version == version,
                ~AgentVersionModel.deleted,
            )
        )
        return result.scalar_one_or_none()

    async def refresh_version(self, record: AgentVersionModel) -> None:
        await self.db.refresh(record)

    def _version_read(self, record: AgentVersionModel, published_version: int | None) -> dict[str, Any]:
        return {
            "id": record.id,
            "agent_id": record.agent_id,
            "version": record.version,
            "name": record.name,
            "change_summary": record.change_summary,
            "content_hash": record.content_hash,
            "is_published": published_version == record.version,
            "created_by": record.created_by,
            "workspace_id": record.workspace_id,
            "created_at": record.created_at,
        }

    def _version_detail(self, record: AgentVersionModel, published_version: int | None) -> dict[str, Any]:
        payload = self._version_read(record, published_version)
        payload.update(
            {
                "schema_version": record.schema_version,
                "config_snapshot": record.config_snapshot,
                "pinned_refs": record.pinned_refs or [],
                "ref_manifest": record.ref_manifest or [],
            }
        )
        return payload

    @staticmethod
    def _ref_manifest_diff(v1: AgentVersionModel, v2: AgentVersionModel) -> list[dict[str, Any]]:
        """Manifest deltas between two snapshots (added/removed/content-changed).

        A 5.9d skill entry counts as changed when its pin ``version`` or
        ``content_hash`` moved; entries without the field (legacy shape,
        unversioned resources) compare on hash alone.
        """
        old = {(e.get("resource_type"), str(e.get("resource_id"))): e for e in (v1.ref_manifest or [])}
        new = {(e.get("resource_type"), str(e.get("resource_id"))): e for e in (v2.ref_manifest or [])}
        changes: list[dict[str, Any]] = []
        for key in sorted(set(old) | set(new), key=str):
            o, n = old.get(key), new.get(key)
            if o is not None and n is None:
                changes.append({"change": "removed", "resource_type": key[0], "resource_id": key[1]})
            elif o is None and n is not None:
                changes.append({"change": "added", "resource_type": key[0], "resource_id": key[1]})
            elif (o or {}).get("content_hash") != (n or {}).get("content_hash") or (o or {}).get("version") != (
                n or {}
            ).get("version"):
                changes.append(
                    {
                        "change": "content_changed",
                        "resource_type": key[0],
                        "resource_id": key[1],
                        "v1_hash": (o or {}).get("content_hash"),
                        "v2_hash": (n or {}).get("content_hash"),
                        "v1_version": (o or {}).get("version"),
                        "v2_version": (n or {}).get("version"),
                    }
                )
        return changes

    # Gate glue mirrors the workflow service: lazy imports keep the
    # studio→ops dependency out of module load (test_layering_domain).

    @staticmethod
    def _resolve_gate_config(raw: dict | None):
        from hecate.ops.evaluation.publish_gate import resolve_gate_config as _resolve

        return _resolve(raw)

    async def _evaluate_gate(self, config, candidate_run):
        """Evaluate the publish gate for an agent version.

        Baseline run and live dataset hash are ``None`` — agent
        evaluation runs are not bound to agent versions yet, so the
        regression signal has nothing to compare against and the drift
        signal passes vacuously. ``require_run`` and ``min_pass_rate``
        carry the real v1 gate value.
        """
        from hecate.ops.evaluation.publish_gate import evaluate_gate as _eval

        return await _eval(self.db, config, candidate_run, None, None)

    @staticmethod
    def _gate_report(gate_result, bypassed: bool) -> dict:
        from hecate.ops.evaluation.publish_gate import result_to_report_payload as _render

        return _render(gate_result, bypassed)

    async def _latest_completed_run(self, agent_id: uuid.UUID) -> EvaluationRunModel | None:
        """Most recent completed evaluation run for this agent.

        Runs bind to agents through ``evaluation_tasks`` (``task_id``),
        whose ``config`` JSON carries the owning ``agent_id``. There is
        no relational ``agent_id`` on ``evaluation_runs``.
        """
        from hecate.models.evaluation import EvaluationTaskModel

        result = await self.db.execute(
            select(EvaluationRunModel)
            .join(EvaluationTaskModel, EvaluationRunModel.task_id == EvaluationTaskModel.id)
            .where(
                EvaluationTaskModel.config["agent_id"].as_string() == str(agent_id),
                EvaluationRunModel.status == "completed",
                ~EvaluationRunModel.deleted,
            )
            .order_by(EvaluationRunModel.completed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _record_gate_bypass(
        self,
        agent: AgentModel,
        version: int,
        actor_user_id: uuid.UUID,
        gate_result,
    ) -> None:
        """Persist a require-gate bypass to the audit log (1.3.20)."""
        from hecate.models.audit import AuditLogModel

        failing_signals = [s.name for s in gate_result.signals if not s.passed]
        try:
            self.db.add(
                AuditLogModel(
                    org_id=agent.workspace_id,
                    workspace_id=agent.workspace_id,
                    user_id=actor_user_id,
                    action="AGENT_EVALUATION_GATE_BYPASS",
                    resource_type="agent",
                    resource_id=agent.id,
                    success=True,
                    metadata_={
                        "version": version,
                        "failing_signals": failing_signals,
                        "deterministic_pass_rate": gate_result.deterministic_pass_rate,
                    },
                )
            )
            await self.db.flush()
        except Exception:
            logger.exception("Failed to record agent evaluation gate bypass audit")
