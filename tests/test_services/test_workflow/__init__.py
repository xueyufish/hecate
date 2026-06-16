"""Unit tests for WorkflowModel and schemas."""

from __future__ import annotations

import uuid

from hecate.models.workflow import (
    WorkflowCreateSchema,
    WorkflowDetailSchema,
    WorkflowModel,
    WorkflowReadSchema,
    WorkflowUpdateSchema,
    WorkflowVersionModel,
    WorkflowVersionReadSchema,
)


class TestWorkflowModel:
    """Tests for WorkflowModel ORM."""

    def test_workflow_model_fields(self) -> None:
        """Test WorkflowModel has required fields."""
        workflow = WorkflowModel(
            name="test-workflow",
            workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000000"),
            current_version=1,
        )
        assert workflow.name == "test-workflow"
        assert workflow.current_version == 1

    def test_workflow_version_model_fields(self) -> None:
        """Test WorkflowVersionModel has required fields."""
        version = WorkflowVersionModel(
            workflow_id=uuid.uuid4(),
            version=1,
            graph_dsl={"version": "1.0", "name": "test", "nodes": {}, "edges": []},
            compiled_graph={"entry": "A"},
            change_summary="Initial",
        )
        assert version.version == 1
        assert version.change_summary == "Initial"


class TestWorkflowSchemas:
    """Tests for Pydantic schemas."""

    def test_create_schema(self) -> None:
        """Test WorkflowCreateSchema validation."""
        schema = WorkflowCreateSchema(
            name="test",
            graph_dsl={
                "version": "1.0",
                "name": "test",
                "state": {"messages": {"type": "topic", "reduce": "append"}},
                "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
                "edges": [],
                "entry": "A",
            },
        )
        assert schema.name == "test"
        assert schema.change_summary == ""

    def test_create_schema_with_summary(self) -> None:
        """Test WorkflowCreateSchema with change_summary."""
        schema = WorkflowCreateSchema(
            name="test",
            graph_dsl={
                "version": "1.0",
                "name": "test",
                "state": {"messages": {"type": "topic", "reduce": "append"}},
                "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
                "edges": [],
                "entry": "A",
            },
            change_summary="First version",
        )
        assert schema.change_summary == "First version"

    def test_update_schema_partial(self) -> None:
        """Test WorkflowUpdateSchema with partial data."""
        schema = WorkflowUpdateSchema(name="updated")
        assert schema.name == "updated"
        assert schema.graph_dsl is None

    def test_read_schema(self) -> None:
        """Test WorkflowReadSchema from model."""
        workflow = WorkflowModel(
            name="test",
            workspace_id=uuid.uuid4(),
            current_version=1,
        )
        workflow.id = uuid.uuid4()
        schema = WorkflowReadSchema.model_validate(workflow)
        assert schema.name == "test"

    def test_version_read_schema(self) -> None:
        """Test WorkflowVersionReadSchema from model."""
        version = WorkflowVersionModel(
            workflow_id=uuid.uuid4(),
            version=1,
            graph_dsl={"version": "1.0"},
            compiled_graph={"entry": "A"},
            change_summary="Initial",
        )
        version.id = uuid.uuid4()
        schema = WorkflowVersionReadSchema.model_validate(version)
        assert schema.version == 1

    def test_detail_schema(self) -> None:
        """Test WorkflowDetailSchema construction."""
        schema = WorkflowDetailSchema(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            name="test",
            current_version=1,
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
            deleted_at=None,
        )
        assert schema.name == "test"
        assert schema.version is None
