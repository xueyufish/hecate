"""IM channel webhook endpoints.

Exposes ``POST /v1/channels/{name}/webhook`` for inbound messages from
Feishu, Slack, and future IM platforms. The endpoint resolves the channel
adapter via the existing ``PluginRegistry`` (type="channel"), validates
the request via the adapter's ``verify_webhook`` hook, dispatches the
inbound payload to the :class:`IMMessageBus`, and returns 200 OK within
the platform's 3-second window.

Signature verification and challenge handling are the adapter's concern,
never the route's (PR5b):

- Feishu (``hecate-channel-feishu``): delegates to ``lark_oapi``'s
  ``handle_webhook_request`` — signature validation, event decryption,
  and URL-verification challenges.
- Slack (``hecate-channel-slack``): implements the signing-secrets ``v0``
  scheme directly (HMAC-SHA256 + replay window); Bolt's request
  middleware never runs on this path.
- Adapters without platform verification inherit the ``(200, {})``
  default and pass through.

Design reference: D7 in ``openspec/changes/multi-channel-feishu-slack/design.md``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse

from hecate.channel.im.message_bus import IMMessageBus
from hecate.core.plugin.registry import PluginRegistry

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

    # Platform-level verification / decryption is the adapter's concern
    # (PR5b): signed or encrypted webhooks override ``verify_webhook``;
    # everyone else inherits the ``(200, {})`` default and passes through.
    # This must run before JSON decoding — signatures cover the raw body,
    # and encrypted payloads are not JSON-decodable until decrypted.
    try:
        verify_status, verify_body = await adapter.verify_webhook(dict(request.headers), raw_body)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook verification failed for channel=%s: %s", name, exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signature verification failed",
        ) from exc
    if verify_status != 200:
        return JSONResponse(status_code=verify_status, content=verify_body or {})

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
