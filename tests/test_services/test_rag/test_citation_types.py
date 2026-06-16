"""Tests for citation types."""

from __future__ import annotations

import uuid

from hecate.services.rag.types import Citation, CitationResponse, KbCitationAnnotation


def test_citation_creation():
    citation = Citation(
        position=1,
        kb_id=uuid.uuid4(),
        kb_name="Test KB",
        document_name="test.pdf",
        chunk_id="chunk-123",
        score=0.95,
        content_snippet="First 150 chars...",
    )
    assert citation.position == 1
    assert citation.score == 0.95


def test_citation_to_annotation():
    kb_id = uuid.uuid4()
    citation = Citation(
        position=1,
        kb_id=kb_id,
        kb_name="Test KB",
        document_name="test.pdf",
        chunk_id="chunk-123",
        score=0.95,
        content_snippet="First 150 chars...",
    )
    annotation = citation.to_annotation()
    assert annotation["type"] == "kb_citation"
    assert annotation["kb_citation"]["position"] == 1
    assert annotation["kb_citation"]["kb_id"] == str(kb_id)
    assert annotation["kb_citation"]["score"] == 0.95


def test_citation_content_snippet_max_length():
    kb_id = uuid.uuid4()
    long_snippet = "x" * 200
    citation = Citation(
        position=1,
        kb_id=kb_id,
        kb_name="Test KB",
        document_name="test.pdf",
        chunk_id="chunk-123",
        score=0.95,
        content_snippet=long_snippet[:150],
    )
    assert len(citation.content_snippet) == 150


def test_kb_citation_annotation():
    kb_id = uuid.uuid4()
    citation = Citation(
        position=1,
        kb_id=kb_id,
        kb_name="Test KB",
        document_name="test.pdf",
        chunk_id="chunk-123",
        score=0.95,
        content_snippet="First 150 chars...",
    )
    annotation = KbCitationAnnotation(kb_citation=citation)
    assert annotation.type == "kb_citation"
    assert annotation.kb_citation.position == 1


def test_citation_response():
    message_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    citation = Citation(
        position=1,
        kb_id=kb_id,
        kb_name="Test KB",
        document_name="test.pdf",
        chunk_id="chunk-123",
        score=0.95,
        content_snippet="First 150 chars...",
    )
    response = CitationResponse(
        citations=[citation],
        message_id=message_id,
    )
    assert len(response.citations) == 1
    assert response.message_id == message_id


def test_citation_response_empty():
    message_id = uuid.uuid4()
    response = CitationResponse(
        citations=[],
        message_id=message_id,
    )
    assert len(response.citations) == 0
