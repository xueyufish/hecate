"""A2A JSON-RPC request handler."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.a2a.server.executor import HecateAgentExecutor
from hecate.a2a.server.task_store import DatabaseTaskStore
from hecate.a2a.types import (
    Message,
    Task,
    TaskState,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class A2ARequestHandler:
    """Handles A2A JSON-RPC requests (SendMessage, GetTask, CancelTask)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._executor = HecateAgentExecutor(db)
        self._task_store = DatabaseTaskStore(db)

    async def handle_send_message(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle SendMessage JSON-RPC request.

        Args:
            params: Request params with "message" dict.

        Returns:
            JSON-RPC result with Task object.
        """
        import uuid

        message_data = params.get("message", {})
        message = Message(
            role=message_data.get("role", "user"),
            parts=message_data.get("parts", []),
        )

        task_id = str(uuid.uuid4())
        context_id = str(uuid.uuid4())

        # Save initial task
        task = Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.SUBMITTED),
            history=[message],
        )
        await self._task_store.save_task(task)

        # Update to working state
        task.status = TaskStatus(state=TaskState.WORKING)
        await self._task_store.save_task(task)

        # Execute
        result_task = await self._executor.execute(message, task_id, context_id)

        # Save final state
        await self._task_store.save_task(result_task)

        return {"task": self._task_to_dict(result_task)}

    async def handle_get_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle GetTask JSON-RPC request.

        Args:
            params: Request params with "id" field.

        Returns:
            JSON-RPC result with Task object or error.
        """
        task_id = params.get("id")
        if not task_id:
            return {"error": {"code": -32602, "message": "Missing task ID"}}

        task = await self._task_store.get_task(task_id)
        if task is None:
            return {"error": {"code": -32001, "message": f"Task {task_id} not found"}}

        return {"task": self._task_to_dict(task)}

    async def handle_cancel_task(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle CancelTask JSON-RPC request.

        Args:
            params: Request params with "id" field.

        Returns:
            JSON-RPC result with canceled Task or error.
        """
        task_id = params.get("id")
        if not task_id:
            return {"error": {"code": -32602, "message": "Missing task ID"}}

        task = await self._task_store.get_task(task_id)
        if task is None:
            return {"error": {"code": -32001, "message": f"Task {task_id} not found"}}

        # Check if task is in terminal state
        if task.status.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED):
            return {"error": {"code": -32002, "message": f"Task {task_id} already in terminal state"}}

        # Cancel the task
        task.status = TaskStatus(state=TaskState.CANCELED)
        await self._task_store.save_task(task)

        return {"task": self._task_to_dict(task)}

    def _task_to_dict(self, task: Task) -> dict[str, Any]:
        """Convert Task to JSON-serializable dict."""
        return {
            "id": task.id,
            "contextId": task.context_id,
            "status": {
                "state": task.status.state.value,
                "message": task.status.message.__dict__ if task.status.message else None,
            },
            "artifacts": [
                {
                    "artifactId": a.artifact_id,
                    "name": a.name,
                    "parts": a.parts,
                }
                for a in task.artifacts
            ],
            "history": [
                {
                    "role": m.role,
                    "parts": m.parts,
                    "messageId": m.message_id,
                }
                for m in task.history
            ],
            "metadata": task.metadata,
        }
