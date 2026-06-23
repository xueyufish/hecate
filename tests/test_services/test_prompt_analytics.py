"""Tests for prompt analytics, diff, commit messages, and protected labels."""

from __future__ import annotations

import uuid

import pytest

import hecate.models.prompt  # noqa: F401 — ensure prompt tables are created by create_all


class TestPromptVersionCommitMessage:
    """Test commit message persistence on prompt versions."""

    async def test_create_prompt_with_commit_message(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello {{name}}",
            commit_message="Initial version",
        )
        result = await svc.create_prompt(data)
        assert result.version.commit_message == "Initial version"

    async def test_create_prompt_without_commit_message(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello {{name}}",
        )
        result = await svc.create_prompt(data)
        assert result.version.commit_message is None

    async def test_update_prompt_with_commit_message(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello {{name}}",
        )
        created = await svc.create_prompt(data)

        update = PromptUpdateSchema(
            template="Hello {{name}}, welcome!",
            commit_message="Added welcome message",
        )
        updated = await svc.update_prompt(created.id, update)
        assert updated.version.commit_message == "Added welcome message"

    async def test_version_listing_includes_commit_messages(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="v1",
            commit_message="Initial",
        )
        created = await svc.create_prompt(data)
        await svc.update_prompt(
            created.id,
            PromptUpdateSchema(template="v2", commit_message="Update 2"),
        )
        await svc.update_prompt(
            created.id,
            PromptUpdateSchema(template="v3"),
        )

        versions = await svc.list_versions(created.id)
        assert len(versions) == 3
        assert versions[0].commit_message == "Initial"
        assert versions[1].commit_message == "Update 2"
        assert versions[2].commit_message is None

    async def test_rollback_preserves_commit_message(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="v1",
            commit_message="Original",
        )
        created = await svc.create_prompt(data)
        await svc.update_prompt(
            created.id,
            PromptUpdateSchema(template="v2", commit_message="Changed"),
        )

        rolled = await svc.rollback_to_version(created.id, 1)
        assert rolled.version.commit_message == "Rollback to version 1"


class TestProtectedLabels:
    """Test protected label RBAC enforcement."""

    async def test_admin_can_add_protected_label(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)

        update = PromptUpdateSchema(template="Hello updated", labels=["production"])
        result = await svc.update_prompt(created.id, update, user_role="admin")
        assert "production" in result.version.labels

    async def test_non_admin_blocked_from_protected_label(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)

        update = PromptUpdateSchema(template="Hello updated", labels=["production"])
        with pytest.raises(PermissionError, match="admin role"):
            await svc.update_prompt(created.id, update, user_role="editor")

    async def test_non_admin_can_modify_non_protected_labels(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)

        update = PromptUpdateSchema(template="Hello updated", labels=["staging"])
        result = await svc.update_prompt(created.id, update, user_role="editor")
        assert "staging" in result.version.labels

    async def test_admin_can_remove_protected_label(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
            labels=["production", "staging"],
        )
        created = await svc.create_prompt(data)

        update = PromptUpdateSchema(template="Hello updated", labels=["staging"])
        result = await svc.update_prompt(created.id, update, user_role="admin")
        assert "production" not in result.version.labels
        assert "staging" in result.version.labels

    async def test_check_protected_labels_static_method(self) -> None:
        from hecate.services.prompt_service import PromptService

        svc = PromptService.__new__(PromptService)
        svc._check_protected_labels([], ["production"], "admin")

        with pytest.raises(PermissionError):
            svc._check_protected_labels([], ["production"], "editor")

        svc._check_protected_labels([], ["staging"], "editor")


class TestPromptAnalyticsService:
    """Test prompt analytics service diff computation."""

    async def test_compute_diff_identical_templates(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello {{name}}",
        )
        created = await svc.create_prompt(data)

        analytics = PromptAnalyticsService(db_session)
        result = await analytics.compute_diff(created.id, 1, 1)
        assert result["added_lines"] == 0
        assert result["removed_lines"] == 0
        assert result["token_delta"] == 0

    async def test_compute_diff_different_templates(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Line one\nLine two\nLine three",
        )
        created = await svc.create_prompt(data)
        await svc.update_prompt(
            created.id,
            PromptUpdateSchema(template="Line one\nLine TWO\nLine three\nLine four"),
        )

        analytics = PromptAnalyticsService(db_session)
        result = await analytics.compute_diff(created.id, 1, 2)
        assert result["added_lines"] >= 2
        assert result["removed_lines"] >= 1

    async def test_compute_diff_nonexistent_version(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)

        analytics = PromptAnalyticsService(db_session)
        with pytest.raises(ValueError, match="Version 99 not found"):
            await analytics.compute_diff(created.id, 1, 99)

    async def test_get_version_analytics_empty(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)

        analytics = PromptAnalyticsService(db_session)
        result = await analytics.get_version_analytics(created.id, 1, days=30)
        assert result["total_calls"] == 0
        assert result["avg_latency_ms"] == 0.0
        assert result["error_rate"] == 0.0
        assert result["daily_breakdown"] == []

    async def test_compare_versions(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="v1",
        )
        created = await svc.create_prompt(data)
        await svc.update_prompt(created.id, PromptUpdateSchema(template="v2"))

        analytics = PromptAnalyticsService(db_session)
        result = await analytics.compare_versions(created.id, 1, 2, days=30)
        assert result["from_version"] == 1
        assert result["to_version"] == 2
        assert "deltas" in result

    async def test_generate_change_summary_initial_version(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)

        analytics = PromptAnalyticsService(db_session)
        result = await analytics.generate_change_summary(created.id, 1)
        assert result["is_initial"] is True
        assert "initial" in result["summary"].lower()

    async def test_generate_change_summary_no_changes(self, db_session) -> None:
        from hecate.models.prompt import PromptCreateSchema, PromptUpdateSchema
        from hecate.services.prompt_analytics_service import PromptAnalyticsService
        from hecate.services.prompt_service import PromptService

        svc = PromptService(db_session)
        data = PromptCreateSchema(
            name=f"test-{uuid.uuid4().hex[:8]}",
            template="Hello",
        )
        created = await svc.create_prompt(data)
        await svc.update_prompt(
            created.id,
            PromptUpdateSchema(template="Hello", labels=["staging"]),
        )

        analytics = PromptAnalyticsService(db_session)
        result = await analytics.generate_change_summary(created.id, 2)
        assert result["is_initial"] is False
        assert "no template changes" in result["summary"].lower()
