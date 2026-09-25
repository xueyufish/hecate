"""End-to-end pipeline tests: harvest input → attribution → gate.

LLM and judge are stubbed; the projection seam is patched so no event log
is needed. Verifies run state machine, candidate creation, gate application,
budget enforcement, and per-input isolation.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionInputModel,
    EvolutionRunStatus,
    SkillCandidateModel,
)
from hecate.studio.self_evolution import attribution as attribution_module
from hecate.studio.self_evolution.gate import EvolutionGate
from hecate.studio.self_evolution.pipeline import EvolutionPipeline

WS_A = uuid.uuid4()
AGENT = uuid.uuid4()
CONVERSATION = uuid.uuid4()

VALID_RESPONSE = """
{
  "category": "invalid_invocation",
  "confidence": 0.8,
  "root_cause": "wrong arguments",
  "evidence": [{"message_index": 1, "quote": "weather_api failed"}],
  "skill": {
    "name": "verify-tool-args",
    "description": "Verify tool arguments before calling",
    "procedure": "Read the schema.",
    "guardrails": "Never guess formats."
  }
}
"""


async def _seed_input(db: AsyncSession, **overrides: Any) -> EvolutionInputModel:
    row = EvolutionInputModel(
        workspace_id=WS_A,
        conversation_id=CONVERSATION,
        agent_id=AGENT,
        signal_type="quality_below_threshold",
        quality_score=0.2,
        status="pending",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    db.add(row)
    await db.flush()
    return row


def _patch_projection(monkeypatch, messages: list[dict]) -> None:
    async def fake_project(db, conversation_id, event_store=None, **kwargs):
        return messages

    monkeypatch.setattr(
        "hecate.ops.ops_center.conversation_messages.project_conversation_messages",
        fake_project,
    )


def _stub_llm(monkeypatch) -> None:
    async def chat(**kwargs):
        return SimpleNamespace(content=VALID_RESPONSE, usage=SimpleNamespace(total_tokens=100))

    monkeypatch.setattr(attribution_module.llm_service, "chat", chat)


async def _seed_evidence(db: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Evidence + session rows for golden-subset building (pipeline WS_A)."""
    from hecate.models.evidence import EvidenceModel
    from hecate.models.session import SessionModel

    session = SessionModel(conversation_id=conversation_id, agent_id=AGENT, workspace_id=WS_A)
    db.add(session)
    await db.flush()
    db.add(
        EvidenceModel(
            session_id=session.id,
            conversation_id=conversation_id,
            workspace_id=WS_A,
            tool_name="weather_api",
            is_error=True,
        )
    )
    await db.flush()


class TestPipelineRun:
    async def test_full_cycle_validates_candidate(self, db_session: AsyncSession, monkeypatch) -> None:
        await _seed_evidence(db_session, CONVERSATION)
        for _ in range(2):
            await _seed_evidence(db_session, uuid.uuid4())
        _patch_projection(
            monkeypatch,
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "error occurred"},
                {"role": "user", "content": "That's wrong, try again"},
            ],
        )
        _stub_llm(monkeypatch)
        await _seed_input(db_session)

        async def judge(instruction: str, transcript: str) -> str:
            return "NO" if "degrade" in instruction else "YES"

        async def runner(candidate: Any, *, bind_skill: bool, agent_id) -> float:
            return 0.9 if bind_skill else 0.7

        pipeline = EvolutionPipeline(
            db_session,
            gate=EvolutionGate(db_session, runner, judge_fn=judge),
        )
        run = await pipeline.run(WS_A)

        assert run.status == EvolutionRunStatus.GATED.value
        assert run.input_count == 1
        assert run.candidate_count == 1

        candidates = (await db_session.execute(select(SkillCandidateModel))).scalars().all()
        assert len(candidates) == 1
        assert candidates[0].status == "validated"
        assert candidates[0].validation_report["overall"] == "pass"

    async def test_budget_exceeded_stops_run(self, db_session: AsyncSession, monkeypatch) -> None:
        _patch_projection(monkeypatch, [{"role": "user", "content": "hello"}])
        _stub_llm(monkeypatch)
        await _seed_input(db_session)

        pipeline = EvolutionPipeline(db_session, llm_call_limit=0)
        run = await pipeline.run(WS_A)

        assert run.status == EvolutionRunStatus.BUDGET_EXCEEDED.value
        assert run.candidate_count == 0

    async def test_input_without_conversation_is_skipped(self, db_session: AsyncSession, monkeypatch) -> None:
        await _seed_input(db_session, conversation_id=None)

        pipeline = EvolutionPipeline(db_session, llm_call_limit=5)
        run = await pipeline.run(WS_A)

        assert run.candidate_count == 0
        assert run.status == EvolutionRunStatus.GATED.value

    async def test_no_inputs_concludes_immediately(self, db_session: AsyncSession) -> None:
        pipeline = EvolutionPipeline(db_session)
        run = await pipeline.run(WS_A)

        assert run.status == EvolutionRunStatus.CONCLUDED.value
        assert run.input_count == 0
