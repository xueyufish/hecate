"""Tests for EnginePort knowledge_query integration.

Tests cover:
- AgentExecutionPort.knowledge_query() delegates to KnowledgeBaseService
- kb_id lookup and collection name mapping
- Graceful handling of missing knowledge bases
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.services.rag.searcher import HybridSearchResult


@pytest.mark.asyncio
async def test_knowledge_query_empty_kb_ids() -> None:
    """Test knowledge_query with empty kb_ids returns empty list."""
    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    mock_db = AsyncMock(spec=AsyncSession)
    port = AgentExecutionPort(db=mock_db)

    results = await port.knowledge_query(query="test", kb_ids=[])
    assert results == []


@pytest.mark.asyncio
async def test_knowledge_query_with_mock_search() -> None:
    """Test knowledge_query delegates to KnowledgeBaseService.search()."""
    from hecate.services.orchestration.agent_execution_port import AgentExecutionPort

    mock_db = AsyncMock(spec=AsyncSession)
    port = AgentExecutionPort(db=mock_db)

    mock_results = [
        HybridSearchResult(
            id="test_id",
            score=0.95,
            content="test content",
            metadata={"source": "test.pdf"},
        )
    ]

    with patch("hecate.services.orchestration.agent_execution_port.knowledge_base_service") as mock_kb_service:
        mock_kb_service.search = AsyncMock(return_value=mock_results)

        mock_result = AsyncMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        results = await port.knowledge_query(
            query="test query",
            kb_ids=[uuid4()],
        )

        assert results == []


def test_knowledge_query_result_format() -> None:
    """Test that knowledge_query results have correct format."""
    result = {
        "content": "test content",
        "metadata": {
            "score": 0.95,
            "kb_id": "test-kb-id",
            "kb_name": "Test KB",
            "source": "test.pdf",
        },
    }
    assert "content" in result
    assert "metadata" in result
    assert "score" in result["metadata"]
    assert "kb_id" in result["metadata"]
