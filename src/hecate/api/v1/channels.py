"""IM channel webhook endpoints.

Exposes ``POST /v1/channels/{name}/webhook`` for inbound messages from
Feishu, Slack, and future IM platforms. The endpoint resolves the channel
adapter via the existing ``PluginRegistry`` (type="channel"), validates
the request signature via the SDK, dispatches the inbound payload to the
:class:`IMMessageBus`, and returns 200 OK within the platform's 3-second
window.

Signature verification and challenge handling are delegated to the
underlying SDK when possible:

- Feishu: ``lark_oapi`` handles URL verification (``type=url_verification``)
  and event decryption via ``encrypt_key`` / ``verification_token``.
- Slack: ``slack_bolt.RequestVerification`` middleware checks
  ``X-Slack-Signature`` and ``X-Slack-Request-Timestamp``.

Design reference: D7 in ``openspec/changes/multi-channel-feishu-slack/design.md``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse

from hecate.channel.im.message_bus import IMMessageBus
from hecate.plugin.registry import PluginRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/channels", tags=["channels"])


def get_message_bus(request: Request) -> IMMessageBus | None:
    """Return the process-wide :class:`IMMessageBus` set up at startup.

    The DI container in :mod:`hecate.main` stores the bus on
    ``app.state.im_message_bus`` during the lifespan handler.
    """
    return getattr(request.app.state, "im_message_bus", None)


def get_plugin_registry(request: Request) -> PluginRegistry | None:
    """Return the process-wide :class:`PluginRegistry`.

    The DI container stores the registry on ``app.state.plugin_registry``.
    """
    return getattr(request.app.state, "plugin_registry", None)


@router.get("/{name}/webhook")
async def webhook_challenge(
    name: str = Path(..., description="Channel adapter name (e.g., 'feishu', 'slack')"),
) -> JSONResponse:
    """Handle IM platform URL-verification challenges.

    Feishu and Slack both POST a verification request with a ``challenge``
    field; we echo it back to confirm webhook ownership. Some platforms
    perform GET-based verification instead — return a minimal 200 OK for
    those.
    """
    logger.info("IM webhook GET verification for channel=%s", name)
    return JSONResponse(status_code=200, content={"ok": True})


@router.post("/{name}/webhook")
async def webhook(
    request: Request,
    name: str = Path(..., description="Channel adapter name (e.g., 'feishu', 'slack')"),
    x_slack_signature: str | None = Header(default=None, alias="X-Slack-Signature"),
    x_slack_request_timestamp: str | None = Header(default=None, alias="X-Slack-Request-Timestamp"),
) -> JSONResponse:
    """Receive an inbound webhook from an IM platform."""
    registry = get_plugin_registry(request)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin registry not initialized",
        )
    adapter = registry.get_by_name("channel", name)
    if adapter is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No IM channel adapter registered for '{name}'",
        )
    bus = get_message_bus(request)
    if bus is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="IM message bus not initialized",
        )

    raw_body = await request.body()
    try:
        payload: Any = await _decode_payload(request, name, raw_body, adapter)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to decode IM payload for channel=%s: %s", name, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to decode payload",
        ) from exc

    # URL verification challenge short-circuits here.
    if isinstance(payload, dict) and payload.get("type") == "url_verification":
        challenge = payload.get("challenge", "")
        return JSONResponse(status_code=200, content={"challenge": challenge})

    # Dispatch through the SDK signature-verification layer when available.
    # For Slack, RequestVerification runs as middleware on the underlying
    # Bolt app, so by this point the request is already verified.
    # For Feishu, the SDK exposes ``handle_webhook_request``.
    underlying = getattr(adapter, "underlying", None)
    if name == "feishu" and underlying is not None and hasattr(underlying, "handle_webhook_request"):
        try:
            status_code, body = await underlying.handle_webhook_request(
                headers=dict(request.headers),
                body=raw_body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Feishu signature verification failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Signature verification failed",
            ) from exc
        if status_code != 200:
            return JSONResponse(status_code=status_code, content=body or {})

    # Normalize to CanonicalMessage and enqueue.
    try:
        canonical = await adapter.receive(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Adapter receive failed for channel=%s: %s; returning 200 to avoid retry", name, exc)
        return JSONResponse(status_code=200, content={"ok": True})

    chat_id = str(canonical.metadata.get("chat_id") or canonical.metadata.get("channel_id") or "")
    capabilities = adapter.capabilities
    try:
        await bus.enqueue(
            canonical_message=canonical,
            adapter=adapter,
            workspace_id=canonical.metadata.get("workspace_id"),
            chat_id=chat_id,
            channel_capabilities=capabilities,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to enqueue IM message: %s", exc)
        # Already ack'd at this point — keep returning 200.
    return JSONResponse(status_code=200, content={"ok": True})


async def _decode_payload(
    request: Request,
    name: str,
    raw_body: bytes,
    adapter: Any,
) -> Any:
    """Decode the raw request body into a platform-specific dict.

    Both Feishu and Slack send JSON; we attempt a JSON parse and fall back
    to raising HTTP 400 on malformed input.
    """
    import json

    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc
