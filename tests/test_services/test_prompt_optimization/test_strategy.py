"""Tests for ReflectiveMutationStrategy — prompt construction and parsing."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hecate.ops.prompt_optimization.strategy import (
    MutationProposal,
    ReflectionError,
    ReflectiveMutationStrategy,
)

_EVIDENCE = [
    {
        "query": "What is X?",
        "expected_answer": "X",
        "generated": "I do not know",
        "scores": [{"metric": "exact", "value": 0.0, "reasoning": "wrong answer", "source": "deterministic"}],
    }
]


def _mock_llm(content: str) -> MagicMock:
    mock = MagicMock()
    response = MagicMock()
    response.content = content
    mock.chat = AsyncMock(return_value=response)
    return mock


async def test_mutate_parses_strict_json() -> None:
    payload = json.dumps({"template": "New {{question}} prompt", "summary": "added formatting rules"})
    with patch("hecate_llm.service.llm_service", _mock_llm(payload)):
        proposal = await ReflectiveMutationStrategy().mutate(
            "Base {{question}}", "Parent {{question}}", _EVIDENCE, "gpt-4o-mini"
        )

    assert isinstance(proposal, MutationProposal)
    assert proposal.template == "New {{question}} prompt"
    assert proposal.summary == "added formatting rules"


async def test_mutate_parses_fenced_json() -> None:
    payload = '```json\n{"template": "T {{question}}", "summary": "s"}\n```'
    with patch("hecate_llm.service.llm_service", _mock_llm(payload)):
        proposal = await ReflectiveMutationStrategy().mutate("B", "P {{question}}", _EVIDENCE, "m")
    assert proposal.template == "T {{question}}"


async def test_mutate_raises_reflection_error_on_garbage() -> None:
    with patch("hecate_llm.service.llm_service", _mock_llm("no json here at all")), pytest.raises(ReflectionError):
        await ReflectiveMutationStrategy().mutate("B", "P", _EVIDENCE, "m")


async def test_mutate_prompt_carries_evidence_and_constraints() -> None:
    mock = _mock_llm(json.dumps({"template": "T", "summary": "s"}))
    with patch("hecate_llm.service.llm_service", mock):
        await ReflectiveMutationStrategy().mutate("BASE {{question}}", "PARENT {{question}}", _EVIDENCE, "m")

    sent = mock.chat.call_args.kwargs["messages"][0]["content"]
    assert "BASE {{question}}" in sent
    assert "PARENT {{question}}" in sent
    assert "What is X?" in sent
    assert "{{variable}}" in sent  # the do-not-add-or-remove constraint
    assert mock.chat.call_args.kwargs["model"] == "m"
