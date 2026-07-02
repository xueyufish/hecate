"""Tests for parallel KB search across multiple knowledge bases.

Covers:
- Parallel search across multiple KBs returns globally ranked results
- One KB search failure doesn't break others
- Empty kb_ids returns empty list
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.knowledge import KnowledgeBaseModel
from hecate.services.conversation import ConversationService
from hecate.services.rag.searcher import HybridSearchResult


async def _create_test_kb(db: AsyncSession, name: str) -> KnowledgeBaseModel:
    kb = KnowledgeBaseModel(
        name=name,
        description=f"Test KB {name}",
        collection_name=f"test_{uuid.uuid4().hex[:8]}",
    )
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    return kb


def _make_search_result(content: str, score: float) -> HybridSearchResult:
    return HybridSearchResult(
        id=f"chunk-{uuid.uuid4().hex[:8]}",
        content=content,
        score=score,
        dense_score=score,
        sparse_score=0.0,
        metadata={"source": "test"},
    )


async def test_parallel_search_returns_globally_ranked(db_session: AsyncSession) -> None:
    kb1 = await _create_test_kb(db_session, "kb-1")
    kb2 = await _create_test_kb(db_session, "kb-2")
    kb3 = await _create_test_kb(db_session, "kb-3")

    mock_results = {
        str(kb1.collection_name): [
            _make_search_result("kb1-chunk-high", 0.9),
            _make_search_result("kb1-chunk-low", 0.3),
        ],
        str(kb2.collection_name): [
            _make_search_result("kb2-chunk-mid", 0.6),
        ],
        str(kb3.collection_name): [
            _make_search_result("kb3-chunk-highest", 0.95),
        ],
    }

    async def mock_search(collection_name: str, query: str, limit: int = 10, mode: str = "hybrid"):
        return mock_results.get(collection_name, [])

    service = ConversationService()
    messages = [{"role": "user", "content": "test query"}]

    with patch("hecate.services.conversation.knowledge_base_service.search", side_effect=mock_search):
        chunks = await service._retrieve_knowledge(
            db=db_session,
            kb_ids=[kb1.id, kb2.id, kb3.id],
            messages=messages,
        )

    assert len(chunks) == 4
    scores = [c["metadata"]["score"] for c in chunks]
    assert scores == sorted(scores, reverse=True)
    assert chunks[0]["content"] == "kb3-chunk-highest"
    assert chunks[0]["metadata"]["kb_id"] == str(kb3.id)


async def test_parallel_search_one_kb_fails(db_session: AsyncSession) -> None:
    kb1 = await _create_test_kb(db_session, "kb-good")
    kb2 = await _create_test_kb(db_session, "kb-fail")

    async def mock_search(collection_name: str, query: str, limit: int = 10, mode: str = "hybrid"):
        if collection_name == kb2.collection_name:
            raise RuntimeError("Search failed")
        return [_make_search_result("good-chunk", 0.8)]

    service = ConversationService()
    messages = [{"role": "user", "content": "test query"}]

    with patch("hecate.services.conversation.knowledge_base_service.search", side_effect=mock_search):
        chunks = await service._retrieve_knowledge(
            db=db_session,
            kb_ids=[kb1.id, kb2.id],
            messages=messages,
        )

    assert len(chunks) == 1
    assert chunks[0]["content"] == "good-chunk"


async def test_parallel_search_empty_kb_ids(db_session: AsyncSession) -> None:
    service = ConversationService()
    messages = [{"role": "user", "content": "test query"}]

    chunks = await service._retrieve_knowledge(
        db=db_session,
        kb_ids=[],
        messages=messages,
    )

    assert chunks == []


async def test_parallel_search_no_messages(db_session: AsyncSession) -> None:
    kb = await _create_test_kb(db_session, "kb-no-msg")
    service = ConversationService()

    chunks = await service._retrieve_knowledge(
        db=db_session,
        kb_ids=[kb.id],
        messages=[],
    )

    assert chunks == []
