"""Tests for knowledge injection in ContextAssembler."""

from __future__ import annotations

from hecate.services.context.assembler import ContextAssembler
from hecate.services.context.budget import BudgetManager


def test_inject_knowledge_empty():
    assembler = ContextAssembler(BudgetManager())
    messages = [{"role": "user", "content": "Hello"}]
    result_messages, citation_map = assembler._inject_knowledge(messages, [])
    assert result_messages == messages
    assert citation_map == {}


def test_inject_knowledge_none():
    assembler = ContextAssembler(BudgetManager())
    messages = [{"role": "user", "content": "Hello"}]
    result_messages, citation_map = assembler._inject_knowledge(messages, [])
    assert result_messages == messages
    assert citation_map == {}


def test_inject_knowledge_with_chunks():
    assembler = ContextAssembler(BudgetManager())
    messages = [{"role": "user", "content": "Hello"}]
    knowledge = [
        {
            "id": "chunk-1",
            "content": "First chunk content",
            "metadata": {
                "kb_id": "kb-1",
                "kb_name": "Test KB",
                "source_file": "doc1.pdf",
                "score": 0.95,
            },
        },
        {
            "id": "chunk-2",
            "content": "Second chunk content",
            "metadata": {
                "kb_id": "kb-1",
                "kb_name": "Test KB",
                "source_file": "doc2.pdf",
                "score": 0.85,
            },
        },
    ]
    result_messages, citation_map = assembler._inject_knowledge(messages, knowledge)
    assert len(result_messages) == 2
    assert result_messages[0]["role"] == "system"
    assert "The following reference documents are available:" in result_messages[0]["content"]
    assert "[1]" in result_messages[0]["content"]
    assert "[2]" in result_messages[0]["content"]
    assert len(citation_map) == 2
    assert citation_map[1]["chunk_id"] == "chunk-1"
    assert citation_map[2]["chunk_id"] == "chunk-2"


def test_inject_knowledge_truncation():
    assembler = ContextAssembler(BudgetManager())
    messages = [{"role": "user", "content": "Hello"}]
    long_content = "x" * 600
    knowledge = [
        {
            "id": "chunk-1",
            "content": long_content,
            "metadata": {
                "kb_id": "kb-1",
                "kb_name": "Test KB",
                "source_file": "doc1.pdf",
                "score": 0.95,
            },
        },
    ]
    result_messages, citation_map = assembler._inject_knowledge(messages, knowledge)
    assert "..." in result_messages[0]["content"]
    assert len(citation_map[1]["content_snippet"]) == 150
