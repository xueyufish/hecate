"""Shared entry-side assembly: process-wide stores and tool assembly.

Entry executions (HTTP chat, MCP, IM, A2A, scheduled agents) must observe
one assembly: a single engine event store and a single session-state
(checkpoint) store per process, plus one tool-assembly source. Before
step5 hardening each entry module kept its own module-level singleton and
imported tool helpers from the HTTP chat module, so with the default
in-memory backends the entries silently wrote to different stores.

The application lifecycle registers its lifespan-built instances here
(``core/composition/wiring.attach_state_stores``); entries resolve stores
exclusively through the getters below. Entry modules must not define
their own store singletons or import each other's private assembly
helpers — that rule is enforced by ``tests/test_layering_entry_imports.py``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.tool import ToolModel

logger = logging.getLogger(__name__)
_DEFAULT_WORKSPACE = uuid.UUID(int=0)

_shared_event_store: Any = None
_shared_session_state_store: Any = None


def register_shared_event_store(store: Any) -> None:
    """Register the lifespan-built event store as the process-wide instance.

    The latest registration wins: production builds exactly one store per
    process, while test suites build one per app — re-registration keeps
    every entry reading the current app's store instead of a stale one.
    """
    global _shared_event_store
    if _shared_event_store is not None and _shared_event_store is not store:
        logger.debug("Shared event store replaced by a newer registration")
    _shared_event_store = store


def register_shared_session_state_store(store: Any) -> None:
    """Register the lifespan-built session-state store (see event store note)."""
    global _shared_session_state_store
    if _shared_session_state_store is not None and _shared_session_state_store is not store:
        logger.debug("Shared session-state store replaced by a newer registration")
    _shared_session_state_store = store


def get_shared_event_store() -> Any:
    """The engine event store every entry reads and writes.

    Returns the registered instance when the application lifecycle has
    built one; otherwise lazily builds one from settings for entry paths
    that run outside the app lifespan (e.g. MCP over stdio).
    """
    global _shared_event_store
    if _shared_event_store is None:
        from hecate.core.config import settings
        from hecate.studio.event_state import create_event_store

        _shared_event_store = create_event_store(settings)
    return _shared_event_store


def get_shared_session_state_store() -> Any:
    """The session-state (checkpoint) store every entry reads and writes."""
    global _shared_session_state_store
    if _shared_session_state_store is None:
        from hecate.core.config import settings
        from hecate.studio.session_state import create_session_state_store

        _shared_session_state_store = create_session_state_store(settings)
    return _shared_session_state_store


async def load_agent_tools(
    db: AsyncSession, tool_names: list[str], *, workspace_id: uuid.UUID = _DEFAULT_WORKSPACE
) -> list[dict[str, Any]]:
    """Resolve an agent's configured tools into OpenAI-format definitions.

    Builtin tool names resolve from the in-memory ``BUILTIN_TOOL_DEFINITIONS``;
    any other names are looked up in the ``ToolModel`` table.

    Args:
        db: The async database session.
        tool_names: Tool names configured on the agent.

    Returns:
        Tool definitions formatted for LLM function calling.
    """
    if not tool_names:
        return []
    from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS

    definitions: list[dict[str, Any]] = []
    db_names: list[str] = []
    for name in tool_names:
        if name in BUILTIN_TOOL_DEFINITIONS:
            definitions.append({"name": name, **BUILTIN_TOOL_DEFINITIONS[name]})
        else:
            db_names.append(name)
    if db_names:
        result = await db.execute(
            select(ToolModel).where(
                ToolModel.name.in_(db_names), ToolModel.workspace_id == workspace_id, ~ToolModel.deleted
            )
        )
        for tool in result.scalars().all():
            definitions.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.parameters or {"type": "object", "properties": {}},
                }
            )
    from hecate_llm.tool_calling import format_tools_for_llm

    return format_tools_for_llm(definitions)


def build_tool_registry(
    db: AsyncSession,
    skill_ref_manifest: list[dict[str, Any]] | None = None,
    *,
    workspace_id: uuid.UUID = _DEFAULT_WORKSPACE,
) -> Any:
    """Construct a ToolRegistry wired to builtin + DB tools using app settings.

    Args:
        db: The async database session.
        skill_ref_manifest: Optional resolved skill-ref manifest from the agent.

    Returns:
        A configured ToolRegistry.
    """
    from hecate.core.config import settings
    from hecate.tools.skill.loader import SkillLoader
    from hecate.tools.tool.builtin import BuiltInToolExecutor
    from hecate.tools.tool.registry import ToolRegistry
    from hecate.tools.tool.search.factory import create_search_provider

    search_provider = create_search_provider(
        provider=settings.SEARCH_PROVIDER,
        api_key=settings.SEARCH_API_KEY,
    )
    memory_backend = None
    if settings.MEMORY_TOOLS_ENABLED:
        try:
            from hecate_memory.memory.tools_backend import MemoryToolBackend

            memory_backend = MemoryToolBackend(db)
        except ImportError:
            memory_backend = None
    # 4.21 reflection_tools seeding — when MEMORY_TOOLS_ENABLED is on
    # but REFLECTION_ENABLED is off, the seeding layer excludes
    # ``reflection_search`` and ``work_context_query`` so the agent
    # never sees tools whose backend would refuse the call. Tool
    # definitions live in tools/tool/builtin.py and are also gated by
    # the same visibility check at the registry layer.
    builtin_executor = BuiltInToolExecutor(
        search_provider=search_provider,
        workspace_root=settings.WORKSPACE_ROOT,
        skill_loader=SkillLoader(db, ref_manifest=skill_ref_manifest),
        memory_backend=memory_backend,
    )
    return ToolRegistry(db=db, builtin_executor=builtin_executor, workspace_id=workspace_id)
