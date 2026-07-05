"""A2A FastAPI router with AgentCard and JSON-RPC endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.a2a.server.auth import verify_a2a_auth
from hecate.a2a.server.card import generate_agent_card
from hecate.a2a.server.handler import A2ARequestHandler
from hecate.a2a.server.streaming import task_to_status_event
from hecate.core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/.well-known/agent-card.json")
async def get_agent_card() -> JSONResponse:
    """Serve the A2A AgentCard at the well-known URL."""
    card = generate_agent_card()
    return JSONResponse(
        content={
            "name": card.name,
            "description": card.description,
            "version": card.version,
            "url": card.url,
            "capabilities": card.capabilities,
            "skills": card.skills,
            "securitySchemes": card.security_schemes,
            "defaultInputModes": card.default_input_modes,
            "defaultOutputModes": card.default_output_modes,
        }
    )


@router.post("/a2a/")
async def handle_jsonrpc(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[str | None, Depends(verify_a2a_auth)] = None,
) -> Response:
    """Handle A2A JSON-RPC 2.0 requests.

    Supports methods:
    - SendMessage: Execute agent and return task
    - GetTask: Retrieve task by ID
    - CancelTask: Cancel a working task
    - SendStreamingMessage: Execute with SSE streaming
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
        )

    method = body.get("method")
    params = body.get("params", {})
    request_id = body.get("id")

    handler = A2ARequestHandler(db)

    if method == "SendMessage":
        result = await handler.handle_send_message(params)
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})

    if method == "GetTask":
        result = await handler.handle_get_task(params)
        if "error" in result:
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": result["error"]})
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})

    if method == "CancelTask":
        result = await handler.handle_cancel_task(params)
        if "error" in result:
            return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "error": result["error"]})
        return JSONResponse(content={"jsonrpc": "2.0", "id": request_id, "result": result})

    if method == "SendStreamingMessage":
        return await _handle_streaming(params, handler)

    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    )


async def _handle_streaming(params: dict[str, Any], handler: A2ARequestHandler) -> StreamingResponse:
    """Handle SendStreamingMessage with SSE response."""

    async def event_generator():
        # Send initial working status
        yield task_to_status_event(
            __import__("hecate.a2a.types", fromlist=["Task"]).Task(
                id="streaming",
                context_id="streaming",
                status=__import__("hecate.a2a.types", fromlist=["TaskStatus"]).TaskStatus(
                    state=__import__("hecate.a2a.types", fromlist=["TaskState"]).TaskState.WORKING
                ),
            )
        )

        # Execute the task
        result = await handler.handle_send_message(params)
        task_data = result.get("task", {})

        # Send final status
        yield task_to_status_event(
            __import__("hecate.a2a.types", fromlist=["Task"]).Task(
                id=task_data.get("id", "unknown"),
                context_id=task_data.get("contextId", "unknown"),
                status=__import__("hecate.a2a.types", fromlist=["TaskStatus"]).TaskStatus(
                    state=__import__("hecate.a2a.types", fromlist=["TaskState"]).TaskState(
                        task_data.get("status", {}).get("state", "completed")
                    )
                ),
            ),
            final=True,
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
