"""Tests for the learning-input harvester (signal evaluation + dedup)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.conversation import ConversationModel
from hecate.models.conversation_turn_score import ConversationTurnScoreModel
from hecate.studio.self_evolution.harvest import LearningInputHarvester

WS_A = uuid.uuid4()
WS_B = uuid.uuid4()


async def _create_conversation(
    db: AsyncSession,
    workspace_id: uuid.UUID = WS_A,
    quality_score: float | None = None,
) -> ConversationModel:
    row = ConversationModel(
        agent_id=uuid.uuid4(),
        title="conv",
        workspace_id=workspace_id,
        quality_score=quality_score,
    )
    if quality_score is not None:
        from datetime import UTC, datetime

        row.quality_scored_at = datetime.now(UTC)
    db.add(row)
    await db.flush()
    return row


async def _add_turn_score(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    adherence: float,
) -> None:
    db.add(
        ConversationTurnScoreModel(
            conversation_id=conversation_id,
            message_id=uuid.uuid4(),
            turn_index=0,
            instruction_adherence=adherence,
        )
    )
    await db.flush()


class TestQualitySignal:
    async def test_low_turn_adherence_creates_input(self, db_session: AsyncSession) -> None:
        conv = await _create_conversation(db_session)
        await _add_turn_score(db_session, conv.id, adherence=0.2)

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        row = await harvester.harvest_conversation(conv.id, WS_A)

        assert row is not None
        assert row.signal_type == "quality_below_threshold"
        assert abs((row.quality_score or 0.0) - 0.2) < 1e-6

    async def test_conversation_level_score_used_without_turn_scores(self, db_session: AsyncSession) -> None:
        conv = await _create_conversation(db_session, quality_score=0.3)

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        row = await harvester.harvest_conversation(conv.id, WS_A)

        assert row is not None
        assert row.detail["source"] == "conversation"

    async def test_healthy_conversation_creates_nothing(self, db_session: AsyncSession) -> None:
        conv = await _create_conversation(db_session)
        await _add_turn_score(db_session, conv.id, adherence=0.9)

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        assert await harvester.harvest_conversation(conv.id, WS_A) is None

    async def test_unscored_conversation_creates_nothing(self, db_session: AsyncSession) -> None:
        conv = await _create_conversation(db_session)

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        assert await harvester.harvest_conversation(conv.id, WS_A) is None


class TestCorrectionSignal:
    async def test_user_correction_creates_input(self, db_session: AsyncSession, monkeypatch) -> None:
        conv = await _create_conversation(db_session)

        async def fake_project(db, conversation_id, event_store=None):
            return [
                {"role": "user", "content": "No, I meant the quarterly report"},
                {"role": "assistant", "content": "Understood"},
            ]

        monkeypatch.setattr(
            "hecate.ops.ops_center.conversation_messages.project_conversation_messages",
            fake_project,
        )

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        row = await harvester.harvest_conversation(conv.id, WS_A)

        assert row is not None
        assert row.signal_type == "user_correction"

    async def test_correction_signal_deduped_after_quality_input(self, db_session: AsyncSession) -> None:
        conv = await _create_conversation(db_session, quality_score=0.2)

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        first = await harvester.harvest_conversation(conv.id, WS_A)
        second = await harvester.harvest_conversation(conv.id, WS_A)

        assert first is not None
        assert second is None


class TestSweep:
    async def test_sweep_scopes_to_workspace_and_scored_only(self, db_session: AsyncSession) -> None:
        scored_a = await _create_conversation(db_session, quality_score=0.2)
        await _create_conversation(db_session, WS_B, quality_score=0.2)  # other workspace
        await _create_conversation(db_session, WS_A)  # unscored

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        harvested = await harvester.harvest_recent_conversations(WS_A)

        assert [row.conversation_id for row in harvested] == [scored_a.id]

    async def test_sweep_survives_per_conversation_failure(self, db_session: AsyncSession, monkeypatch) -> None:
        scored_first = await _create_conversation(db_session, quality_score=0.2)
        failed_conv = await _create_conversation(db_session, quality_score=0.1)
        ok_conv = await _create_conversation(db_session, quality_score=0.3)

        real_harvest = LearningInputHarvester.harvest_conversation
        attempted: list[uuid.UUID] = []

        async def flaky_harvest(self, conversation_id, workspace_id):
            attempted.append(conversation_id)
            if conversation_id == failed_conv.id:
                raise RuntimeError("boom")
            return await real_harvest(self, conversation_id, workspace_id)

        monkeypatch.setattr(LearningInputHarvester, "harvest_conversation", flaky_harvest)

        harvester = LearningInputHarvester(db_session, quality_threshold=0.6)
        harvested = await harvester.harvest_recent_conversations(WS_A)

        assert failed_conv.id in attempted
        assert sorted(row.conversation_id for row in harvested) == sorted([scored_first.id, ok_conv.id])
