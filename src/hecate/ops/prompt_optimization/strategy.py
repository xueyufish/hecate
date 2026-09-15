"""Mutation strategies for the prompt optimization loop (6.19).

A strategy turns rollout evidence into a candidate template. The interface
is deliberately narrow (design D1): ``mutate`` receives the base template,
the parent template being improved on, the parent's failure evidence, and
returns one candidate. Search mechanics (pooling, gating, budgeting) live
in the runner — only the proposal step is pluggable.

``ReflectiveMutationStrategy`` is the v1 implementation: one reflection LLM
call reads the parent's failure trajectories (query, expected answer,
generated answer, evaluator reasoning — GEPA's "actionable side
information") and emits a full replacement template plus a human-readable
summary of what changed and why.
"""

from __future__ import annotations

import abc
import json
import logging
import re

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

_MAX_EVIDENCE_ITEMS = 20


class ReflectionError(ValueError):
    """The reflection LLM returned output the pipeline cannot use."""


class MutationProposal(BaseModel):
    """Structured output expected from a reflection call."""

    template: str
    summary: str


class MutationStrategy(abc.ABC):
    """Pluggable candidate-proposal step of the optimization loop.

    Runtime-internal extension point: implementations are named after the
    mechanism (no Port/Base/ABC suffix) and registered by name in the run
    config's ``strategy`` field.
    """

    @abc.abstractmethod
    async def mutate(
        self,
        base_template: str,
        parent_template: str,
        evidence: list[dict],
        reflection_model: str,
    ) -> MutationProposal:
        """Propose one candidate template from rollout evidence.

        Args:
            base_template: The run's baseline template (variable-set anchor).
            parent_template: The template the evidence was produced with.
            evidence: Failure trajectories — dicts with ``query``,
                ``expected_answer``, ``generated``, ``scores``.
            reflection_model: Model identifier for the reflection call.

        Returns:
            A MutationProposal with the full replacement template.

        Raises:
            ReflectionError: When the reflection call fails or returns
                unusable output — the runner records the candidate as a
                rejected mutation.
        """


class ReflectiveMutationStrategy(MutationStrategy):
    """GEPA-style reflective mutation over failure trajectories."""

    def __init__(self, name: str = "reflective_mutation") -> None:
        self.name = name

    async def mutate(
        self,
        base_template: str,
        parent_template: str,
        evidence: list[dict],
        reflection_model: str,
    ) -> MutationProposal:
        from hecate_llm.service import llm_service

        prompt = self._build_prompt(base_template, parent_template, evidence)
        response = await llm_service.chat(
            messages=[{"role": "user", "content": prompt}],
            model=reflection_model,
        )
        return self._parse(getattr(response, "content", "") or "")

    def _build_prompt(
        self,
        base_template: str,
        parent_template: str,
        evidence: list[dict],
    ) -> str:
        evidence_block = json.dumps(evidence[:_MAX_EVIDENCE_ITEMS], ensure_ascii=False, indent=2)
        return (
            "You are optimizing a system prompt for an AI agent. Below are the "
            "base prompt, the current prompt being improved, and failure "
            "trajectories from evaluating the current prompt on a dataset "
            "(each entry: the user query, the expected answer, the generated "
            "answer, and per-metric scores with the evaluator's reasoning).\n\n"
            "Write an improved FULL replacement for the current prompt that "
            "fixes the observed failures without regressing what already "
            "works. Hard constraints:\n"
            "1. Keep exactly the same template placeholders as the base "
            "prompt — do not add or remove any {{variable}}.\n"
            "2. Keep valid template syntax (blocks, filters, literals).\n"
            '3. Output strict JSON: {"template": "<full new prompt>", '
            '"summary": "<one-paragraph explanation of the changes and '
            'which failures they address>"}. No markdown fences, no extra '
            "keys.\n\n"
            f"BASE PROMPT:\n{base_template}\n\n"
            f"CURRENT PROMPT:\n{parent_template}\n\n"
            f"FAILURE TRAJECTORIES:\n{evidence_block}"
        )

    def _parse(self, content: str) -> MutationProposal:
        text = content.strip()
        # Tolerate fenced output even though the prompt forbids it.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        try:
            return MutationProposal.model_validate_json(text)
        except ValidationError as first_error:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match is None:
                msg = f"reflection output is not JSON: {text[:200]!r}"
                raise ReflectionError(msg) from first_error
            try:
                return MutationProposal.model_validate_json(match.group(0))
            except ValidationError as e:
                msg = f"reflection JSON missing required keys: {e}"
                raise ReflectionError(msg) from e
