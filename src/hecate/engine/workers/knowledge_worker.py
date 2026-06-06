"""Knowledge retrieval worker for querying knowledge bases.

Extracts a query from the messages channel, calls knowledge base search
via EnginePort, and writes retrieved context and system messages to
channel_updates for downstream LLM consumption.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from hecate.engine.ports import EnginePort
from hecate.engine.types import WorkerResult
from hecate.engine.worker import Worker

logger = logging.getLogger(__name__)


class KnowledgeWorker(Worker):
    """Worker that queries knowledge bases via EnginePort and injects results.

    Extracts the search query from the last message in the ``messages`` channel,
    calls ``EnginePort.knowledge_query()`` for the configured KB IDs, and
    writes the retrieved context to channels.
    """

    def __init__(self, port: EnginePort, event_store: Any = None) -> None:
        super().__init__(event_store=event_store)
        self._port = port

    async def execute(
        self,
        node_id: str,
        node_config: dict,
        channel_snapshot: dict,
        execution_context: dict | None = None,
    ) -> WorkerResult:
        kb_ids_raw = node_config.get("kb_ids", [])
        top_k = node_config.get("top_k", 5)

        if not kb_ids_raw:
            return WorkerResult(
                node_id=node_id,
                channel_updates={"messages": [], "context": ""},
            )

        messages = channel_snapshot.get("messages", [])
        query = self._extract_query(messages, node_config)

        try:
            kb_ids = [UUID(kid) if isinstance(kid, str) else kid for kid in kb_ids_raw]
        except (ValueError, AttributeError) as e:
            return WorkerResult(
                node_id=node_id,
                error=ValueError(f"Invalid kb_id format in node '{node_id}': {e}"),
            )

        try:
            chunks = await self._port.knowledge_query(query=query, kb_ids=kb_ids)
        except Exception as e:
            logger.warning("Knowledge retrieval failed for node '%s': %s", node_id, e)
            return WorkerResult(node_id=node_id, error=e)

        truncated = chunks[:top_k]
        context_parts = [c.get("content", "") for c in truncated]

        return WorkerResult(
            node_id=node_id,
            channel_updates={
                "context": "\n\n".join(context_parts),
                "messages": [
                    {
                        "role": "system",
                        "content": f"Retrieved {len(truncated)} documents from {len(kb_ids)} knowledge bases",
                    }
                ],
            },
        )

    def _extract_query(self, messages: list[dict], node_config: dict) -> str:
        """Extract search query from messages or config template.

        Args:
            messages: Channel messages list.
            node_config: Node configuration dict.

        Returns:
            The extracted query string.
        """
        template = node_config.get("query_template", "")
        if template:
            try:
                return template.format(messages=messages)
            except (KeyError, IndexError):
                pass

        if messages:
            last_msg = messages[-1]
            content = last_msg.get("content", "")
            if content:
                return content

        return ""
