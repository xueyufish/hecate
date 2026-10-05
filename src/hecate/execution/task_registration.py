"""Recover platform Task/Run records from a committed durable submission."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.contracts.execution.identity import IdentityChain
from hecate.contracts.execution.references import BackendRef
from hecate.execution.task_run_registry import TaskNotFoundError, TaskRunRegistry
from hecate.models.run import RunModel
from hecate.models.task import TaskModel


async def ensure_submission_registration(
    db: AsyncSession, task_ref: BackendRef, payload: dict[str, Any], *, sql_store: bool
) -> tuple[TaskModel, RunModel]:
    """Idempotently materialize the frozen submission; do not mint a retry."""
    if sql_store:
        from hecate_durable.storage.models import TaskStateRow

        await db.execute(
            select(TaskStateRow)
            .where(
                TaskStateRow.task_issuer == task_ref.issuer_domain,
                TaskStateRow.task_id == task_ref.id,
            )
            .with_for_update()
        )
    registry = TaskRunRegistry(db)
    workspace = uuid.UUID(payload["workspace_id"])
    chain = IdentityChain.from_dict(payload["identity_chain"])
    try:
        task = await registry.get_task(uuid.UUID(task_ref.id), workspace)
    except TaskNotFoundError:
        task = await registry.create_task(
            task_id=uuid.UUID(task_ref.id),
            goal=payload["goal"],
            initiator_ref=chain.to_dict(),
            workspace_id=workspace,
        )
    try:
        run = await registry.get_run(uuid.UUID(payload["platform_run_id"]), workspace)
    except TaskNotFoundError:
        run = await registry.create_run(
            task_id=task.id,
            workspace_id=workspace,
            deployment_id=uuid.UUID(payload["deployment_id"]),
            identity_chain=chain,
            backend_run_ref=BackendRef.from_dict(payload["backend_run_ref"]),
            run_id=uuid.UUID(payload["platform_run_id"]),
        )
    await db.commit()
    return task, run
