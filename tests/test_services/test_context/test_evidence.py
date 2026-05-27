"""Unit tests for EvidenceTracker."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evidence import EvidenceModel
from hecate.services.context.evidence_tracker import EvidenceTracker


@pytest.mark.asyncio
async def test_capture_tool_result(db_session: AsyncSession) -> None:
    """Test capturing a tool execution result."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    evidence = await tracker.capture(
        tool_name="web_search",
        tool_arguments={"query": "Python tutorials"},
        result={"status": "ok", "data": ["result1", "result2"]},
        session_id=session_id,
    )

    assert evidence is not None
    assert evidence.tool_name == "web_search"
    assert evidence.session_id == session_id
    assert evidence.is_error is False
    assert evidence.importance == 0.5
    assert evidence.normalized_content["format"] == "json"


@pytest.mark.asyncio
async def test_capture_error_result(db_session: AsyncSession) -> None:
    """Test capturing an error result."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    evidence = await tracker.capture(
        tool_name="write_file",
        tool_arguments={"path": "/test.txt", "content": "data"},
        result="Permission denied",
        session_id=session_id,
        is_error=True,
    )

    assert evidence is not None
    assert evidence.is_error is True
    assert evidence.importance == 0.0


@pytest.mark.asyncio
async def test_normalize_json_result(db_session: AsyncSession) -> None:
    """Test normalization of JSON results."""
    tracker = EvidenceTracker(db_session)

    result = {"key": "value", "nested": {"a": 1}}
    normalized, raw = tracker._normalize_result(result)

    assert normalized["format"] == "json"
    assert normalized["value"] == result
    assert raw is not None


@pytest.mark.asyncio
async def test_normalize_text_result(db_session: AsyncSession) -> None:
    """Test normalization of text results."""
    tracker = EvidenceTracker(db_session)

    normalized, raw = tracker._normalize_result("Hello, world!")

    assert normalized["format"] == "text"
    assert normalized["value"] == "Hello, world!"


@pytest.mark.asyncio
async def test_normalize_json_string(db_session: AsyncSession) -> None:
    """Test normalization of JSON string results."""
    tracker = EvidenceTracker(db_session)

    normalized, raw = tracker._normalize_result('{"key": "value"}')

    assert normalized["format"] == "json"
    assert normalized["value"] == {"key": "value"}


@pytest.mark.asyncio
async def test_normalize_null_result(db_session: AsyncSession) -> None:
    """Test normalization of null results."""
    tracker = EvidenceTracker(db_session)

    normalized, raw = tracker._normalize_result(None)

    assert normalized["format"] == "null"
    assert normalized["value"] is None


@pytest.mark.asyncio
async def test_boost_importance(db_session: AsyncSession) -> None:
    """Test boosting importance of evidence."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    evidence = await tracker.capture(
        tool_name="test_tool",
        tool_arguments={},
        result="test",
        session_id=session_id,
    )

    new_importance = await tracker.boost_importance(evidence.id)
    assert new_importance == 0.6  # 0.5 + 0.1

    # Boost again
    new_importance = await tracker.boost_importance(evidence.id)
    assert new_importance == 0.7  # 0.6 + 0.1


@pytest.mark.asyncio
async def test_boost_importance_capped(db_session: AsyncSession) -> None:
    """Test that importance boost is capped at 1.0."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    evidence = await tracker.capture(
        tool_name="test_tool",
        tool_arguments={},
        result="test",
        session_id=session_id,
    )

    # Set importance near max
    evidence.importance = 0.95
    await db_session.flush()

    new_importance = await tracker.boost_importance(evidence.id)
    assert new_importance == 1.0  # Capped at max


@pytest.mark.asyncio
async def test_query_by_session(db_session: AsyncSession) -> None:
    """Test querying evidence by session."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    await tracker.capture(
        tool_name="tool1",
        tool_arguments={},
        result="result1",
        session_id=session_id,
    )
    await tracker.capture(
        tool_name="tool2",
        tool_arguments={},
        result="result2",
        session_id=session_id,
    )

    results = await tracker.query(session_id=session_id)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_query_by_tool_name(db_session: AsyncSession) -> None:
    """Test querying evidence by tool name."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    await tracker.capture(
        tool_name="web_search",
        tool_arguments={},
        result="result",
        session_id=session_id,
    )
    await tracker.capture(
        tool_name="write_file",
        tool_arguments={},
        result="result",
        session_id=session_id,
    )

    results = await tracker.query(session_id=session_id, tool_name="web_search")
    assert len(results) == 1
    assert results[0].tool_name == "web_search"


@pytest.mark.asyncio
async def test_query_by_importance(db_session: AsyncSession) -> None:
    """Test querying evidence by minimum importance."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    e1 = await tracker.capture(
        tool_name="tool1",
        tool_arguments={},
        result="result",
        session_id=session_id,
    )
    e1.importance = 0.8

    e2 = await tracker.capture(
        tool_name="tool2",
        tool_arguments={},
        result="result",
        session_id=session_id,
    )
    e2.importance = 0.3

    await db_session.flush()

    results = await tracker.query(session_id=session_id, min_importance=0.5)
    assert len(results) == 1
    assert results[0].tool_name == "tool1"


@pytest.mark.asyncio
async def test_get_by_id(db_session: AsyncSession) -> None:
    """Test getting evidence by ID."""
    tracker = EvidenceTracker(db_session)
    session_id = uuid4()

    evidence = await tracker.capture(
        tool_name="test_tool",
        tool_arguments={},
        result="test",
        session_id=session_id,
    )

    found = await tracker.get_by_id(evidence.id)
    assert found is not None
    assert found.id == evidence.id


@pytest.mark.asyncio
async def test_get_by_id_not_found(db_session: AsyncSession) -> None:
    """Test getting non-existent evidence returns None."""
    tracker = EvidenceTracker(db_session)

    found = await tracker.get_by_id(uuid4())
    assert found is None
