"""A2A Task store using async SQLAlchemy."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.a2a.types import Message, Task, TaskState, TaskStatus
from hecate.models.a2a_task import A2ATaskModel

logger = logging.getLogger(__name__)


class DatabaseTaskStore:
    """A2A task store backed by PostgreSQL via async SQLAlchemy."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def save_task(self, task: Task) -> None:
        """Save or update a task in the database.

        Args:
            task: The A2A task to persist.
        """
        result = await self._db.execute(select(A2ATaskModel).where(A2ATaskModel.task_id == task.id))
        existing = result.scalar_one_or_none()

        if existing:
            existing.state = task.status.state.value
            existing.status_message = task.status.message.__dict__ if task.status.message else None
            existing.artifacts = [a.__dict__ for a in task.artifacts]
            existing.history = [m.__dict__ for m in task.history]
        else:
            model = A2ATaskModel(
                task_id=task.id,
                context_id=task.context_id,
                state=task.status.state.value,
                status_message=task.status.message.__dict__ if task.status.message else None,
                artifacts=[a.__dict__ for a in task.artifacts],
                history=[m.__dict__ for m in task.history],
                metadata_=task.metadata,
            )
            self._db.add(model)

        await self._db.flush()

    async def get_task(self, task_id: str) -> Task | None:
        """Retrieve a task by ID.

        Args:
            task_id: The task ID to look up.

        Returns:
            Task if found, None otherwise.
        """
        result = await self._db.execute(select(A2ATaskModel).where(A2ATaskModel.task_id == task_id))
        model = result.scalar_one_or_none()

        if model is None:
            return None

        status_msg = None
        if model.status_message:
            status_msg = Message(
                role=model.status_message.get("role", "agent"),
                parts=model.status_message.get("parts", []),
                message_id=model.status_message.get("message_id", ""),
            )

        return Task(
            id=model.task_id,
            context_id=model.context_id,
            status=TaskStatus(
                state=TaskState(model.state),
                message=status_msg,
            ),
            artifacts=[],  # Artifacts loaded separately if needed
            history=[],  # History loaded separately if needed
            metadata=model.metadata_ or {},
        )

    async def list_tasks(self, limit: int = 50) -> list[Task]:
        """List recent tasks.

        Args:
            limit: Maximum number of tasks to return.

        Returns:
            List of tasks.
        """
        result = await self._db.execute(
            select(A2ATaskModel)
            .where(A2ATaskModel.deleted.is_(False))
            .order_by(A2ATaskModel.created_at.desc())
            .limit(limit)
        )
        models = result.scalars().all()

        tasks = []
        for model in models:
            status_msg = None
            if model.status_message:
                status_msg = Message(
                    role=model.status_message.get("role", "agent"),
                    parts=model.status_message.get("parts", []),
                )
            tasks.append(
                Task(
                    id=model.task_id,
                    context_id=model.context_id,
                    status=TaskStatus(
                        state=TaskState(model.state),
                        message=status_msg,
                    ),
                    metadata=model.metadata_ or {},
                )
            )
        return tasks
