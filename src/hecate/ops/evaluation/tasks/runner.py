"""Async execution for offline task runs.

Mirrors the synthesis-job lifecycle (``queued → running → completed/failed``,
:func:`asyncio.create_task` + a fresh session per execution): the API request
persists a ``queued`` run row and commits, then hands off here; the spawned
task reopens the run, executes the task's evaluators against its dataset via
:class:`EvaluationEngine`, and commits the terminal state.

7.3 (Workflow Evaluation) additions: pre-flight cost guardrail check
(``items × repetitions ≤ max_total_executions``), dataset snapshot freeze at
run start, workflow answer-source plumbing into the engine, and dataset
drift summary appended to the run row on completion.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evaluation import (
    EvaluationDatasetVersionModel,
    EvaluationItemModel,
    EvaluationRunModel,
    EvaluationTaskModel,
    RunStatus,
)
from hecate.ops.evaluation.engine import EvaluationEngine, get_evaluator_class
from hecate.ops.evaluation.evaluator import Evaluator
from hecate.ops.evaluation.snapshot import build_snapshot
from hecate.ops.evaluation.types import AnswerSource

logger = logging.getLogger(__name__)


class CostGuardrailExceededError(ValueError):
    """Pre-flight refusal when ``items × repetitions`` exceeds the cap."""


def _items_from_snapshot(entries: list[dict], dataset_id: uuid.UUID) -> list[EvaluationItemModel]:
    """Rehydrate transient item objects from frozen snapshot entries (7.3b).

    The returned instances are never added to a session — the engine only
    reads their attributes and persists scores keyed by the frozen item
    ids. ``generated_answer`` is deliberately not carried (snapshots never
    store it), so the answer source regenerates on every run.
    """
    items: list[EvaluationItemModel] = []
    for entry in entries:
        items.append(
            EvaluationItemModel(
                id=uuid.UUID(str(entry["id"])),
                dataset_id=dataset_id,
                query=str(entry.get("query") or ""),
                expected_answer=entry.get("expected_answer"),
                context=entry.get("context") or [],
                tags=list(entry.get("tags") or []),
                metadata_=dict(entry.get("metadata") or {}),
                known_bad=bool(entry.get("known_bad")),
            )
        )
    return items


class OfflineTaskRunner:
    """Execute offline task runs in the background.

    Args:
        db: Async SQLAlchemy session. As with the synthesis job service,
            the background task opens its own session via
            ``async_session_factory`` so the request's session can close
            cleanly while the run executes.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_in_background(self, run_id: uuid.UUID, task_id: uuid.UUID) -> None:
        """Spawn an asyncio task executing the run; returns immediately."""
        asyncio.create_task(self._execute(run_id, task_id))

    async def _execute(self, run_id: uuid.UUID, task_id: uuid.UUID) -> None:
        from hecate.core.database import async_session_factory  # local import — avoid circular

        if async_session_factory is None:
            logger.error("async_session_factory unavailable — cannot run task run %s", run_id)
            return

        async with async_session_factory() as session:
            run = await session.get(EvaluationRunModel, run_id)
            task = await session.get(EvaluationTaskModel, task_id)
            if run is None or task is None:
                logger.error("Task run %s (task %s) not found", run_id, task_id)
                return

            run.status = RunStatus.RUNNING.value
            run.started_at = datetime.now(UTC)
            await session.flush()

            try:
                evaluators = self._resolve_evaluators(task)
                if not evaluators:
                    raise RuntimeError("no evaluators could be resolved: " + ", ".join(task.evaluator_configs or []))

                config = task.config or {}
                answer_source = AnswerSource(config.get("answer_source") or "manual")
                repetitions = int(config.get("repetitions") or 1)
                max_total = int(config.get("max_total_executions") or 1000)

                # Version-bound run (7.3b): snapshot and execution both come
                # from the frozen named version — the live dataset plays no
                # part, so live edits cannot leak into the run.
                version = None
                if run.dataset_version_id is not None:
                    version = await session.get(EvaluationDatasetVersionModel, run.dataset_version_id)
                    if version is None or version.deleted:
                        raise RuntimeError(f"dataset version {run.dataset_version_id} not found for run {run_id}")

                if version is not None:
                    item_count = len(version.items or [])
                else:
                    item_count = await self._count_items(run.dataset_id)
                if item_count * repetitions > max_total:
                    raise CostGuardrailExceededError(
                        f"cost guardrail: {item_count} items x {repetitions} repetitions = "
                        f"{item_count * repetitions} > max_total_executions={max_total}"
                    )

                if version is not None:
                    frozen_items = list(version.items or [])
                    run.dataset_snapshot = {
                        "items": frozen_items,
                        "hash": str(version.content_hash),
                        "captured_at": datetime.now(UTC).isoformat(),
                        "dataset_version_id": str(version.id),
                        "dataset_version_name": version.name,
                    }
                else:
                    snapshot, snapshot_hash = await self._snapshot_dataset(run.dataset_id)
                    run.dataset_snapshot = {
                        "items": snapshot,
                        "hash": snapshot_hash,
                        "captured_at": datetime.now(UTC).isoformat(),
                    }
                await session.flush()

                engine = EvaluationEngine(session)
                run_kwargs = {
                    "evaluators": evaluators,
                    "dataset_id": run.dataset_id,
                    "answer_source": answer_source,
                    "tags": config.get("tags"),
                    "agent_id": uuid.UUID(str(config["agent_id"])) if config.get("agent_id") else None,
                    "run": run,
                    "summary_config": self._summary_config(task),
                    "repetitions": repetitions,
                }
                if version is not None:
                    run_kwargs["items_override"] = _items_from_snapshot(frozen_items, run.dataset_id)
                if answer_source == AnswerSource.WORKFLOW:
                    run_kwargs["workflow_id"] = uuid.UUID(str(config["workflow_id"]))
                    if config.get("workflow_version") is not None:
                        run_kwargs["workflow_version"] = int(config["workflow_version"])
                    run_kwargs["max_in_flight"] = int(config.get("max_in_flight") or 4)

                await engine.run(**run_kwargs)
                # engine.run marked the run completed; persist its scores + summary
                await self._append_dataset_drift(run, session)
                await session.commit()
            except CostGuardrailExceededError as e:
                logger.warning("Task run %s rejected by cost guardrail: %s", run_id, e)
                run.status = RunStatus.FAILED.value
                run.summary = {
                    "error": "cost_guardrail_exceeded",
                    "message": str(e),
                }
                run.completed_at = datetime.now(UTC)
                await session.commit()
            except Exception:  # noqa: BLE001 — surface error state on the run row
                logger.exception("Task run %s failed", run_id)
                run.status = RunStatus.FAILED.value
                run.completed_at = datetime.now(UTC)
                await session.commit()

    async def _count_items(self, dataset_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(EvaluationItemModel)
            .where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
        )
        return int((await self.db.execute(stmt)).scalar_one() or 0)

    async def _snapshot_dataset(self, dataset_id: uuid.UUID) -> tuple[list[dict], str]:
        """Return a canonical-JSON snapshot of every dataset item + content hash.

        Serialization and hashing live in :mod:`hecate.ops.evaluation.snapshot`
        — shared with named dataset versions (7.3b) so a version's
        ``content_hash`` always equals the hash the same items produce here.
        This is intentionally called after ``CostGuardrailExceededError`` so a
        guardrail-rejected run does not pollute the run's
        ``dataset_snapshot`` column.
        """
        stmt = (
            select(EvaluationItemModel)
            .where(
                EvaluationItemModel.dataset_id == dataset_id,
                ~EvaluationItemModel.deleted,
            )
            .order_by(EvaluationItemModel.created_at.asc(), EvaluationItemModel.id.asc())
        )
        items = list((await self.db.execute(stmt)).scalars().all())
        return build_snapshot(items)

    async def _append_dataset_drift(self, run: EvaluationRunModel, session: AsyncSession) -> None:
        """Compute the post-run dataset hash vs the snapshot hash.

        Writes a ``dataset_drift`` block into ``run.summary`` when the
        hashes diverge. Called by ``_execute`` after the engine finishes
        so the diff reflects the dataset as it stands at run completion
        (the snapshot already locked what the run consumed). Version-bound
        runs (7.3b) never drift: their snapshot hash is the frozen
        version's hash, so a live-edit comparison would be meaningless.
        """
        if run.dataset_snapshot is None:
            return
        if run.dataset_version_id is not None:
            return
        snapshot_hash = run.dataset_snapshot.get("hash")
        if not snapshot_hash:
            return

        # Re-count + re-hash against the live dataset using a fresh read.
        # We use a separate session-bound query because the runner already
        # has an open session by the time we are called.
        _, current_hash = await self._snapshot_dataset(run.dataset_id)

        if current_hash == snapshot_hash:
            return

        snapshot_items = {row["id"] for row in run.dataset_snapshot.get("items", [])}
        live_stmt = select(EvaluationItemModel.id).where(
            EvaluationItemModel.dataset_id == run.dataset_id,
            ~EvaluationItemModel.deleted,
        )
        live_ids = {str(row[0]) for row in (await session.execute(live_stmt)).all()}
        changed = sorted(snapshot_items ^ live_ids)

        summary = dict(run.summary or {})
        summary["dataset_drift"] = {
            "snapshot_hash": snapshot_hash,
            "current_hash": current_hash,
            "changed_item_ids": changed,
        }
        run.summary = summary

    def _resolve_evaluators(self, task: EvaluationTaskModel) -> list[Evaluator]:
        """Instantiate the task's evaluators, skipping unresolvable names.

        Trigger-time validation normally guarantees resolvability; between
        trigger and execution the registry can change (e.g. an optional
        dependency went missing at restart), so unresolvable names are
        skipped and only fail the run when nothing resolves.
        """
        evaluators: list[Evaluator] = []
        for name in task.evaluator_configs or []:
            cls = get_evaluator_class(name)
            if cls is None:
                logger.warning("Task %s references unregistered evaluator %r — skipped", task.id, name)
                continue
            evaluators.append(cls())
        return evaluators

    def _summary_config(self, task: EvaluationTaskModel) -> dict | None:
        """Build the engine ``summary_config`` from the task config.

        Pass/fail needs ``threshold``; regression flags need
        ``baseline_run_id``. Without either, no summary is produced.
        """
        config = task.config or {}
        summary_config: dict = {}
        if config.get("threshold") is not None:
            summary_config["threshold"] = float(config["threshold"])
        if config.get("baseline_run_id"):
            summary_config["baseline_run_id"] = str(config["baseline_run_id"])
        if not summary_config:
            return None
        summary_config["regression_threshold"] = float(config.get("regression_threshold") or 0.05)
        return summary_config
