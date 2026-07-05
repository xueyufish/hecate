"""A2A Client for communicating with remote agents via JSON-RPC."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hecate.a2a.types import (
    Message,
    Task,
    TaskState,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class A2AClient:
    """A2A client for sending messages to remote agents via JSON-RPC 2.0.

    Supports:
    - SendMessage: Execute agent and return task
    - GetTask: Retrieve task by ID
    - CancelTask: Cancel a working task
    """

    def __init__(
        self,
        agent_url: str,
        api_key: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        """Initialize the A2A client.

        Args:
            agent_url: Base URL of the remote A2A agent.
            api_key: Optional API key for authentication.
            timeout: HTTP request timeout in seconds.
        """
        self._agent_url = agent_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers including auth if configured."""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    async def send_message(
        self,
        message: Message,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        """Send a message to the remote agent and return the resulting task.

        Args:
            message: The A2A message to send.
            metadata: Optional metadata for the request.

        Returns:
            Task with execution results.

        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            ValueError: If the response cannot be parsed.
        """
        params = {
            "message": {
                "role": message.role,
                "parts": message.parts,
                "messageId": message.message_id,
            },
        }
        if metadata:
            params["metadata"] = metadata

        result = await self._jsonrpc_call("SendMessage", params)
        return self._parse_task(result.get("task", {}))

    async def get_task(self, task_id: str) -> Task:
        """Retrieve a task by ID from the remote agent.

        Args:
            task_id: The task ID to retrieve.

        Returns:
            Task object.

        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            ValueError: If the task is not found.
        """
        result = await self._jsonrpc_call("GetTask", {"id": task_id})
        return self._parse_task(result.get("task", {}))

    async def cancel_task(self, task_id: str) -> Task:
        """Cancel a task on the remote agent.

        Args:
            task_id: The task ID to cancel.

        Returns:
            Canceled Task object.

        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            ValueError: If the task cannot be canceled.
        """
        result = await self._jsonrpc_call("CancelTask", {"id": task_id})
        return self._parse_task(result.get("task", {}))

    async def _jsonrpc_call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Make a JSON-RPC 2.0 call to the remote agent.

        Args:
            method: The JSON-RPC method name.
            params: The method parameters.

        Returns:
            The result dict from the JSON-RPC response.

        Raises:
            httpx.HTTPStatusError: If the HTTP request fails.
            ValueError: If the response contains an error.
        """
        import uuid

        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._agent_url}/a2a/",
                json=payload,
                headers=self._get_headers(),
            )
            response.raise_for_status()

        data = response.json()

        if "error" in data:
            error = data["error"]
            raise ValueError(f"A2A error {error.get('code')}: {error.get('message')}")

        return data.get("result", {})

    def _parse_task(self, data: dict[str, Any]) -> Task:
        """Parse a task dict into a Task object.

        Args:
            data: Task dict from JSON-RPC response.

        Returns:
            Parsed Task object.
        """
        status_data = data.get("status", {})
        state_str = status_data.get("state", "submitted")

        try:
            state = TaskState(state_str)
        except ValueError:
            state = TaskState.SUBMITTED

        return Task(
            id=data.get("id", ""),
            context_id=data.get("contextId", ""),
            status=TaskStatus(state=state),
            metadata=data.get("metadata", {}),
        )
