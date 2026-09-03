"""Tests for SecurityFinding feedback endpoint and service method."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.security_finding import SecurityFindingModel
from hecate.ops.security.findings import SecurityFindingService


@pytest.fixture
def service() -> SecurityFindingService:
    return SecurityFindingService()


async def _create_finding(
    db: AsyncSession,
    org_id: uuid.UUID | None = None,
) -> SecurityFindingModel:
    finding = SecurityFindingModel(
        org_id=org_id or uuid.uuid4(),
        workspace_id=None,
        user_id=None,
        rule_name="test:rule",
        severity="medium",
        message="Test finding",
        source_event={"event": "test"},
        metadata_={},
    )
    db.add(finding)
    await db.flush()
    return finding


class TestSetFeedback:
    async def test_sets_true_positive_feedback(
        self,
        db_session: AsyncSession,
        service: SecurityFindingService,
    ) -> None:
        finding = await _create_finding(db_session)
        result = await service.set_feedback(
            finding_id=finding.id,
            feedback="true_positive",
            feedback_user="analyst@corp.com",
            feedback_comment="Confirmed — real AWS key",
            session=db_session,
        )
        assert result is not None
        assert result.metadata_["feedback"] == "true_positive"
        assert result.metadata_["feedback_user"] == "analyst@corp.com"
        assert result.metadata_["feedback_comment"] == "Confirmed — real AWS key"
        assert "feedback_at" in result.metadata_

    async def test_sets_false_positive_feedback(
        self,
        db_session: AsyncSession,
        service: SecurityFindingService,
    ) -> None:
        finding = await _create_finding(db_session)
        result = await service.set_feedback(
            finding_id=finding.id,
            feedback="false_positive",
            feedback_user="dev@corp.com",
            session=db_session,
        )
        assert result is not None
        assert result.metadata_["feedback"] == "false_positive"
        assert result.metadata_["feedback_comment"] is None

    async def test_returns_none_for_missing_finding(
        self,
        db_session: AsyncSession,
        service: SecurityFindingService,
    ) -> None:
        result = await service.set_feedback(
            finding_id=uuid.uuid4(),
            feedback="true_positive",
            feedback_user="user",
            session=db_session,
        )
        assert result is None

    async def test_overwrites_previous_feedback(
        self,
        db_session: AsyncSession,
        service: SecurityFindingService,
    ) -> None:
        finding = await _create_finding(db_session)
        await service.set_feedback(
            finding_id=finding.id,
            feedback="true_positive",
            feedback_user="analyst",
            session=db_session,
        )
        result = await service.set_feedback(
            finding_id=finding.id,
            feedback="false_positive",
            feedback_user="analyst",
            feedback_comment="Changed my mind",
            session=db_session,
        )
        assert result is not None
        assert result.metadata_["feedback"] == "false_positive"
        assert result.metadata_["feedback_comment"] == "Changed my mind"

    async def test_preserves_existing_metadata(
        self,
        db_session: AsyncSession,
        service: SecurityFindingService,
    ) -> None:
        finding = await _create_finding(db_session)
        finding.metadata_ = {"custom_key": "custom_value", "nested": {"a": 1}}
        await db_session.flush()

        result = await service.set_feedback(
            finding_id=finding.id,
            feedback="true_positive",
            feedback_user="user",
            session=db_session,
        )
        assert result is not None
        assert result.metadata_["custom_key"] == "custom_value"
        assert result.metadata_["nested"] == {"a": 1}
        assert result.metadata_["feedback"] == "true_positive"
