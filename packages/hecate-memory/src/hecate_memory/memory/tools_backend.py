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
    CAP_CROSS_THREAD,
    CAP_FORGET_MEMORY,
    CAP_SEARCH_MEMORIES,
    CAP_SEARCH_RECALL,
    CAP_TASK_MEMORY,
    CAP_UPDATE_MEMORY,
    TIER_2,
    TIER_4,
    TIER_5,
    TIER_UNSUPPORTED_ERROR,
    VALID_TIERS,
    MemoryFactHit,
    TierRoutingError,
    provider_supports,
    resolve_memory_provider,
    route_search_by_tier,
)
from hecate.core.config import settings as core_settings
from hecate.models.memory import MemoryEditLogModel
from hecate_memory.memory.access import record_access
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
        # 4.21 surfaces — gated on ``REFLECTION_ENABLED`` at the seeding
        # layer; tools_backend executes them whenever the runtime
        # reaches this code path because the registry only mounts
        # them when the flag is on.
        "reflection_search",
        "work_context_query",
    }
)

# Allowed values for ``memory_add.scope``. ``actor_scoped`` is the default
# for backwards compatibility; ``workspace_shared`` is restricted to
# workspace admin callers (Group 8.2 / task-memory capability).
MEMORY_ADD_SCOPE_ACTOR_SCOPED = "actor_scoped"
MEMORY_ADD_SCOPE_WORKSPACE_SHARED = "workspace_shared"
_MEMORY_ADD_SCOPES: frozenset[str] = frozenset({MEMORY_ADD_SCOPE_ACTOR_SCOPED, MEMORY_ADD_SCOPE_WORKSPACE_SHARED})

# Tier literals accepted by ``memory_search(tier=...)``. Default is
# ``tier_2`` (existing L3 + L4 behavior); ``tier_4`` routes to
# task-memory / reflections, ``tier_5`` routes to cross-thread
# shared facts. Other tier values are rejected as structured errors.
_MEMORY_SEARCH_TIER_DEFAULT = TIER_2


def get_memory_tool_names() -> frozenset[str]:
    """Return the set of tool names handled by the memory backend."""
    return _MEMORY_TOOL_NAMES


def get_visible_memory_tool_names(*, reflection_enabled: bool | None = None) -> frozenset[str]:
    """Return the tool names that should be seeded under the current flag state.

    ``reflection_enabled`` defaults to ``settings.REFLECTION_ENABLED``;
    callers may pass an explicit value for tests. When ``False``,
    ``reflection_search`` and ``work_context_query`` are withheld so
    the agent cannot mount them.
    """
    flag = core_settings.REFLECTION_ENABLED if reflection_enabled is None else reflection_enabled
    if flag:
        return _MEMORY_TOOL_NAMES
    return _MEMORY_TOOL_NAMES - {"reflection_search", "work_context_query"}


_SUMMARY_LIMIT = 200


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
                return await self._search(workspace_id, agent_id, session_id, args)
            if name == "memory_add":
                return await self._add(workspace_id, agent_id, session_id, args, context)
            if name == "memory_update":
                return await self._update(workspace_id, agent_id, session_id, args, context)
            if name == "memory_forget":
                return await self._forget(workspace_id, agent_id, session_id, args, context)
            if name == "conversation_search":
                return await self._conversation_search(workspace_id, agent_id, args)
            if name == "reflection_search":
                return await self._reflection_search_tool(workspace_id=workspace_id, agent_id=agent_id, args=args)
            if name == "work_context_query":
                return await self._work_context_query_tool(workspace_id=workspace_id, agent_id=agent_id, args=args)
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

    async def _search(
        self,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        session_id: uuid.UUID | None,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            return _err("invalid_arguments", "memory_search requires query")
        top_k = min(int(args.get("top_k", 5)), 20)
        tags = args.get("tags")
        # Tier routing — added in 4.21. ``tier_2`` is the default
        # (L3 + L4 fact memory) and preserves the pre-change behavior
        # byte-identical. Other tiers are routed via the provider's
        # capability declaration; an incapable provider yields a
        # structured error rather than a crash.
        tier = str(args.get("tier", _MEMORY_SEARCH_TIER_DEFAULT))
        if tier not in VALID_TIERS:
            return _err(
                TIER_UNSUPPORTED_ERROR,
                f"unknown tier {tier!r}; expected one of {sorted(VALID_TIERS)}",
                tier=tier,
            )
        provider = resolve_memory_provider()
        if provider is None:
            return _err("no_provider", "no memory provider resolved")
        try:
            route_search_by_tier(provider, tier, provider_name=type(provider).__name__)
        except TierRoutingError as e:
            return _err(
                TIER_UNSUPPORTED_ERROR,
                f"memory provider does not declare capability for tier {tier!r}",
                tier=e.requested_tier,
                required_capability=e.required_capability,
            )

        if tier == TIER_2:
            provider_or_err = self._provider(CAP_SEARCH_MEMORIES)
            if isinstance(provider_or_err, dict):
                return provider_or_err
            hits: list[MemoryFactHit] = await provider_or_err.search_memories(
                query=query,
                workspace_id=workspace_id,
                agent_id=agent_id,
                top_k=top_k,
                tags=[str(t) for t in tags] if tags else None,
            )
            await record_access(
                self.db,
                workspace_id=workspace_id,
                hits=[(h.source_layer, h.memory_id) for h in hits],
                session_id=session_id,
            )
            return {
                "ok": True,
                "tier": tier,
                "source_scope_default": "actor_scoped",
                "results": [
                    {
                        "memory_id": str(h.memory_id),
                        "source_layer": h.source_layer,
                        "content": h.content,
                        "score": round(h.score, 4),
                        "revision": h.revision,
                        "breakdown": h.metadata.get("breakdown", {}),
                    }
                    for h in hits
                ],
            }
        if tier == TIER_4:
            return await self._reflection_search(workspace_id=workspace_id, agent_id=agent_id, args=args)
        if tier == TIER_5:
            return await self._cross_thread_search(workspace_id=workspace_id, args=args)
        # Should be unreachable: VALID_TIERS covers the three handled
        # cases above. Defensive fallback: structured error.
        return _err(
            TIER_UNSUPPORTED_ERROR,
            f"tier {tier!r} not handled by memory_search",
            tier=tier,
        )

    async def _reflection_search(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Tier-4 reflection_search: approved reflections only.

        Tool-level helper invoked by ``memory_search(tier='tier_4')``.
        Returns reflections matching the query / ``task_type`` filter;
        ``pending`` / ``rejected`` / ``deprecated`` reflections are
        excluded — the same status filter the ``reflection_search``
        standalone tool enforces. Behavior is byte-identical to the
        standalone tool, both routing through
        ``provider.search_task_memory`` (Group 4 capability surface).
        """
        if not core_settings.REFLECTION_ENABLED:
            return _err(
                "reflection_disabled",
                "REFLECTION_ENABLED is off; tier_4 not available",
            )
        provider = resolve_memory_provider()
        if provider is None or not provider_supports(provider, CAP_TASK_MEMORY):
            return _err(
                TIER_UNSUPPORTED_ERROR,
                "memory provider does not declare capability for tier_4",
                required_capability=CAP_TASK_MEMORY,
            )
        hits = await provider.search_task_memory(
            query=str(args.get("query", "")).strip(),
            workspace_id=workspace_id,
            agent_id=agent_id,
            top_k=min(int(args.get("top_k", 5)), 20),
            task_type=args.get("task_type"),
        )
        return {
            "ok": True,
            "tier": TIER_4,
            "source_scope_default": "reflection",
            "results": [
                {
                    "reflection_id": str(h.hit_id),
                    "kind": str(h.hit_kind),
                    "title": h.title,
                    "content": h.content,
                    "use_cases": list(h.use_cases),
                    "confidence": round(h.confidence, 4),
                    "score": round(h.score, 4),
                }
                for h in hits
            ],
        }

    async def _cross_thread_search(
        self,
        *,
        workspace_id: uuid.UUID,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Tier-5 cross-thread search: workspace_shared / team_scoped facts.

        Tool-level helper invoked by ``memory_search(tier='tier_5')``.
        Walks the namespace dimensions from the tool context; only the
        surfaces the caller is authorized for are returned.
        """
        provider = resolve_memory_provider()
        if provider is None or not provider_supports(provider, CAP_CROSS_THREAD):
            return _err(
                TIER_UNSUPPORTED_ERROR,
                "memory provider does not declare capability for tier_5",
                required_capability=CAP_CROSS_THREAD,
            )
        hits = await provider.search_cross_thread(
            query=str(args.get("query", "")).strip(),
            workspace_id=workspace_id,
            top_k=min(int(args.get("top_k", 5)), 20),
            tags=[str(t) for t in args.get("tags") or []] or None,
        )
        return {
            "ok": True,
            "tier": TIER_5,
            "source_scope_default": "workspace_shared",
            "results": [
                {
                    "memory_id": str(h.memory_id),
                    "source_layer": h.source_layer,
                    "source_scope": h.source_scope,
                    "content": h.content,
                    "score": round(h.score, 4),
                    "revision": h.revision,
                    "tags": list(h.tags),
                    "importance": h.importance,
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
        # 4.23 scope parameter. ``actor_scoped`` (default) preserves
        # the pre-change behavior byte-identical; ``workspace_shared``
        # is restricted to workspace admin callers. The role check
        # runs before any DB write so a rejected scope never produces
        # a half-write.
        scope = str(args.get("scope", MEMORY_ADD_SCOPE_ACTOR_SCOPED))
        if scope not in _MEMORY_ADD_SCOPES:
            return _err(
                "invalid_scope",
                f"memory_add.scope must be one of {sorted(_MEMORY_ADD_SCOPES)}",
                requested=scope,
            )
        if scope == MEMORY_ADD_SCOPE_WORKSPACE_SHARED:
            ctx = context or {}
            caller_role = str(ctx.get("workspace_role") or "editor")
            if caller_role != "admin":
                return _err(
                    "permission_denied",
                    "memory_add(scope='workspace_shared') requires workspace admin role",
                    caller_role=caller_role,
                )
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
            "scope": scope,
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

    async def _reflection_search_tool(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Standalone ``reflection_search`` tool entry point.

        Distinct from the ``memory_search(tier='tier_4')`` shim only in
        ergonomics: callers get a typed response without needing to
        remember the tier keyword. Both paths share the same
        provider call and the same approved-only status filter.
        """
        if not core_settings.REFLECTION_ENABLED:
            return _err(
                "reflection_disabled",
                "REFLECTION_ENABLED is off; reflection_search not available",
            )
        provider = resolve_memory_provider()
        if provider is None or not provider_supports(provider, CAP_TASK_MEMORY):
            return _err(
                "tier_unsupported",
                "memory provider does not declare capability for task_memory",
                required_capability=CAP_TASK_MEMORY,
            )
        query = str(args.get("query", "")).strip()
        if not query:
            return _err("invalid_arguments", "reflection_search requires query")
        hits = await provider.search_task_memory(
            query=query,
            workspace_id=workspace_id,
            agent_id=agent_id,
            top_k=min(int(args.get("top_k", 5)), 20),
            task_type=args.get("task_type"),
        )
        return {
            "ok": True,
            "results": [
                {
                    "reflection_id": str(h.hit_id),
                    "kind": str(h.hit_kind),
                    "title": h.title,
                    "content": h.content,
                    "use_cases": list(h.use_cases),
                    "confidence": round(h.confidence, 4),
                    "score": round(h.score, 4),
                }
                for h in hits
            ],
        }

    async def _work_context_query_tool(
        self,
        *,
        workspace_id: uuid.UUID,
        agent_id: uuid.UUID,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """Standalone ``work_context_query`` tool entry point."""
        if not core_settings.REFLECTION_ENABLED:
            return _err(
                "reflection_disabled",
                "REFLECTION_ENABLED is off; work_context_query not available",
            )
        provider = resolve_memory_provider()
        if provider is None or not provider_supports(provider, CAP_TASK_MEMORY):
            return _err(
                "tier_unsupported",
                "memory provider does not declare capability for task_memory",
                required_capability=CAP_TASK_MEMORY,
            )
        from hecate_memory.memory.work_context_graph import get_active_node

        node_type = args.get("node_type")
        query = str(args.get("query", "")).strip()
        if not query:
            return _err("invalid_arguments", "work_context_query requires query")
        top_k = min(int(args.get("top_k", 5)), 20)
        # The provider's ``search_task_memory`` returns both reflections
        # and work-context nodes; ``work_context_query`` filters to
        # nodes only via the ``hit_kind`` discriminator.
        hits = await provider.search_task_memory(
            query=query,
            workspace_id=workspace_id,
            agent_id=agent_id,
            top_k=top_k,
            task_type=None,
        )
        nodes_only = [h for h in hits if str(h.hit_kind) == "work_context_node"]
        # Filter by node_type client-side; the builtin provider's
        # future implementation may push this into the SQL query
        # (Group 6 follow-up).
        if node_type:
            nodes_only = [h for h in nodes_only if (h.title or "").startswith(node_type + ":")]
        # active-only filter — rely on the provider's status gate;
        # belt-and-suspenders check via get_active_node per result.
        out: list[dict[str, Any]] = []
        for h in nodes_only:
            node = await get_active_node(
                self.db,
                workspace_id=workspace_id,
                agent_id=agent_id,
                linked_reflection_id=h.hit_id,  # best-effort: hit_id == reflection_id when kind=node
            )
            if node is None:
                continue
            out.append(
                {
                    "node_id": str(node.id),
                    "node_type": node.node_type,
                    "content": node.content,
                    "success_rate": round(node.success_rate, 4),
                    "usage_count": node.usage_count,
                    "user_correction_count": node.user_correction_count,
                    "source_reliability": node.source_reliability,
                    "score": round(h.score, 4),
                }
            )
        return {"ok": True, "results": out}

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
                    target_id=target_id,
                    revision_before=(revision_after - 1) if revision_after else None,
                    revision_after=revision_after,
                    before_summary=_truncate(before),
                    after_summary=_truncate(after),
                )
            )
            await self.db.flush()
        except Exception:
            logger.exception("Failed to write memory edit audit row for %s", tool_name)
