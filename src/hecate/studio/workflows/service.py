"""Workflow service for managing workflow CRUD and versioning.

Provides business logic for workflow operations:
- Create/Read/Update/Delete workflows
- Version management (list, get, rollback)
- Graph DSL validation via GraphCompiler
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import EvaluationRunModel
from hecate.models.workflow import (
    WorkflowCreateSchema,
    WorkflowDetailSchema,
    WorkflowModel,
    WorkflowReadSchema,
    WorkflowUpdateSchema,
    WorkflowVersionModel,
    WorkflowVersionReadSchema,
)
from hecate.runtime.compiler import GraphCompiler
from hecate.studio.workflows.graph_dsl import GraphValidationError, parse_graph

logger = logging.getLogger(__name__)


def _resolve_gate_config(raw: dict | None):
    """Lazy-import the gate config helper to avoid studio→ops at module load.

    The publish evaluation gate is a cross-cutting evaluation feature
    that the studio workflow publish path consumes; the rule
    ``studio/ must not import other domain directories at module
    level`` is enforced by ``test_layering_domain``. Lazy import inside
    a helper keeps the gate composition available while preserving the
    layering invariant.
    """
    from hecate.ops.evaluation.publish_gate import (
        resolve_gate_config as _resolve,
    )

    return _resolve(raw)


async def _evaluate_gate(db, config, candidate_run, baseline_run, live_dataset_hash):
    """Lazy-imported wrapper around ``hecate.ops.evaluation.publish_gate.evaluate_gate``.

    See :func:`_resolve_gate_config` for the layering rationale.
    """
    from hecate.ops.evaluation.publish_gate import evaluate_gate as _eval

    return await _eval(db, config, candidate_run, baseline_run, live_dataset_hash)


async def _live_dataset_hash(db, dataset_id):
    from hecate.ops.evaluation.publish_gate import live_dataset_hash as _hash

    return await _hash(db, dataset_id)


def _gate_to_report_payload(result, bypassed: bool):
    from hecate.ops.evaluation.publish_gate import (
        result_to_report_payload as _render,
    )

    return _render(result, bypassed)


class PublishEvaluationGateBlockedError(Exception):
    """Raised when a require-mode gate rejects a publish attempt (7.3a).

    The exception carries the gate verdict and the full evaluation
    report so the API layer can return them in the 409 response body
    without re-querying.
    """

    def __init__(self, message: str, report: dict, gate_result) -> None:
        super().__init__(message)
        self.report = report
        self.gate_result = gate_result


class WorkflowService:
    """Service for workflow CRUD and version management."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize with a database session.

        Args:
            db: Async SQLAlchemy session for database operations.
        """
        self.db = db
        self.compiler = GraphCompiler()

    async def create_workflow(
        self,
        data: WorkflowCreateSchema,
        workspace_id: uuid.UUID | None = None,
    ) -> WorkflowDetailSchema:
        """Create a new workflow with initial version.

        Args:
            data: Workflow creation data.
            workspace_id: Optional workspace ID (defaults to zero UUID).

        Returns:
            The created workflow with version details.

        Raises:
            GraphValidationError: If the graph DSL is invalid.
        """
        if workspace_id is None:
            workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000000")

        # Validate and compile the graph DSL
        graph_config = parse_graph(data.graph_dsl)
        compiled = self.compiler.compile(graph_config, execution_mode=data.execution_mode)

        # Create workflow
        workflow = WorkflowModel(
            name=data.name,
            workspace_id=workspace_id,
            current_version=1,
            execution_mode=data.execution_mode,
        )
        self.db.add(workflow)
        await self.db.flush()

        # Create initial version
        version = WorkflowVersionModel(
            workflow_id=workflow.id,
            version=1,
            graph_dsl=data.graph_dsl,
            compiled_graph=compiled.to_json(),
            change_summary=data.change_summary or "Initial version",
        )
        self.db.add(version)
        await self.db.flush()

        logger.info(f"Created workflow {workflow.id} with version 1")

        return WorkflowDetailSchema(
            id=workflow.id,
            workspace_id=workflow.workspace_id,
            name=workflow.name,
            current_version=workflow.current_version,
            execution_mode=workflow.execution_mode,
            published_version=workflow.published_version,
            evaluation_gate=workflow.evaluation_gate,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            deleted_at=workflow.deleted_at,
            version=WorkflowVersionReadSchema.model_validate(version),
        )

    async def get_workflow(self, workflow_id: uuid.UUID) -> WorkflowDetailSchema:
        """Get a workflow with its current version.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            The workflow with current version details.

        Raises:
            ValueError: If workflow not found.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Get current version
        version = await self._get_version(workflow_id, workflow.current_version)

        return WorkflowDetailSchema(
            id=workflow.id,
            workspace_id=workflow.workspace_id,
            name=workflow.name,
            current_version=workflow.current_version,
            execution_mode=workflow.execution_mode,
            published_version=workflow.published_version,
            evaluation_gate=workflow.evaluation_gate,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            deleted_at=workflow.deleted_at,
            version=WorkflowVersionReadSchema.model_validate(version) if version else None,
        )

    async def update_workflow(
        self,
        workflow_id: uuid.UUID,
        data: WorkflowUpdateSchema,
    ) -> WorkflowDetailSchema:
        """Update a workflow — name change and/or new version with updated DSL.

        Args:
            workflow_id: UUID of the workflow to update.
            data: Update data.

        Returns:
            The updated workflow.

        Raises:
            ValueError: If workflow not found.
            GraphValidationError: If new graph DSL is invalid.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Update name if provided
        if data.name is not None:
            workflow.name = data.name

        # Update execution_mode if provided
        if data.execution_mode is not None and data.execution_mode != workflow.execution_mode:
            workflow.execution_mode = data.execution_mode
            if data.graph_dsl is None:
                current_version = await self._get_version(workflow_id, workflow.current_version)
                if current_version is not None:
                    graph_config = parse_graph(current_version.graph_dsl)
                    self.compiler.compile(graph_config, execution_mode=workflow.execution_mode)

        # Publish gate (7.3a): an explicitly-sent null clears the gate,
        # omitting the field leaves the stored configuration untouched.
        if "evaluation_gate" in data.model_fields_set:
            workflow.evaluation_gate = data.evaluation_gate.model_dump() if data.evaluation_gate else None

        # Create new version if graph_dsl provided
        if data.graph_dsl is not None:
            graph_config = parse_graph(data.graph_dsl)
            compiled = self.compiler.compile(graph_config, execution_mode=workflow.execution_mode)

            new_version_num = workflow.current_version + 1
            version = WorkflowVersionModel(
                workflow_id=workflow.id,
                version=new_version_num,
                graph_dsl=data.graph_dsl,
                compiled_graph=compiled.to_json(),
                change_summary=data.change_summary or "",
            )
            self.db.add(version)
            workflow.current_version = new_version_num

            logger.info(f"Created version {new_version_num} for workflow {workflow_id}")

        await self.db.flush()

        return await self.get_workflow(workflow_id)

    async def delete_workflow(self, workflow_id: uuid.UUID) -> None:
        """Soft delete a workflow.

        Args:
            workflow_id: UUID of the workflow to delete.

        Raises:
            ValueError: If workflow not found.
        """
        from datetime import UTC, datetime

        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        workflow.deleted = True
        workflow.deleted_at = datetime.now(UTC)
        await self.db.flush()
        logger.info(f"Deleted workflow {workflow_id}")

    async def list_workflows(
        self,
        workspace_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List workflows with pagination.

        Args:
            workspace_id: Optional workspace filter.
            page: Page number (1-indexed).
            page_size: Items per page.

        Returns:
            Dict with 'items' and 'total' keys.
        """
        conditions = [~WorkflowModel.deleted]
        if workspace_id is not None:
            conditions.append(WorkflowModel.workspace_id == workspace_id)

        # Count total
        count_stmt = select(func.count()).select_from(WorkflowModel).where(*conditions)
        total = (await self.db.execute(count_stmt)).scalar_one()

        # Fetch page
        offset = (page - 1) * page_size
        stmt = (
            select(WorkflowModel)
            .where(*conditions)
            .order_by(WorkflowModel.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self.db.execute(stmt)
        workflows = result.scalars().all()

        return {
            "items": [WorkflowReadSchema.model_validate(w) for w in workflows],
            "total": total,
        }

    async def list_versions(self, workflow_id: uuid.UUID) -> list[WorkflowVersionReadSchema]:
        """List all versions of a workflow.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            List of version schemas ordered by version number.
        """
        stmt = (
            select(WorkflowVersionModel)
            .where(
                WorkflowVersionModel.workflow_id == workflow_id,
                ~WorkflowVersionModel.deleted,
            )
            .order_by(WorkflowVersionModel.version.asc())
        )
        result = await self.db.execute(stmt)
        versions = result.scalars().all()

        return [WorkflowVersionReadSchema.model_validate(v) for v in versions]

    async def get_version(
        self,
        workflow_id: uuid.UUID,
        version: int,
    ) -> WorkflowVersionReadSchema:
        """Get a specific version of a workflow.

        Args:
            workflow_id: UUID of the workflow.
            version: Version number.

        Returns:
            The version schema.

        Raises:
            ValueError: If version not found.
        """
        v = await self._get_version(workflow_id, version)
        if v is None:
            raise ValueError(f"Version {version} not found for workflow {workflow_id}")

        return WorkflowVersionReadSchema.model_validate(v)

    async def rollback_to_version(
        self,
        workflow_id: uuid.UUID,
        target_version: int,
    ) -> WorkflowDetailSchema:
        """Rollback a workflow to a specific version.

        Creates a new version with the target version's graph DSL.

        Args:
            workflow_id: UUID of the workflow.
            target_version: Version number to rollback to.

        Returns:
            The updated workflow with new version.

        Raises:
            ValueError: If workflow or target version not found.
        """
        # Get workflow
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Get target version
        target = await self._get_version(workflow_id, target_version)
        if target is None:
            raise ValueError(f"Version {target_version} not found for workflow {workflow_id}")

        # Create new version with target's graph DSL
        new_version_num = workflow.current_version + 1
        version = WorkflowVersionModel(
            workflow_id=workflow.id,
            version=new_version_num,
            graph_dsl=target.graph_dsl,
            compiled_graph=target.compiled_graph,
            change_summary=f"Rollback to version {target_version}",
        )
        self.db.add(version)
        workflow.current_version = new_version_num
        await self.db.flush()

        logger.info(f"Rolled back workflow {workflow_id} to version {target_version} (new version {new_version_num})")

        return await self.get_workflow(workflow_id)

    async def validate_dsl(
        self,
        graph_dsl: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate a graph DSL without persisting (dry-run compile).

        Args:
            graph_dsl: The graph DSL definition to validate.

        Returns:
            Dict with ``valid`` (bool) and optional ``errors`` (list[str]).
        """
        try:
            graph_config = parse_graph(graph_dsl)
            self.compiler.compile(graph_config)
            return {"valid": True, "errors": []}
        except GraphValidationError as e:
            return {"valid": False, "errors": [str(e)]}

    async def _build_evaluation_report(
        self,
        workflow_id: uuid.UUID,
        publishing_version: int,
        previously_published_version: int | None,
        workflow: WorkflowModel | None = None,
        candidate_run_override: EvaluationRunModel | None = None,
        gate_bypassed: bool = False,
        gate_blocking: bool = False,
    ) -> dict | None:
        """Assemble the publish-time evaluation report (7.3 + 7.3a).

        Returns ``None`` when there is no evaluation run for the
        publishing version (the spec says the field is informational —
        publish succeeds either way).

        The report carries a ``gate`` block whenever the workflow has an
        ``evaluation_gate`` configured, and a ``dataset_version`` block
        when the evaluated run was bound to a named dataset version
        (7.3b). When ``gate_blocking`` is ``True`` the gate verdict was
        the reason publish was rejected (so the report surfaces the
        unsatisfied signals even on the 409 path).
        """
        if candidate_run_override is not None:
            latest = candidate_run_override
        else:
            latest = (
                await self.db.execute(
                    select(EvaluationRunModel)
                    .where(
                        EvaluationRunModel.workflow_id == workflow_id,
                        EvaluationRunModel.workflow_version == publishing_version,
                        EvaluationRunModel.status == "completed",
                        ~EvaluationRunModel.deleted,
                    )
                    .order_by(EvaluationRunModel.completed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

        if latest is None:
            no_run_report: dict = {
                "evaluation_status": "no_run_for_version",
                "publishing_version": publishing_version,
            }
            # Carry the gate verdict even when no run exists: with
            # ``gate_bypassed=True`` the marker must travel so callers
            # can distinguish "no data" from "passing/silent".
            if workflow is not None and _resolve_gate_config(workflow.evaluation_gate).mode != "off":
                no_run_report["gate"] = {
                    "mode": _resolve_gate_config(workflow.evaluation_gate).mode,
                    "bypassed_by_force": gate_bypassed,
                    "signals": [],
                    "deterministic_pass_rate": None,
                    "deterministic_metric_averages": {},
                }
            return no_run_report

        report: dict = {
            "run_id": str(latest.id),
            "publishing_version": publishing_version,
            "pass_rate": (latest.summary or {}).get("pass_rate"),
            "consistency_rate": (latest.summary or {}).get("consistency_rate"),
            "metric_averages": (latest.summary or {}).get("metric_averages") or {},
            "threshold": (latest.summary or {}).get("threshold"),
        }

        # 7.3b: name + hash of the bound dataset version, when present.
        if latest.dataset_version_id is not None:
            from hecate.models.evaluation import EvaluationDatasetVersionModel

            version_row = await self.db.get(EvaluationDatasetVersionModel, latest.dataset_version_id)
            if version_row is not None:
                report["dataset_version"] = {
                    "id": str(version_row.id),
                    "name": version_row.name,
                    "content_hash": str(version_row.content_hash),
                }

        # Gate verdict (7.3a) — evaluated unconditionally so the report
        # shows the same numbers whether publish succeeded or was rejected.
        gate_config = _resolve_gate_config(workflow.evaluation_gate if workflow is not None else None)
        if gate_config.mode != "off":
            baseline_run = None
            if previously_published_version is not None and previously_published_version != publishing_version:
                baseline_run = (
                    await self.db.execute(
                        select(EvaluationRunModel)
                        .where(
                            EvaluationRunModel.workflow_id == workflow_id,
                            EvaluationRunModel.workflow_version == previously_published_version,
                            EvaluationRunModel.status == "completed",
                            ~EvaluationRunModel.deleted,
                        )
                        .order_by(EvaluationRunModel.completed_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            live_hash = await _live_dataset_hash(self.db, latest.dataset_id) if latest.dataset_id else None
            gate_result = await _evaluate_gate(
                self.db,
                gate_config,
                latest,
                baseline_run,
                live_hash,
            )
            report["gate"] = _gate_to_report_payload(gate_result, bypassed=gate_bypassed)
            if gate_blocking:
                report["gate"]["verdict"] = "rejected"

        if previously_published_version is not None and previously_published_version != publishing_version:
            prev = (
                await self.db.execute(
                    select(EvaluationRunModel)
                    .where(
                        EvaluationRunModel.workflow_id == workflow_id,
                        EvaluationRunModel.workflow_version == previously_published_version,
                        EvaluationRunModel.status == "completed",
                        ~EvaluationRunModel.deleted,
                    )
                    .order_by(EvaluationRunModel.completed_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if prev is not None:
                prev_avg = (prev.summary or {}).get("metric_averages") or {}
                cand_avg = report["metric_averages"]
                comparison: list[dict] = []
                for metric, cand in cand_avg.items():
                    base = prev_avg.get(metric)
                    if base is None:
                        continue
                    comparison.append(
                        {
                            "metric": metric,
                            "baseline_avg": base,
                            "candidate_avg": cand,
                            "delta": cand - base,
                            "is_regression": cand < base * 0.95,
                        }
                    )
                report["comparison_to_published"] = {
                    "baseline_run_id": str(prev.id),
                    "baseline_version": previously_published_version,
                    "metrics": comparison,
                }
            else:
                report["comparison_to_published"] = {"evaluation_status": "no_baseline"}
        else:
            report["comparison_to_published"] = {"evaluation_status": "no_baseline"}

        return report

    async def publish_version(
        self,
        workflow_id: uuid.UUID,
        version: int,
        *,
        force: bool = False,
        actor_user_id: uuid.UUID | None = None,
    ) -> WorkflowDetailSchema:
        """Publish a specific version to production.

        Sets the published_version pointer and manages the "production"
        label. With a configured ``evaluation_gate`` in ``mode=require``,
        any unsatisfied enabled signal raises
        :class:`PublishEvaluationGateBlockedError` carrying the gate
        verdict and the full evaluation report. ``force=True`` overrides
        the gate and is recorded in the audit log with the acting user.

        Args:
            workflow_id: UUID of the workflow.
            version: Version number to publish.
            force: Bypass a require-mode gate verdict; ignored otherwise.
            actor_user_id: Acting user for the audit entry recorded when
                a bypass is taken.

        Returns:
            The updated workflow with the evaluation report attached.

        Raises:
            ValueError: If workflow or version not found.
            PublishEvaluationGateBlockedError: If the require-mode gate
                rejects the publish.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")

        target = await self._get_version(workflow_id, version)
        if target is None:
            raise ValueError(f"Version {version} not found for workflow {workflow_id}")

        gate_config = _resolve_gate_config(workflow.evaluation_gate)
        # Re-evaluate the gate BEFORE any mutation so a rejection leaves
        # the workflow in its previous state. ``force`` is honored only
        # when the verdict would otherwise block.
        gate_result = None
        gate_blocking = False
        if gate_config.mode != "off":
            candidate_run = await self._latest_completed_run(workflow_id, version)
            baseline_run = await self._latest_completed_run(workflow_id, workflow.published_version)
            live_hash = (
                await _live_dataset_hash(self.db, candidate_run.dataset_id)
                if candidate_run and candidate_run.dataset_id
                else None
            )
            gate_result = await _evaluate_gate(self.db, gate_config, candidate_run, baseline_run, live_hash)
            gate_blocking = gate_result.blocking
            if gate_blocking and not force:
                # Build the report payload from the rejected state so
                # the 409 envelope carries the gate + report without
                # any persistence side-effect.
                report = await self._build_evaluation_report(
                    workflow_id=workflow_id,
                    publishing_version=version,
                    previously_published_version=workflow.published_version,
                    workflow=workflow,
                    candidate_run_override=candidate_run,
                    gate_bypassed=False,
                    gate_blocking=True,
                )
                raise PublishEvaluationGateBlockedError("publish rejected by evaluation_gate", report, gate_result)

        all_versions = await self.list_versions(workflow_id)
        for v in all_versions:
            if "production" in v.labels and v.version != version:
                ver_model = await self._get_version(workflow_id, v.version)
                if ver_model is not None:
                    ver_model.labels = [lbl for lbl in ver_model.labels if lbl != "production"]

        if "production" not in (target.labels or []):
            target.labels = list(target.labels or []) + ["production"]

        previous_published = workflow.published_version
        workflow.published_version = version
        await self.db.flush()

        if gate_blocking and force and actor_user_id is not None:
            await self._record_gate_bypass(
                workflow=workflow,
                version=version,
                actor_user_id=actor_user_id,
                gate_result=gate_result,
            )

        logger.info(f"Published version {version} for workflow {workflow_id}")

        detail = await self.get_workflow(workflow_id)
        detail.evaluation_report = await self._build_evaluation_report(
            workflow_id=workflow_id,
            publishing_version=version,
            previously_published_version=previous_published,
            workflow=workflow,
            candidate_run_override=await self._latest_completed_run(workflow_id, version),
            gate_bypassed=bool(gate_blocking and force),
            gate_blocking=False,
        )
        return detail

    async def _latest_completed_run(
        self,
        workflow_id: uuid.UUID,
        workflow_version: int | None,
    ) -> EvaluationRunModel | None:
        """Fetch the most recent completed run for a workflow version."""
        if workflow_version is None:
            return None
        result = await self.db.execute(
            select(EvaluationRunModel)
            .where(
                EvaluationRunModel.workflow_id == workflow_id,
                EvaluationRunModel.workflow_version == workflow_version,
                EvaluationRunModel.status == "completed",
                ~EvaluationRunModel.deleted,
            )
            .order_by(EvaluationRunModel.completed_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _record_gate_bypass(
        self,
        workflow: WorkflowModel,
        version: int,
        actor_user_id: uuid.UUID,
        gate_result,
    ) -> None:
        """Persist a require-gate bypass to the audit log (7.3a).

        Writes via the request-scoped session so the audit row commits
        with the rest of the publish transaction — the bypass must not
        outlive the publish it belongs to. A standalone audit-store
        fallback covers callers without an open session.
        """
        from hecate.models.audit import AuditLogModel

        failing_signals = [s.name for s in gate_result.signals if not s.passed]
        try:
            self.db.add(
                AuditLogModel(
                    org_id=workflow.workspace_id,
                    workspace_id=workflow.workspace_id,
                    user_id=actor_user_id,
                    action="WORKFLOW_EVALUATION_GATE_BYPASS",
                    resource_type="workflow",
                    resource_id=workflow.id,
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
            logger.exception("Failed to record evaluation gate bypass audit")

    async def get_version_by_label(
        self,
        workflow_id: uuid.UUID,
        label: str,
    ) -> WorkflowVersionReadSchema | None:
        """Find a version by label.

        Args:
            workflow_id: UUID of the workflow.
            label: Label to search for.

        Returns:
            The matching version or None.
        """
        versions = await self.list_versions(workflow_id)
        for v in versions:
            if label in (v.labels or []):
                return v
        return None

    async def get_published_version(
        self,
        workflow_id: uuid.UUID,
    ) -> WorkflowVersionReadSchema:
        """Get the currently published version.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            The published version schema.

        Raises:
            ValueError: If workflow not found or no published version.
        """
        result = await self.db.execute(
            select(WorkflowModel).where(
                WorkflowModel.id == workflow_id,
                ~WorkflowModel.deleted,
            )
        )
        workflow = result.scalar_one_or_none()
        if workflow is None:
            raise ValueError(f"Workflow {workflow_id} not found")
        if workflow.published_version is None:
            raise ValueError(f"Workflow {workflow_id} has no published version")

        return await self.get_version(workflow_id, workflow.published_version)

    async def diff_versions(
        self,
        workflow_id: uuid.UUID,
        v1: int,
        v2: int,
    ) -> dict[str, Any]:
        """Compare two versions' graph DSL.

        Args:
            workflow_id: UUID of the workflow.
            v1: First version number.
            v2: Second version number.

        Returns:
            Dict with diff summary and details.

        Raises:
            ValueError: If either version not found.
        """
        ver1 = await self._get_version(workflow_id, v1)
        if ver1 is None:
            raise ValueError(f"Version {v1} not found for workflow {workflow_id}")
        ver2 = await self._get_version(workflow_id, v2)
        if ver2 is None:
            raise ValueError(f"Version {v2} not found for workflow {workflow_id}")

        try:
            from deepdiff import DeepDiff

            diff = DeepDiff(ver1.graph_dsl, ver2.graph_dsl, ignore_order=True)
            import json

            return {
                "v1": v1,
                "v2": v2,
                "identical": not bool(diff),
                "summary": {
                    "values_changed": len(diff.get("values_changed", {})),
                    "dictionary_item_added": len(diff.get("dictionary_item_added", {})),
                    "dictionary_item_removed": len(diff.get("dictionary_item_removed", {})),
                    "type_changes": len(diff.get("type_changes", {})),
                },
                "details": json.loads(diff.to_json()),
            }
        except ImportError:
            return {
                "v1": v1,
                "v2": v2,
                "identical": ver1.graph_dsl == ver2.graph_dsl,
                "summary": {"error": "deepdiff not installed"},
                "details": {},
            }

    async def _get_version(
        self,
        workflow_id: uuid.UUID,
        version: int,
    ) -> WorkflowVersionModel | None:
        """Internal helper to get a specific version.

        Args:
            workflow_id: UUID of the workflow.
            version: Version number.

        Returns:
            The WorkflowVersionModel or None if not found.
        """
        stmt = select(WorkflowVersionModel).where(
            WorkflowVersionModel.workflow_id == workflow_id,
            WorkflowVersionModel.version == version,
            ~WorkflowVersionModel.deleted,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
