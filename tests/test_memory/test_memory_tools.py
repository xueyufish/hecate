"""Tests for the memory tool layer (agent-memory-tools).

Covers the flag-gated seeding, the executor dispatch (fail-closed without a
backend), and the ``MemoryToolBackend`` semantics: L1 block edit errors and
successes, provider-routed L3/L4 tools, and the ``memory_edit_log`` audit
trail.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.core import database as core_db
from hecate.core.config import settings
from hecate.models.memory import MemoryBlockCreateSchema, MemoryEditLogModel
from hecate.tools.tool.builtin import BUILTIN_TOOL_DEFINITIONS, BuiltInToolExecutor, get_memory_tool_names
from hecate.tools.tool.registry import seed_builtin_tools
from hecate.tools.tool.search import SearchProvider
from tests.conftest import test_session_factory

_CTX = {"workspace_id": str(uuid.uuid4()), "agent_id": str(uuid.uuid4()), "session_id": str(uuid.uuid4())}


class _StubSearch(SearchProvider):
    async def search(self, query: str, max_results: int = 5) -> list[dict]:
        return []


def _make_executor(db: AsyncSession, with_backend: bool = True) -> BuiltInToolExecutor:
    backend = None
    if with_backend:
        from hecate_memory.memory.tools_backend import MemoryToolBackend

        backend = MemoryToolBackend(db)
    return BuiltInToolExecutor(
        search_provider=_StubSearch(),
        workspace_root="./workspace",
        memory_backend=backend,
    )


def _bind_provider_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the builtin provider's self-managed sessions at the test DB."""
    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)


# ---------------------------------------------------------------------------
# Seeding gate (3.1)
# ---------------------------------------------------------------------------


async def test_seed_flags_off_seeds_no_memory_tools(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MEMORY_TOOLS_ENABLED", False)
    count = await seed_builtin_tools(db_session)
    assert count >= 0  # non-memory tools still seeded/updated

    from hecate.models.tool import ToolModel

    names = {
        n for (n,) in (await db_session.execute(select(ToolModel.name).where(ToolModel.source == "builtin"))).all()
    }
    assert not (names & get_memory_tool_names())
    # Non-memory builtin tools unaffected.
    assert "web_search" in names


async def test_seed_flags_on_seeds_memory_tools(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MEMORY_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "RECALL_INDEXING_ENABLED", True)
    await seed_builtin_tools(db_session)

    from hecate.models.tool import ToolModel

    names = {
        n for (n,) in (await db_session.execute(select(ToolModel.name).where(ToolModel.source == "builtin"))).all()
    }
    assert get_memory_tool_names() <= names


async def test_conversation_search_gated_by_recall_flag(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MEMORY_TOOLS_ENABLED", True)
    monkeypatch.setattr(settings, "RECALL_INDEXING_ENABLED", False)
    await seed_builtin_tools(db_session)

    from hecate.models.tool import ToolModel

    names = {
        n for (n,) in (await db_session.execute(select(ToolModel.name).where(ToolModel.source == "builtin"))).all()
    }
    assert "memory_search" in names
    assert "conversation_search" not in names


def test_memory_tool_definitions_present() -> None:
    assert get_memory_tool_names() <= set(BUILTIN_TOOL_DEFINITIONS)
    assert BUILTIN_TOOL_DEFINITIONS["conversation_search"]["risk_level"] == "LOW"
    assert BUILTIN_TOOL_DEFINITIONS["memory_rethink"]["risk_level"] == "MEDIUM"


# ---------------------------------------------------------------------------
# Executor dispatch (3.2)
# ---------------------------------------------------------------------------


async def test_executor_without_backend_fails_closed() -> None:
    executor = _make_executor(None, with_backend=False)  # type: ignore[arg-type]
    result = await executor.execute("memory_search", {"query": "x"}, _CTX)
    assert result["ok"] is False
    assert result["error"] == "unavailable"


async def test_executor_without_scope_fails_closed(db_session: AsyncSession) -> None:
    executor = _make_executor(db_session)
    result = await executor.execute("memory_search", {"query": "x"}, {"session_id": "s"})
    assert result["ok"] is False
    assert result["error"] == "missing_scope"


# ---------------------------------------------------------------------------
# L1 block edit semantics (3.3)
# ---------------------------------------------------------------------------


async def _seed_block(db: AsyncSession, agent_id: uuid.UUID, ws: uuid.UUID, label: str = "persona") -> None:
    from hecate_memory.memory.working_memory import WorkingMemoryService

    await WorkingMemoryService(db).create_block(
        agent_id,
        ws,
        MemoryBlockCreateSchema(label=label, content="User likes concise replies.\nUser works in fintech."),
    )


async def test_block_replace_success_and_audit(db_session: AsyncSession) -> None:
    ws = uuid.uuid4()
    agent = uuid.uuid4()
    await _seed_block(db_session, agent, ws)
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent), "session_id": str(uuid.uuid4())}

    result = await executor.execute(
        "memory_replace",
        {"label": "persona", "old_string": "concise replies", "new_string": "structured tables"},
        ctx,
    )
    assert result["ok"] is True
    assert "structured tables" in result["content"]
    assert result["revision"] == 2

    rows = (await db_session.execute(select(MemoryEditLogModel))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tool_name == "memory_replace"
    assert rows[0].target_type == "l1_block"
    assert rows[0].revision_before == 1 and rows[0].revision_after == 2
    assert rows[0].before_summary and rows[0].after_summary


async def test_block_replace_ambiguous_match(db_session: AsyncSession) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    from hecate_memory.memory.working_memory import WorkingMemoryService

    await WorkingMemoryService(db_session).create_block(
        agent, ws, MemoryBlockCreateSchema(label="persona", content="a b a")
    )
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    result = await executor.execute("memory_replace", {"label": "persona", "old_string": "a", "new_string": "c"}, ctx)
    assert result["ok"] is False
    assert result["error"] == "ambiguous_match"
    assert result["matches"] == 2


async def test_block_replace_not_found_and_missing_block(db_session: AsyncSession) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    await _seed_block(db_session, agent, ws)
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    miss = await executor.execute(
        "memory_replace", {"label": "persona", "old_string": "no such text", "new_string": "x"}, ctx
    )
    assert miss["error"] == "old_string_not_found"

    absent = await executor.execute("memory_replace", {"label": "nope", "old_string": "a", "new_string": "b"}, ctx)
    assert absent["error"] == "block_not_found"


async def test_block_limit_exceeded_errors_explicitly(db_session: AsyncSession) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    from hecate_memory.memory.working_memory import WorkingMemoryService

    await WorkingMemoryService(db_session).create_block(
        agent, ws, MemoryBlockCreateSchema(label="persona", content="tiny", limit=1)
    )
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    result = await executor.execute(
        "memory_rethink",
        {"label": "persona", "new_content": "x" * 200},
        ctx,
    )
    assert result["ok"] is False
    assert result["error"] == "block_limit_exceeded"
    assert result["limit"] == 1


async def test_block_insert_top_and_line_number_guard(db_session: AsyncSession) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    await _seed_block(db_session, agent, ws)
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    guarded = await executor.execute("memory_insert", {"label": "persona", "insert_text": "3. something"}, ctx)
    assert guarded["ok"] is True  # "3." is not a bare line number

    top = await executor.execute(
        "memory_insert",
        {"label": "persona", "insert_text": "TOP LINE", "insert_line": 0},
        ctx,
    )
    assert top["ok"] is True
    assert top["content"].startswith("TOP LINE")


async def test_rethink_cannot_create_block(db_session: AsyncSession) -> None:
    ws, agent = uuid.uuid4(), uuid.uuid4()
    executor = _make_executor(db_session)
    result = await executor.execute(
        "memory_rethink",
        {"label": "brand_new", "new_content": "hello"},
        {"workspace_id": str(ws), "agent_id": str(agent)},
    )
    assert result["error"] == "block_not_found"


# ---------------------------------------------------------------------------
# L3/L4 tools through the provider contract (3.4) + audit (3.5)
# ---------------------------------------------------------------------------


async def test_memory_search_and_add_via_builtin_provider(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_provider_sessions(monkeypatch)
    ws, agent = uuid.uuid4(), uuid.uuid4()
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    added = await executor.execute("memory_add", {"content": "Deployment requires approval gate"}, ctx)
    assert added["ok"] is True
    assert added["deduplicated"] is False

    dup = await executor.execute("memory_add", {"content": "Deployment requires approval gate"}, ctx)
    assert dup["ok"] is True
    assert dup["deduplicated"] is True

    found = await executor.execute("memory_search", {"query": "deployment"}, ctx)
    assert found["ok"] is True
    assert isinstance(found["results"], list)
    # The mock vector store cannot rank in the unit environment (L4 relevance
    # merge is covered by test_provider_impl with a stubbed searcher), but the
    # call must succeed through the whole contract path.

    stmt = select(MemoryEditLogModel).where(MemoryEditLogModel.tool_name == "memory_add")
    rows = (await db_session.execute(stmt)).scalars().all()
    # Only the first add mutates; the duplicate only bumps access_count.
    assert len(rows) == 1


async def test_memory_update_conflict_via_tool(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_provider_sessions(monkeypatch)
    ws, agent = uuid.uuid4(), uuid.uuid4()
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    added = await executor.execute("memory_add", {"content": "Cache TTL is 300s"}, ctx)
    memory_id = added["memory_id"]

    conflict = await executor.execute(
        "memory_update",
        {"memory_id": memory_id, "content": "Cache TTL is 600s", "expected_revision": 7},
        ctx,
    )
    assert conflict["ok"] is False
    assert conflict["error"] == "revision_conflict"
    assert conflict["current_revision"] == 1

    ok = await executor.execute(
        "memory_update",
        {"memory_id": memory_id, "content": "Cache TTL is 600s", "expected_revision": 1},
        ctx,
    )
    assert ok["ok"] is True and ok["revision"] == 2


async def test_memory_forget_via_tool(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    _bind_provider_sessions(monkeypatch)
    ws, agent = uuid.uuid4(), uuid.uuid4()
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(ws), "agent_id": str(agent)}

    added = await executor.execute("memory_add", {"content": "Outdated fact"}, ctx)
    forgotten = await executor.execute("memory_forget", {"memory_id": added["memory_id"]}, ctx)
    assert forgotten["ok"] is True
    assert forgotten["revision"] == 2

    found = await executor.execute("memory_search", {"query": "Outdated fact"}, ctx)
    assert all(r["memory_id"] != added["memory_id"] for r in found["results"])


async def test_conversation_search_empty_is_low_signal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_provider_sessions(monkeypatch)
    executor = _make_executor(db_session)
    ctx = {"workspace_id": str(uuid.uuid4()), "agent_id": str(uuid.uuid4())}
    result = await executor.execute("conversation_search", {"query": "past deployment talks"}, ctx)
    assert result["ok"] is True
    assert result["results"] == []
    assert result["low_signal"] is True
