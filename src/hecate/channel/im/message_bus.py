"""IMMessageBus — asynchronous decoupling between webhook ACK and Agent execution.

IM platforms (Feishu, Slack) require webhooks to return within ~3 seconds.
Agent runs, however, can take many seconds (LLM latency, tool calls,
multi-step orchestration). The MessageBus pattern lets the webhook
endpoint immediately enqueue the inbound :class:`CanonicalMessage` and
return 200 OK, while a background asyncio task consumes the queue, invokes
``WorkflowExecutionService.execute(...)``, and routes the response back via
the originating channel adapter.

Design reference: D5 in ``openspec/changes/multi-channel-feishu-slack/design.md``.

Usage::

    bus = IMMessageBus(workflow_service=workflow_service)
    await bus.start()
    # from the webhook handler:
    await bus.enqueue(
        canonical_message=msg,
        adapter=feishu_adapter,
        workspace_id=workspace_id,
        chat_id=chat_id,
        channel_capabilities=feishu_adapter.capabilities,
    )
    # on shutdown:
    await bus.stop()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hecate.channel.adapter import ChannelBase
from hecate.channel.capabilities import ChannelCapabilities
from hecate.channel.types import CanonicalMessage

logger = logging.getLogger(__name__)


class IMMessageBus:
    """In-process asyncio queue decoupling webhook ACK from Agent execution."""

    def __init__(
        self,
        workflow_service: Any | None = None,
        max_queue_size: int = 1024,
    ) -> None:
        """Args:
        workflow_service: an object exposing an ``execute(messages=..., agent_id=..., ...)``
            coroutine compatible with :class:`WorkflowExecutionService`. The
            service is injected by the DI container at startup. If ``None``,
            :meth:`enqueue` raises until injection occurs (test seam).
        max_queue_size: maximum pending messages. ``enqueue`` waits if full
            to provide natural backpressure; ``asyncio.Queue`` semantics.
        """
        self._workflow_service = workflow_service
        self._queue: asyncio.Queue[_Envelope] | None = None
        self._max_queue_size = max_queue_size
        self._workers: list[asyncio.Task[None]] = []
        self._stop_event: asyncio.Event | None = None
        self._worker_count = 4  # default concurrency

    def attach_workflow_service(self, workflow_service: Any) -> None:
        """Inject the workflow service after construction.

        Useful when DI is configured after the bus is created.
        """
        self._workflow_service = workflow_service

    async def start(self, worker_count: int = 4) -> None:
        """Start the background consumer tasks.

        Safe to call once; calling twice has no effect.
        """
        if self._workers:
            return
        self._queue = asyncio.Queue(maxsize=self._max_queue_size)
        self._stop_event = asyncio.Event()
        self._worker_count = worker_count
        self._workers = [
            asyncio.create_task(self._consume(worker_id=i), name=f"im-message-bus-{i}") for i in range(worker_count)
        ]
        logger.info("IMMessageBus started with %d workers", worker_count)

    async def stop(self) -> None:
        """Stop background consumers and drain remaining messages."""
        if self._stop_event is not None:
            self._stop_event.set()
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.debug("IMMessageBus worker shutdown raised: %s", exc)
        self._workers.clear()
        logger.info("IMMessageBus stopped")

    async def enqueue(
        self,
        canonical_message: CanonicalMessage,
        adapter: ChannelBase,
        workspace_id: Any,
        chat_id: str,
        channel_capabilities: ChannelCapabilities,
        agent_id: Any | None = None,
    ) -> None:
        """Enqueue an inbound message for background processing.

        ``chat_id`` is the IM-platform identifier used by the adapter to
        route the response. ``workspace_id`` scopes the lookup performed by
        the SessionRouter when it is invoked downstream.
        """
        if self._queue is None:
            raise RuntimeError("IMMessageBus.start() must be called before enqueue()")
        envelope = _Envelope(
            message=canonical_message,
            adapter=adapter,
            workspace_id=workspace_id,
            chat_id=chat_id,
            capabilities=channel_capabilities,
            agent_id=agent_id,
        )
        await self._queue.put(envelope)

    async def _consume(self, worker_id: int) -> None:
        """Background consumer loop."""
        if self._queue is None or self._stop_event is None:
            logger.warning("IMMessageBus worker %d started before bus was ready", worker_id)
            return
        queue = self._queue
        stop_event = self._stop_event
        try:
            while not stop_event.is_set():
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                try:
                    await self._process(envelope, worker_id=worker_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "IMMessageBus worker %d failed to process message id=%s channel=%s: %s",
                        worker_id,
                        envelope.message.id,
                        envelope.message.channel_id,
                        exc,
                    )
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            logger.debug("IMMessageBus worker %d cancelled", worker_id)
            raise

    async def _process(self, envelope: _Envelope, worker_id: int) -> None:
        """Dispatch a single envelope to the workflow service.

        Errors are logged at the worker level — never propagated to the
        webhook handler (which has already returned 200 OK).
        """
        if self._workflow_service is None:
            logger.warning(
                "IMMessageBus worker %d: no workflow_service attached; skipping message id=%s",
                worker_id,
                envelope.message.id,
            )
            return
        messages = self._to_messages(envelope.message)
        try:
            result = await self._workflow_service.execute(
                messages=messages,
                agent_id=envelope.agent_id,
                session_id=None,
                channel_id=envelope.message.channel_id,
                channel_capabilities=envelope.capabilities,
                user_id=envelope.message.user_id,
                workspace_id=envelope.workspace_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Workflow execute failed for IM message id=%s channel=%s: %s",
                envelope.message.id,
                envelope.message.channel_id,
                exc,
            )
            await self._safe_notify_error(envelope)
            return
        # Render the result back through the channel adapter.
        await self._route_response(envelope, result)

    @staticmethod
    def _to_messages(message: CanonicalMessage) -> list[dict[str, Any]]:
        """Convert a :class:`CanonicalMessage` to OpenAI-style message dicts.

        The Agent runtime expects a list of role/content dicts; we use the
        inbound message as a single ``user`` turn. Attachments are passed
        through as a flat ``metadata`` for now — full multimodal support
        is deferred to Phase 2.
        """
        text = message.content.text or ""
        return [
            {
                "role": "user",
                "content": text,
                "metadata": {
                    "im_message_id": message.id,
                    "im_channel_id": message.channel_id,
                    "im_user_id": message.user_id,
                    **(message.metadata or {}),
                },
            }
        ]

    async def _route_response(self, envelope: _Envelope, result: Any) -> None:
        """Dispatch the workflow result back to the originating channel."""
        # The workflow service may return a dict, a generator (streaming),
        # or None. MVP path handles the common dict case.
        text = ""
        if isinstance(result, dict):
            text = result.get("text") or result.get("content") or result.get("message") or ""
        elif isinstance(result, str):
            text = result
        if not text:
            logger.info(
                "IMMessageBus: workflow returned no text for message id=%s; skipping reply",
                envelope.message.id,
            )
            return
        chat_id = envelope.chat_id or envelope.message.metadata.get("chat_id")
        channel_id = envelope.message.metadata.get("channel_id") or envelope.message.channel_id
        thread_ts = envelope.message.metadata.get("thread_ts")
        payload: dict[str, Any] = {"text": text}
        if chat_id:
            payload["chat_id"] = chat_id
        elif channel_id:
            payload["channel_id"] = channel_id
        if thread_ts:
            payload["thread_ts"] = thread_ts
        try:
            await envelope.adapter.respond(str(envelope.message.id), payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Adapter respond failed for IM message id=%s channel=%s: %s",
                envelope.message.id,
                envelope.message.channel_id,
                exc,
            )

    async def _safe_notify_error(self, envelope: _Envelope) -> None:
        """Best-effort error notification back to the IM user."""
        try:
            chat_id = envelope.chat_id or envelope.message.metadata.get("chat_id")
            channel_id = envelope.message.metadata.get("channel_id") or envelope.message.channel_id
            payload: dict[str, Any] = {
                "text": "⚠️ Hecate Agent run failed. Please try again later.",
            }
            if chat_id:
                payload["chat_id"] = chat_id
            elif channel_id:
                payload["channel_id"] = channel_id
            await envelope.adapter.respond(str(envelope.message.id), payload)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to send error notification for message id=%s", envelope.message.id)


class _Envelope:
    """Internal envelope passed through the asyncio queue."""

    __slots__ = (
        "agent_id",
        "adapter",
        "capabilities",
        "chat_id",
        "message",
        "workspace_id",
    )

    def __init__(
        self,
        message: CanonicalMessage,
        adapter: ChannelBase,
        workspace_id: Any,
        chat_id: str,
        capabilities: ChannelCapabilities,
        agent_id: Any | None,
    ) -> None:
        self.message = message
        self.adapter = adapter
        self.workspace_id = workspace_id
        self.chat_id = chat_id
        self.capabilities = capabilities
        self.agent_id = agent_id
