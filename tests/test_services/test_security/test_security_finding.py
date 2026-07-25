"""Tests for SecurityFindingModel and SecurityFindingService."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from hecate.models.security_finding import (
    SecurityFindingModel,
    SecurityFindingQuerySchema,
    SecurityFindingReadSchema,
)


class TestSecurityFindingModel:
    """Tests for SecurityFindingModel ORM."""

    async def test_create_finding(self, db_session) -> None:  # type: ignore[no-untyped-def]
        org_id = uuid.uuid4()
        finding = SecurityFindingModel(
            org_id=org_id,
            workspace_id=None,
            user_id=None,
            rule_name="bulk_delete_rule",
            severity="medium",
            message="User performed 5 deletes in 1 minute",
            source_event={"action": "agent.delete"},
            metadata_={"delete_count": 5},
        )
        db_session.add(finding)
        await db_session.flush()

        result = await db_session.execute(select(SecurityFindingModel).where(SecurityFindingModel.id == finding.id))
        row = result.scalar_one()
        assert row.rule_name == "bulk_delete_rule"
        assert row.severity == "medium"
        assert row.source_event["action"] == "agent.delete"
        assert row.metadata_["delete_count"] == 5

    async def test_finding_with_user_and_workspace(self, db_session) -> None:  # type: ignore[no-untyped-def]
        org_id = uuid.uuid4()
        ws_id = uuid.uuid4()
        user_id = uuid.uuid4()
        finding = SecurityFindingModel(
            org_id=org_id,
            workspace_id=ws_id,
            user_id=user_id,
            rule_name="unusual_ip_rule",
            severity="low",
            message="Action from unrecognized IP",
            source_event={"ip": "10.0.0.99"},
            metadata_={},
        )
        db_session.add(finding)
        await db_session.flush()
        assert finding.id is not None
        assert finding.workspace_id == ws_id


class TestSecurityFindingSchema:
    """Tests for Pydantic schemas."""

    def test_read_schema_from_model(self) -> None:
        from datetime import UTC, datetime

        finding_id = uuid.uuid4()
        org_id = uuid.uuid4()
        model = SecurityFindingModel(
            id=finding_id,
            org_id=org_id,
            rule_name="off_hours_rule",
            severity="low",
            message="Sensitive op on weekend",
            source_event=None,
            metadata_={},
            created_at=datetime.now(UTC),
        )
        schema = SecurityFindingReadSchema.model_validate(model)
        assert schema.rule_name == "off_hours_rule"
        assert schema.severity == "low"

    def test_query_schema_defaults(self) -> None:
        params = SecurityFindingQuerySchema()
        assert params.limit == 50
        assert params.offset == 0


class TestSecurityFindingService:
    """Tests for SecurityFindingService query and cleanup."""

    async def test_query_by_rule_name(self, db_session) -> None:  # type: ignore[no-untyped-def]
        from hecate.services.security.finding_service import SecurityFindingService

        org_id = uuid.uuid4()
        finding = SecurityFindingModel(
            org_id=org_id,
            rule_name="bulk_delete_rule",
            severity="high",
            message="test",
            source_event={},
            metadata_={},
        )
        db_session.add(finding)
        await db_session.flush()

        svc = SecurityFindingService()
        params = SecurityFindingQuerySchema(rule_name="bulk_delete_rule")
        results, total = await svc.query(params, session=db_session)
        assert total == 1
        assert results[0].rule_name == "bulk_delete_rule"

    async def test_query_by_severity(self, db_session) -> None:  # type: ignore[no-untyped-def]
        from hecate.services.security.finding_service import SecurityFindingService

        org_id = uuid.uuid4()
        for sev in ("low", "medium", "high", "critical"):
            finding = SecurityFindingModel(
                org_id=org_id,
                rule_name="test_rule",
                severity=sev,
                message="test",
                source_event={},
                metadata_={},
            )
            db_session.add(finding)
        await db_session.flush()

        svc = SecurityFindingService()
        params = SecurityFindingQuerySchema(severity="high")
        results, total = await svc.query(params, session=db_session)
        assert total == 2  # high + critical (>= operator)
