"""Tests for the evolution validation gate and golden subset builder."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.evolution import (
    EvolutionGoldenSubsetModel,
    SkillCandidateModel,
)
from hecate.models.session import SessionModel  # noqa: F401  (register table)
from hecate.studio.self_evolution.gate import EvolutionGate, GoldenSubsetBuilder

WS_A = uuid.uuid4()
AGENT = uuid.uuid4()


async def _evidence(db: AsyncSession, conversation_id: uuid.UUID, *, n: int = 2) -> None:
    from hecate.models.evidence import EvidenceModel

    session = SessionModel(
        conversation_id=conversation_id,
        agent_id=AGENT,
        workspace_id=WS_A,
    )
    db.add(session)
    await db.flush()

    for i in range(n):
        db.add(
            EvidenceModel(
                session_id=session.id,
                conversation_id=conversation_id,
                workspace_id=WS_A,
                tool_name="weather_api",
                is_error=i == 0,
            )
        )
    await db.flush()


async def _freeze_samples(
    db: AsyncSession, count: int, *, with_evidence: bool = False
) -> list[EvolutionGoldenSubsetModel]:
    rows: list[EvolutionGoldenSubsetModel] = []
    for _ in range(count):
        conv_id = uuid.uuid4()
        if with_evidence:
            await _evidence(db, conv_id)
        row = EvolutionGoldenSubsetModel(
            workspace_id=WS_A,
            agent_id=AGENT,
            conversation_id=conv_id,
            message_count=2,
        )
        db.add(row)
        rows.append(row)
    await db.flush()
    return rows


def _candidate(**overrides: Any) -> SkillCandidateModel:
    row = SkillCandidateModel(
        workspace_id=WS_A,
        name="verify-tool-args",
        description="Verify tool arguments before calling",
        procedure="Read the schema. Confirm required fields.",
        guardrails="Never guess argument formats.",
        failure_category="invalid_invocation",
        status="pending",
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestGoldenSubsetBuilder:
    async def test_freezes_from_evidence(self, db_session: AsyncSession) -> None:
        conv_id = uuid.uuid4()
        await _evidence(db_session, conv_id, n=3)

        builder = GoldenSubsetBuilder(db_session)
        count = await builder.build(WS_A, AGENT)

        assert count == 1
        rows = (
            (
                await db_session.execute(
                    select(EvolutionGoldenSubsetModel).where(
                        EvolutionGoldenSubsetModel.agent_id == AGENT,
                        ~EvolutionGoldenSubsetModel.deleted,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows[0].conversation_id == conv_id

    async def test_rebuild_replaces_old_samples(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 2)

        builder = GoldenSubsetBuilder(db_session)
        await builder.build(WS_A, AGENT)

        active = (
            (await db_session.execute(select(EvolutionGoldenSubsetModel).where(~EvolutionGoldenSubsetModel.deleted)))
            .scalars()
            .all()
        )
        assert len(active) == 0  # no evidence conversations exist yet

    async def test_freezes_with_evidence_samples(self, db_session: AsyncSession) -> None:
        conv_id = uuid.uuid4()
        await _evidence(db_session, conv_id)

        builder = GoldenSubsetBuilder(db_session)
        await builder.build(WS_A, AGENT)

        active = (
            (await db_session.execute(select(EvolutionGoldenSubsetModel).where(~EvolutionGoldenSubsetModel.deleted)))
            .scalars()
            .all()
        )
        assert [row.conversation_id for row in active] == [conv_id]


class TestEvolutionGate:
    async def test_insufficient_data_without_golden(self, db_session: AsyncSession) -> None:
        gate = EvolutionGate(db_session)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        assert report.overall == "insufficient_data"
        statuses = {c["name"]: c["status"] for c in report.checks}
        assert statuses["golden_subset_regression"] == "skipped"
        assert statuses["trigger_test"] == "skipped"

    async def test_pass_with_healthy_judge_and_runner(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 3, with_evidence=True)

        async def judge(instruction: str, transcript: str) -> str:
            # NO to "would it degrade", YES to "would the agent load it".
            return "NO" if "degrade" in instruction else "YES"

        async def runner(candidate: Any, *, bind_skill: bool) -> float:
            return 0.9 if bind_skill else 0.7

        gate = EvolutionGate(db_session, runner, judge_fn=judge)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        assert report.overall == "pass"
        statuses = {c["name"]: c["status"] for c in report.checks}
        # 4.21 path c adds a fifth check (reflection_relevance). The test
        # candidate doesn't carry ``linked_reflection_ids``, so the new
        # check is skipped (no candidate-side references to verify).
        assert statuses == {
            "golden_subset_regression": "pass",
            "trigger_test": "pass",
            "dataset_regression": "pass",
            "with_without_baseline": "pass",
            "reflection_relevance": "skipped",
        }

    async def test_regression_vote_blocks_review(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 3, with_evidence=True)

        async def judge(instruction: str, transcript: str) -> str:
            # YES to "would it degrade" → regression detected.
            return "YES"

        gate = EvolutionGate(db_session, judge_fn=judge)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        assert report.overall == "fail"
        statuses = {c["name"]: c["status"] for c in report.checks}
        assert statuses["golden_subset_regression"] == "fail"

    async def test_trigger_test_fail_blocks(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 3, with_evidence=True)

        async def judge(instruction: str, transcript: str) -> str:
            return "NO" if "need to load" in instruction else "YES"

        gate = EvolutionGate(db_session, judge_fn=judge)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        assert report.overall == "fail"
        statuses = {c["name"]: c["status"] for c in report.checks}
        assert statuses["trigger_test"] == "fail"

    async def test_baseline_improvement_required(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 3, with_evidence=True)

        async def judge(instruction: str, transcript: str) -> str:
            return "YES" if "degrade" in instruction else "YES"

        async def runner(candidate: Any, *, bind_skill: bool) -> float:
            return 0.9 if not bind_skill else 0.7  # skill makes it worse

        gate = EvolutionGate(db_session, runner, judge_fn=judge)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        assert report.overall == "fail"
        statuses = {c["name"]: c["status"] for c in report.checks}
        assert statuses["with_without_baseline"] == "fail"

    async def test_no_runner_records_skipped(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 3, with_evidence=True)

        async def judge(instruction: str, transcript: str) -> str:
            return "NO" if "degrade" in instruction else "YES"

        gate = EvolutionGate(db_session, judge_fn=judge)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        statuses = {c["name"]: c["status"] for c in report.checks}
        assert statuses["dataset_regression"] == "skipped"
        assert statuses["with_without_baseline"] == "skipped"
        assert report.overall == "pass_unverified"

    async def test_runner_returning_none_marks_unverified(self, db_session: AsyncSession) -> None:
        await _freeze_samples(db_session, 3, with_evidence=True)

        async def judge(instruction: str, transcript: str) -> str:
            return "NO" if "degrade" in instruction else "YES"

        async def runner(candidate: Any, *, bind_skill: bool) -> float | None:
            return None

        gate = EvolutionGate(db_session, runner, judge_fn=judge)
        report = await gate.validate(_candidate(), WS_A, AGENT)

        statuses = {c["name"]: c["status"] for c in report.checks}
        assert statuses["dataset_regression"] == "skipped"
        assert report.overall == "pass_unverified"
