"""Channel invocation and IM webhook endpoints.

- ``POST /v1/channels/{channel_id}/chat/completions`` (1.3.20) — invoke a
  publishing channel: the version resolves through the channel's
  ``bind_mode`` (``published`` tracks the latest publish, ``pinned``
  serves the locked version), with an explicit ``X-Agent-Version`` header
  or ``?version=`` override for debugging.
- ``POST /v1/channels/{name}/webhook`` — inbound messages from Feishu,
  Slack, and future IM platforms. The endpoint resolves the channel
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
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.channel.api.v1.agents import AgentChatCompletionRequest, _request_to_chat_request
from hecate.channel.api.v1.chat import _process_chat
from hecate.channel.im.message_bus import IMMessageBus
from hecate.channel.publishing import ChannelPublishingService
from hecate.core.auth_context import AuthContext
from hecate.core.deps import get_db
from hecate.core.deps_event_store import get_event_store
from hecate.core.deps_state_store import get_session_state_store
from hecate.core.deps_workspace import get_auth_context
from hecate.core.plugin.registry import PluginRegistry
from hecate.models.agent import AgentModel
from hecate.runtime.eventstore import EventStore
from hecate.runtime.session_state import SessionStateStore

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


@router.post("/{channel_id}/chat/completions", response_model=None)
async def channel_chat_completion(
    channel_id: uuid.UUID,
    request: AgentChatCompletionRequest,
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    event_store: Annotated[EventStore, Depends(get_event_store)],
    session_state_store: Annotated[SessionStateStore, Depends(get_session_state_store)],
    http_request: Request,
    version: Annotated[int | None, Query(ge=1)] = None,
    x_agent_version: Annotated[str | None, Header(alias="X-Agent-Version")] = None,
):
    """Invoke a publishing channel (1.3.20).

    The version resolves through the channel's ``bind_mode``:
    ``published`` tracks the agent's latest publish; ``pinned`` serves
    the locked version. An explicit override — ``X-Agent-Version`` header
    or ``?version=`` query parameter — pins this one call to a specific
    committed version for debugging; unknown versions return 404.

    Body shape is identical to ``POST /v1/chat/completions`` (OpenAI
    compatible, streaming supported). The snapshot's chat-relevant fields
    (model, persona, tools, guardrail config) are authoritative; the
    body's ``model`` field is ignored.
    """
    override = version
    if override is None and x_agent_version:
        try:
            override = int(x_agent_version)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "INVALID_VERSION_HEADER",
                        "message": f"X-Agent-Version must be an integer, got {x_agent_version!r}",
                        "details": None,
                    }
                },
            ) from None

    try:
        target = await ChannelPublishingService(db).resolve_target(channel_id, override)
    except ValueError as e:
        message = str(e)
        if "not found" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "NOT_FOUND", "message": message, "details": None}},
            ) from e
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": {"code": "CHANNEL_NOT_INVOKABLE", "message": message, "details": None}},
        ) from e

    # Lazy: studio is a sibling domain — cross-domain access is
    # function-level only (no module-level structural coupling).
    from hecate.studio.agents.versioning import AgentVersionService

    try:
        resolved = await AgentVersionService(db).resolve(target.agent_id, target.resolved_version)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "NOT_FOUND", "message": str(e), "details": None}},
        ) from e

    agent = _transient_agent_from_resolved(resolved)
    chat_request = _request_to_chat_request(request)
    dlp_scanner = getattr(http_request.app.state, "dlp_scanner", None)

    if request.session_id:
        try:
            from hecate.studio.session_lock import session_lock_manager

            async with session_lock_manager.acquire(request.session_id):
                return await _process_chat(
                    chat_request,
                    db,
                    ctx.user_id,
                    ctx.workspace_id,
                    event_store,
                    session_state_store,
                    dlp_scanner,
                    preloaded_agent=agent,
                )
        except TimeoutError:
            raise HTTPException(
                status_code=status.HTTP_408_REQUEST_TIMEOUT,
                detail={
                    "error": {
                        "code": "QUEUE_TIMEOUT",
                        "message": "Message timed out waiting in queue. Please try again.",
                        "details": None,
                    }
                },
            ) from None
    return await _process_chat(
        chat_request,
        db,
        ctx.user_id,
        ctx.workspace_id,
        event_store,
        session_state_store,
        dlp_scanner,
        preloaded_agent=agent,
    )


def _transient_agent_from_resolved(resolved: Any) -> AgentModel:
    """Build a detached AgentModel from a resolved version snapshot.

    ``_process_chat`` reads plain attributes off the agent (id, persona,
    model_config, tools, guardrail_config) — a detached instance carrying
    the snapshot's frozen values satisfies it without touching the live
    row.
    """
    cfg = resolved.config
    return AgentModel(
        id=resolved.agent_id,
        workspace_id=resolved.workspace_id,
        name=cfg.get("name", ""),
        persona=cfg.get("persona"),
        model_config_db=cfg.get("model_config") or {},
        mode=cfg.get("mode", "chat"),
        tools=cfg.get("tools") or [],
        skills=cfg.get("skills") or [],
        skill_ids=cfg.get("skill_ids") or [],
        knowledge_base_ids=cfg.get("knowledge_base_ids") or [],
        risk_level=cfg.get("risk_level", "LOW"),
        opening_remarks=cfg.get("opening_remarks"),
        enable_suggestions=cfg.get("enable_suggestions", True),
        guardrail_config=cfg.get("guardrail_config"),
    )


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
    db: Annotated[AsyncSession, Depends(get_db)],
    name: str = Path(..., description="Channel adapter name (e.g., 'feishu', 'slack')"),
) -> JSONResponse:
    """Receive an inbound webhook from an IM platform.

    1.3.20: the message is routed through the im-type publishing channel
    whose ``config.provider`` matches the adapter name. When no channel
    row is configured (or it resolves to no version), the message is
    rejected — logged with the instance identity and never executed.
    There is deliberately no default-agent fallback.
    """
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

    # 1.3.20: resolve the publishing channel for this IM instance. Kept at
    # 200 OK — non-2xx makes IM platforms retry, and a missing channel is
    # a configuration state, not a transient delivery failure.
    from hecate.channel.publishing import ChannelPublishingService

    target = await ChannelPublishingService(db).resolve_im_route(name)
    if target is None:
        logger.error(
            "IM message rejected: no publishing channel configured for provider '%s' "
            "(channel_type=im, provider=%s). Configure an im channel row for this instance.",
            name,
            name,
        )
        return JSONResponse(status_code=200, content={"ok": False, "error": "no channel configured"})

    chat_id = str(canonical.metadata.get("chat_id") or canonical.metadata.get("channel_id") or "")
    capabilities = adapter.capabilities
    try:
        await bus.enqueue(
            canonical_message=canonical,
            adapter=adapter,
            workspace_id=canonical.metadata.get("workspace_id"),
            chat_id=chat_id,
            channel_capabilities=capabilities,
            agent_id=target.agent_id,
            agent_version=target.resolved_version,
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
