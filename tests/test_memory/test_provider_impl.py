"""Tests for ``BuiltinMemoryProvider`` — the full-contract builtin backend.

Covers the tier-2/3 behavior on the in-memory test database: L3/L4 merged
fact retrieval, revision-guarded updates, soft forget with vector cleanup,
L4 dedup on add, and the prefetch budget. The provider's self-managed
sessions are pointed at the test session factory so fixture data is visible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from hecate.core.composition.memory_provider import (
    MemoryFactHit,
    MemoryWriteResult,
    PrefetchEntry,
    RecallPage,
    provider_supports,
)

# ---------------------------------------------------------------------------
# provider_supports routing
# ---------------------------------------------------------------------------


class _SearchOnly:
    async def search(self, *args: Any, **kwargs: Any) -> list[Any]:  # noqa: ANN401
        return []


class _Full:
    def capabilities(self) -> frozenset[str]:
        return frozenset({"search", "add_memory"})


class _Raising:
    def capabilities(self) -> frozenset[str]:
        raise RuntimeError("boom")


def test_provider_supports_defaults_to_search_only() -> None:
    """A pre-tiering provider without capabilities() is search-only."""
    assert provider_supports(_SearchOnly(), "search")
    assert not provider_supports(_SearchOnly(), "add_memory")


def test_provider_supports_reads_declaration() -> None:
    provider = _Full()
    assert provider_supports(provider, "add_memory")
    assert not provider_supports(provider, "forget_memory")


def test_provider_supports_degrades_on_raising_capabilities() -> None:
    """A raising capabilities() degrades to search-only instead of propagating."""
    assert provider_supports(_Raising(), "search")
    assert not provider_supports(_Raising(), "search_memories")


# ---------------------------------------------------------------------------
# BuiltinMemoryProvider CRUD (against the in-memory test database)
# ---------------------------------------------------------------------------


def _bind_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the provider's self-managed sessions at the test database."""
    from hecate.core import database as core_db
    from tests.conftest import test_session_factory

    monkeypatch.setattr(core_db, "async_session_factory", test_session_factory)


@pytest.fixture
def make_provider(monkeypatch: pytest.MonkeyPatch):
    _bind_sessions(monkeypatch)

    def _make() -> Any:
        from hecate_memory.memory.provider_impl import BuiltinMemoryProvider

        return BuiltinMemoryProvider(vector_store=None)

    return _make


async def _seed_l3(db: Any, workspace_id: uuid.UUID, content: str, importance: float) -> Any:
    from hecate_memory.memory.user_memory import UserMemoryService

    from hecate.models.memory import MemoryCreateSchema

    schema = MemoryCreateSchema(content=content, importance=importance)
    return await UserMemoryService(db).store_memory(workspace_id, schema)


async def _seed_l4(db: Any, agent_id: uuid.UUID, workspace_id: uuid.UUID, content: str) -> Any:
    from hecate_memory.memory.knowledge_memory import KnowledgeMemoryService

    return await KnowledgeMemoryService(db).insert_knowledge(
        agent_id=agent_id,
        workspace_id=workspace_id,
        content=content,
    )


async def test_search_memories_merges_l3_and_l4(
    db_session: Any, make_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = uuid.uuid4()
    agent = uuid.uuid4()
    await _seed_l3(db_session, ws, "User prefers morning flights", importance=0.9)
    await _seed_l4(db_session, agent, ws, "Reimbursement requires manager approval")
    await db_session.flush()

    # The L4 leg needs a vector store; stub the searcher result so the merge
    # logic is exercised deterministically in the unit environment.
    from dataclasses import dataclass as _dc

    from hecate_memory.memory import knowledge_memory as km_mod

    class _StubMemory:
        id: uuid.UUID = uuid.uuid4()
        content: str = "Reimbursement requires manager approval"
        revision: int = 1
        tags: list[str] = ["policy"]
        importance: float = 0.5
        access_count: int = 0
        created_at: datetime = datetime.now(UTC)

    @_dc
    class _StubResult:
        memory = _StubMemory()
        score = 0.42
        dense_score = 0.4
        sparse_score = 0.1
        last_confirmed_at = None

    async def _fake_search(self: Any, *a: Any, **kw: Any) -> list[Any]:
        return [_StubResult()]

    monkeypatch.setattr(km_mod.KnowledgeMemoryService, "search_knowledge", _fake_search)

    provider = make_provider()
    hits = await provider.search_memories(query="preferences", workspace_id=ws, agent_id=agent, top_k=10)

    layers = {h.source_layer for h in hits}
    assert layers == {"user_memory", "knowledge_memory"}
    assert all(isinstance(h, MemoryFactHit) for h in hits)
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


async def test_search_memories_requires_agent_for_l4(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    await _seed_l3(db_session, ws, "User likes tea", importance=0.5)
    await db_session.flush()

    provider = make_provider()
    hits = await provider.search_memories(query="tea", workspace_id=ws, agent_id=None, top_k=5)
    assert {h.source_layer for h in hits} == {"user_memory"}


async def test_add_memory_dedups_and_returns_revision(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    agent = uuid.uuid4()
    provider = make_provider()

    first = await provider.add_memory(content="Deploy needs a tag", workspace_id=ws, agent_id=agent)
    second = await provider.add_memory(content="Deploy needs a tag", workspace_id=ws, agent_id=agent)

    assert isinstance(first, MemoryWriteResult) and first.ok
    assert second.ok
    assert second.memory_id == first.memory_id
    assert second.metadata["deduplicated"] is True
    assert second.revision == first.revision == 1


async def test_add_memory_requires_agent(db_session: Any, make_provider: Any) -> None:
    provider = make_provider()
    result = await provider.add_memory(content="orphan", workspace_id=uuid.uuid4(), agent_id=None)
    assert not result.ok
    assert result.error == "missing_agent"


async def test_update_memory_l4_revision_conflict(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    agent = uuid.uuid4()
    seeded = await _seed_l4(db_session, agent, ws, "Original content")
    await db_session.flush()
    provider = make_provider()

    conflict = await provider.update_memory(
        memory_id=seeded.id,
        workspace_id=ws,
        patch={"content": "Changed"},
        expected_revision=99,
        agent_id=agent,
    )
    assert not conflict.ok
    assert conflict.error == "revision_conflict"
    assert conflict.metadata["current_revision"] == 1

    ok = await provider.update_memory(
        memory_id=seeded.id,
        workspace_id=ws,
        patch={"content": "Changed"},
        expected_revision=1,
        agent_id=agent,
    )
    assert ok.ok and ok.revision == 2


async def test_update_memory_rejects_unknown_fields(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    agent = uuid.uuid4()
    seeded = await _seed_l4(db_session, agent, ws, "Content")
    await db_session.flush()

    result = await make_provider().update_memory(
        memory_id=seeded.id,
        workspace_id=ws,
        patch={"scope": {"user_id": "x"}},
        agent_id=agent,
    )
    assert not result.ok
    assert result.error == "invalid_patch"


async def test_update_memory_l3_dispatch(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    seeded = await _seed_l3(db_session, ws, "Prefers window seat", importance=0.5)
    await db_session.flush()

    result = await make_provider().update_memory(
        memory_id=seeded.id,
        workspace_id=ws,
        patch={"importance": 0.8},
    )
    assert result.ok and result.revision == 2


async def test_forget_memory_soft_deletes(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    agent = uuid.uuid4()
    seeded = await _seed_l4(db_session, agent, ws, "Ephemeral fact")
    await db_session.flush()
    provider = make_provider()

    result = await provider.forget_memory(memory_id=seeded.id, workspace_id=ws, agent_id=agent)
    assert result.ok and result.revision == 2

    # Deleted rows are no longer retrievable through the contract.
    gone = await provider.update_memory(memory_id=seeded.id, workspace_id=ws, patch={"content": "x"})
    assert gone.ok is False
    assert gone.error == "not_found"


async def test_forget_memory_not_found(db_session: Any, make_provider: Any) -> None:
    provider = make_provider()
    result = await provider.forget_memory(memory_id=uuid.uuid4(), workspace_id=uuid.uuid4())
    assert not result.ok
    assert result.error == "not_found"


async def test_prefetch_respects_entry_and_token_budget(db_session: Any, make_provider: Any) -> None:
    ws = uuid.uuid4()
    for i in range(6):
        await _seed_l3(db_session, ws, f"Fact number {i} " + "x" * 300, importance=0.5 + i / 10)
    await db_session.flush()

    provider = make_provider()
    entries = await provider.prefetch(
        query_text="facts",
        workspace_id=ws,
        max_entries=3,
        max_tokens=1000,
    )
    assert all(isinstance(e, PrefetchEntry) for e in entries)
    assert 0 < len(entries) <= 3

    tiny = await provider.prefetch(
        query_text="facts",
        workspace_id=ws,
        max_entries=6,
        max_tokens=1,
    )
    assert len(tiny) <= 1


async def test_sync_turn_is_noop(db_session: Any, make_provider: Any) -> None:
    provider = make_provider()
    await provider.sync_turn(
        workspace_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        messages=[{"role": "user", "content": "hi"}],
    )


async def test_search_recall_returns_page(db_session: Any, make_provider: Any) -> None:
    """Without any indexed rows the recall search degrades to a low-signal page."""
    provider = make_provider()
    page = await provider.search_recall(query="anything", workspace_id=uuid.uuid4(), agent_id=uuid.uuid4())
    assert isinstance(page, RecallPage)
    assert page.hits == []
    assert page.low_signal is True
