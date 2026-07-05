"""A2A push notification webhook receiver."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/a2a/webhook")
async def receive_push_notification(request: Request) -> JSONResponse:
    """Receive A2A push notification from remote agent.

    This endpoint receives webhook callbacks for task status updates
    and artifact deliveries from remote A2A agents.

    Args:
        request: The incoming webhook request.

    Returns:
        JSON response acknowledging receipt.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid JSON"},
        )

    task_id = body.get("taskId")
    event_type = body.get("eventType", "unknown")

    logger.info("Received A2A push notification: task=%s, event=%s", task_id, event_type)

    # TODO: Process the notification (update task store, emit EventBus event)
    # For now, just acknowledge receipt

    return JSONResponse(content={"status": "received", "taskId": task_id})
