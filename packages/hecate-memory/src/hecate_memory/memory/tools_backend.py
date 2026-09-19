"""Agent-facing memory tool backend.

Implements the execution side of the eight memory tools
(``memory_replace`` / ``memory_insert`` / ``memory_rethink`` / ``memory_search``
/ ``memory_add`` / ``memory_update`` / ``memory_forget`` / ``conversation_search``)
on top of the tiered ``MemoryProvider`` contract plus the platform-native L1
memory blocks.

Split of responsibilities:

- L1 block edits are platform-native (``memory_blocks`` table) and do not go
  through the provider contract — third-party memory backends own facts and
  recall, never the in-context blocks.
- L3/L4 facts and conversation recall route through the resolved
  ``MemoryProvider`` (``provider_supports`` gates the call); a backend that
  does not declare a capability yields a structured tool error, never a crash.
- Every successful mutation writes one ``MemoryEditLogModel`` audit row. Audit
  rows live on the request-scoped session (committed with the request) and are
  unreachable from any memory tool.

Tool errors are returned as ``{"ok": False, "error": ...}`` dicts instead of
raised exceptions so the model can read and react to them (e.g. retry with a
disambiguated ``old_string``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core.composition.memory_provider import (
    CAP_ADD_MEMORY,
    CAP_FORGET_MEMORY,
    CAP_SEARCH_MEMORIES,
    CAP_SEARCH_RECALL,
    CAP_UPDATE_MEMORY,
    MemoryFactHit,
    provider_supports,
    resolve_memory_provider,
)
from hecate.models.memory import MemoryEditLogModel
from hecate_memory.memory.token_counter import TokenCounter
from hecate_memory.memory.working_memory import WorkingMemoryService

logger = logging.getLogger(__name__)

_MEMORY_TOOL_NAMES = frozenset(
    {
        "memory_replace",
        "memory_insert",
        "memory_rethink",
        "memory_search",
        "memory_add",
        "memory_update",
        "memory_forget",
        "conversation_search",
    }
)

_SUMMARY_LIMIT = 200


def get_memory_tool_names() -> frozenset[str]:
    """Return the set of memory tool names handled by this backend."""
    return _MEMORY_TOOL_NAMES


def _err(error: str, detail: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": error, "detail": detail}
    payload.update(extra)
    return payload


def _truncate(text: str | None) -> str | None:
    if not text:
        return None
    return text.replace("\n", " ").strip()[:_SUMMARY_LIMIT]


class MemoryToolBackend:
    """Executes memory tools for ``BuiltInToolExecutor``.

    Args:
        db: Request-scoped session for L1 block edits and audit rows; committed
            by the surrounding request lifecycle (same contract as the file
            tools' workspace writes).
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._token_counter = TokenCounter()

    async def execute(self, name: str, args: dict[str, Any], context: dict[str, Any] | None) -> Any:
        ids = self._ids(context)
        if isinstance(ids, dict):
            return ids  # structured scope error
        workspace_id, agent_id, session_id = ids

        try:
            if name == "memory_replace":
                return await self._block_replace(workspace_id, agent_id, session_id, args, context)
            if name == "memory_insert":
                return await self._block_insert(workspace_id, agent_id, session_id, args, context)
            if name == "memory_rethink":
                return await self._block_rethink(workspace_id, agent_id, session_id, args, context)
            if name == "memory_search":
                return await self._search(workspace_id, agent_id, args)
            if name == "memory_add":
                return await self._add(workspace_id, agent_id, session_id, args, context)
            if name == "memory_update":
                return await self._update(workspace_id, agent_id, session_id, args, context)
            if name == "memory_forget":
                return await self._forget(workspace_id, agent_id, session_id, args, context)
            if name == "conversation_search":
                return await self._conversation_search(workspace_id, agent_id, args)
        except Exception as e:  # defensive: the tool boundary must not crash the loop
            logger.exception("Memory tool %s failed", name)
            return _err("internal_error", str(e))
        return _err("unknown_tool", f"{name} is not a memory tool")

    # -- identity ----------------------------------------------------------------

    def _ids(self, context: dict[str, Any] | None) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID | None] | dict[str, Any]:
        """Parse workspace/agent/session identifiers from the tool context.

        Fail-closed: memory tools refuse to act without a workspace and agent
        scope rather than guessing an implicit tenant.
        """
        ctx = context or {}
        raw_ws = ctx.get("workspace_id")
        raw_agent = ctx.get("agent_id")
        if not raw_ws or not raw_agent:
            return _err(
                "missing_scope",
                "memory tools require workspace_id and agent_id in the tool execution context",
            )
        try:
            workspace_id = raw_ws if isinstance(raw_ws, uuid.UUID) else uuid.UUID(str(raw_ws))
            agent_id = raw_agent if isinstance(raw_agent, uuid.UUID) else uuid.UUID(str(raw_agent))
            raw_session = ctx.get("session_id")
            session_id = uuid.UUID(str(raw_session)) if raw_session else None
        except ValueError as e:
            return _err("invalid_scope", f"malformed scope identifier: {e}")
        return workspace_id, agent_id, session_id

    # -- L1 block edits -----------------------------------------------------------

    async def _block_replace(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        label = str(args.get("label", ""))
        old_string = args.get("old_string")
        new_string = args.get("new_string")
        if not label or old_string is None or new_string is None:
            return _err("invalid_arguments", "memory_replace requires label, old_string and new_string")

        block = await WorkingMemoryService(self.db).get_block_by_label(agent_id, workspace_id, label)
        if block is None:
            return _err("block_not_found", f"memory block '{label}' does not exist")

        content = block.content
        count = content.count(old_string)
        if count == 0:
            return _err("old_string_not_found", f"'{str(old_string)[:80]}' does not appear in memory block '{label}'")
        if count > 1:
            return _err(
                "ambiguous_match",
                f"'{str(old_string)[:80]}' appears {count} times in '{label}'; provide a longer unique string",
                matches=count,
            )

        updated = content.replace(old_string, new_string, 1)
        limit_error = self._limit_error(block, updated)
        if limit_error is not None:
            return limit_error
        block.content = updated
        block.revision += 1
        await self.db.flush()
        await self._audit(
            "memory_replace",
            "l1_block",
            block.id,
            block.revision,
            content,
            updated,
            workspace_id,
            agent_id,
            session_id,
            context,
        )
        return {"ok": True, "label": label, "content": updated, "revision": block.revision}

    async def _block_insert(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        label = str(args.get("label", ""))
        insert_text = args.get("insert_text")
        if not label or insert_text is None:
            return _err("invalid_arguments", "memory_insert requires label and insert_text")
        insert_text = str(insert_text)
        first_line = insert_text.strip().splitlines()[0].lstrip() if insert_text.strip() else ""
        if first_line.isdigit():
            return _err("line_number_prefix", "insert_text must not carry line-number prefixes")

        block = await WorkingMemoryService(self.db).get_block_by_label(agent_id, workspace_id, label)
        if block is None:
            return _err("block_not_found", f"memory block '{label}' does not exist")

        lines = block.content.splitlines()
        try:
            insert_line = int(args.get("insert_line", -1))
        except (TypeError, ValueError):
            return _err("invalid_arguments", "insert_line must be an integer")

        if insert_line == 0:
            updated = insert_text + ("\n" + block.content if block.content else "")
        elif insert_line < 0 or insert_line >= len(lines):
            updated = (block.content + "\n" + insert_text) if block.content else insert_text
        else:
            lines.insert(insert_line, insert_text)
            updated = "\n".join(lines)

        limit_error = self._limit_error(block, updated)
        if limit_error is not None:
            return limit_error
        before = block.content
        block.content = updated
        block.revision += 1
        await self.db.flush()
        await self._audit(
            "memory_insert",
            "l1_block",
            block.id,
            block.revision,
            before,
            updated,
            workspace_id,
            agent_id,
            session_id,
            context,
        )
        return {"ok": True, "label": label, "content": updated, "revision": block.revision}

    async def _block_rethink(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        label = str(args.get("label", ""))
        new_content = args.get("new_content")
        if not label or new_content is None:
            return _err("invalid_arguments", "memory_rethink requires label and new_content")

        block = await WorkingMemoryService(self.db).get_block_by_label(agent_id, workspace_id, label)
        if block is None:
            return _err(
                "block_not_found",
                f"memory block '{label}' does not exist (blocks are not auto-created)",
            )

        new_content = str(new_content)
        limit_error = self._limit_error(block, new_content)
        if limit_error is not None:
            return limit_error
        before = block.content
        block.content = new_content
        block.revision += 1
        await self.db.flush()
        await self._audit(
            "memory_rethink",
            "l1_block",
            block.id,
            block.revision,
            before,
            new_content,
            workspace_id,
            agent_id,
            session_id,
            context,
        )
        return {"ok": True, "label": label, "content": new_content, "revision": block.revision}

    def _limit_error(self, block: Any, new_content: str) -> dict[str, Any] | None:
        """Explicit over-limit error (never silently truncate the block)."""
        tokens = self._token_counter.count_text(new_content)
        if tokens > block.limit:
            return _err(
                "block_limit_exceeded",
                (
                    f"edit would push memory block '{block.label}' to ~{tokens} tokens "
                    f"(limit {block.limit}); consolidate the block instead of growing it"
                ),
                current_tokens=tokens,
                limit=block.limit,
            )
        return None

    # -- L3/L4 facts & recall (through the provider contract) ---------------------

    def _provider(self, capability: str) -> Any | dict[str, Any]:
        provider = resolve_memory_provider()
        if provider is None:
            return _err("no_memory_backend", "no memory backend is configured")
        if not provider_supports(provider, capability):
            return _err("unsupported_by_backend", f"the active memory backend does not support {capability}")
        return provider

    async def _search(self, workspace_id: uuid.UUID, agent_id: uuid.UUID, args: dict[str, Any]) -> dict[str, Any]:
        provider_or_err = self._provider(CAP_SEARCH_MEMORIES)
        if isinstance(provider_or_err, dict):
            return provider_or_err
        query = str(args.get("query", "")).strip()
        if not query:
            return _err("invalid_arguments", "memory_search requires query")
        top_k = min(int(args.get("top_k", 5)), 20)
        tags = args.get("tags")
        hits: list[MemoryFactHit] = await provider_or_err.search_memories(
            query=query,
            workspace_id=workspace_id,
            agent_id=agent_id,
            top_k=top_k,
            tags=[str(t) for t in tags] if tags else None,
        )
        return {
            "ok": True,
            "results": [
                {
                    "memory_id": str(h.memory_id),
                    "source_layer": h.source_layer,
                    "content": h.content,
                    "score": round(h.score, 4),
                    "revision": h.revision,
                }
                for h in hits
            ],
        }

    async def _add(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        provider_or_err = self._provider(CAP_ADD_MEMORY)
        if isinstance(provider_or_err, dict):
            return provider_or_err
        content = str(args.get("content", "")).strip()
        if not content:
            return _err("invalid_arguments", "memory_add requires content")
        result = await provider_or_err.add_memory(
            content=content,
            workspace_id=workspace_id,
            agent_id=agent_id,
            tags=args.get("tags"),
            importance=float(args.get("importance", 0.5)),
        )
        if not result.ok:
            return _err(result.error or "write_failed", "memory_add failed", **result.metadata)
        deduplicated = bool(result.metadata.get("deduplicated", False))
        if not deduplicated:
            # A dedup hit only bumps access_count — no memory-content change,
            # so no audit row (same rule as search access bumps).
            await self._audit(
                "memory_add",
                "knowledge_memory",
                result.memory_id,
                result.revision,
                None,
                content,
                workspace_id,
                agent_id,
                session_id,
                context,
            )
        return {
            "ok": True,
            "memory_id": str(result.memory_id),
            "revision": result.revision,
            "deduplicated": deduplicated,
        }

    async def _update(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        provider_or_err = self._provider(CAP_UPDATE_MEMORY)
        if isinstance(provider_or_err, dict):
            return provider_or_err
        try:
            memory_id = uuid.UUID(str(args.get("memory_id")))
        except (ValueError, TypeError):
            return _err("invalid_arguments", "memory_update requires a valid memory_id")

        patch: dict[str, Any] = {}
        if args.get("content") is not None:
            patch["content"] = str(args["content"])
        if args.get("tags") is not None:
            patch["tags"] = [str(t) for t in args["tags"]]
        if args.get("importance") is not None:
            patch["importance"] = float(args["importance"])
        if not patch:
            return _err("invalid_arguments", "memory_update requires at least one field to change")

        expected_revision = args.get("expected_revision")
        result = await provider_or_err.update_memory(
            memory_id=memory_id,
            workspace_id=workspace_id,
            patch=patch,
            expected_revision=int(expected_revision) if expected_revision is not None else None,
            agent_id=agent_id,
        )
        if not result.ok:
            return _err(result.error or "write_failed", "memory_update failed", **result.metadata)
        layer = str(result.metadata.get("layer", "knowledge_memory"))
        new_content = str(patch.get("content", ""))
        await self._audit(
            "memory_update",
            layer,
            result.memory_id,
            result.revision,
            None,
            new_content or f"fields={sorted(patch)}",
            workspace_id,
            agent_id,
            session_id,
            context,
        )
        return {"ok": True, "memory_id": str(result.memory_id), "revision": result.revision}

    async def _forget(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
        context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        provider_or_err = self._provider(CAP_FORGET_MEMORY)
        if isinstance(provider_or_err, dict):
            return provider_or_err
        try:
            memory_id = uuid.UUID(str(args.get("memory_id")))
        except (ValueError, TypeError):
            return _err("invalid_arguments", "memory_forget requires a valid memory_id")

        result = await provider_or_err.forget_memory(
            memory_id=memory_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            expected_revision=int(args["expected_revision"]) if args.get("expected_revision") is not None else None,
        )
        if not result.ok:
            return _err(result.error or "write_failed", "memory_forget failed", **result.metadata)
        layer = str(result.metadata.get("layer", "knowledge_memory"))
        await self._audit(
            "memory_forget",
            layer,
            result.memory_id,
            result.revision,
            None,
            None,
            workspace_id,
            agent_id,
            session_id,
            context,
        )
        return {"ok": True, "memory_id": str(result.memory_id), "revision": result.revision}

    async def _conversation_search(
        self, workspace_id: uuid.UUID, agent_id: uuid.UUID, args: dict[str, Any]
    ) -> dict[str, Any]:
        provider_or_err = self._provider(CAP_SEARCH_RECALL)
        if isinstance(provider_or_err, dict):
            return provider_or_err
        query = str(args.get("query", "")).strip()
        if not query:
            return _err("invalid_arguments", "conversation_search requires query")
        try:
            limit = min(int(args.get("limit", 5)), 20)
        except (TypeError, ValueError):
            return _err("invalid_arguments", "limit must be an integer")

        def _opt_date(key: str) -> Any:
            raw = args.get(key)
            if raw is None:
                return None
            try:
                return datetime.fromisoformat(str(raw))
            except ValueError:
                return _err("invalid_arguments", f"{key} must be an ISO-8601 datetime")

        start_date = _opt_date("start_date")
        if isinstance(start_date, dict):
            return start_date
        end_date = _opt_date("end_date")
        if isinstance(end_date, dict):
            return end_date

        roles = args.get("roles")
        exclude_raw = args.get("exclude_session_ids") or []
        try:
            exclude = [uuid.UUID(str(s)) for s in exclude_raw]
        except (ValueError, TypeError):
            return _err("invalid_arguments", "exclude_session_ids must contain valid session UUIDs")

        page = await provider_or_err.search_recall(
            query=query,
            workspace_id=workspace_id,
            agent_id=agent_id,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            roles=[str(r) for r in roles] if roles else None,
            cursor=args.get("cursor"),
            exclude_session_ids=exclude,
        )
        return {
            "ok": True,
            "results": [
                {
                    "recall_id": str(h.recall_id),
                    "session_id": str(h.session_id),
                    "conversation_id": str(h.conversation_id) if h.conversation_id else None,
                    "role": h.role,
                    "content": h.content,
                    "timestamp": h.timestamp.isoformat(),
                    "score": round(h.score, 4),
                }
                for h in page.hits
            ],
            "next_cursor": page.next_cursor,
            "low_signal": page.low_signal,
        }

    # -- audit ---------------------------------------------------------------------

    async def _audit(
        self,
        tool_name: str,
        target_type: str,
        target_id: uuid.UUID | None,
        revision_after: int | None,
        before: str | None,
        after: str | None,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        context: dict[str, Any] | None,
    ) -> None:
        """Append one audit row for a memory mutation.

        Audit failures never fail the tool call — they are logged for
        monitoring; the mutation itself is already durable.
        """
        try:
            self.db.add(
                MemoryEditLogModel(
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    trace_id=(context or {}).get("trace_id"),
                    tool_name=tool_name,
                    target_type=target_type,
                    target_id=target_id,  # type: ignore[arg-type]
                    revision_before=(revision_after - 1) if revision_after else None,
                    revision_after=revision_after,
                    before_summary=_truncate(before),
                    after_summary=_truncate(after),
                )
            )
            await self.db.flush()
        except Exception:
            logger.exception("Failed to write memory edit audit row for %s", tool_name)
