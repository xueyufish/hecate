"""Tests for workflow execution mode and version management features."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import GraphValidationError, parse_graph
from hecate.engine.types import ExecutionMode
from hecate.models.workflow import (
    WorkflowCreateSchema,
    WorkflowUpdateSchema,
)

CONVERSATIONAL_DSL = {
    "version": "1.0",
    "name": "test",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}

SUGGESTION_DSL = {
    "version": "1.0",
    "name": "with-suggestion",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "S": {"type": "suggestion", "config": {}},
    },
    "edges": [{"source": "A", "target": "S"}],
    "entry": "A",
}

MULTI_NODE_DSL = {
    "version": "1.0",
    "name": "multi",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
    },
    "edges": [{"source": "A", "target": "B"}],
    "entry": "A",
}


class TestExecutionModeEnum:
    """Test ExecutionMode StrEnum values."""

    def test_conversational_value(self) -> None:
        assert ExecutionMode.CONVERSATIONAL == "conversational"

    def test_task_value(self) -> None:
        assert ExecutionMode.TASK == "task"

    def test_enum_members(self) -> None:
        assert set(ExecutionMode.__members__) == {"CONVERSATIONAL", "TASK"}


class TestCompilerExecutionMode:
    """Test GraphCompiler.compile() execution_mode validation."""

    def test_conversational_allows_suggestion(self) -> None:
        graph_config = parse_graph(SUGGESTION_DSL)
        compiler = GraphCompiler()
        compiled = compiler.compile(graph_config, execution_mode="conversational")
        assert compiled is not None

    def test_task_mode_rejects_suggestion(self) -> None:
        graph_config = parse_graph(SUGGESTION_DSL)
        compiler = GraphCompiler()
        with pytest.raises(GraphValidationError, match="task mode"):
            compiler.compile(graph_config, execution_mode="task")

    def test_task_mode_allows_conversation_only(self) -> None:
        graph_config = parse_graph(CONVERSATIONAL_DSL)
        compiler = GraphCompiler()
        compiled = compiler.compile(graph_config, execution_mode="task")
        assert compiled is not None

    def test_task_mode_allows_multi_node(self) -> None:
        graph_config = parse_graph(MULTI_NODE_DSL)
        compiler = GraphCompiler()
        compiled = compiler.compile(graph_config, execution_mode="task")
        assert compiled is not None

    def test_default_mode_is_conversational(self) -> None:
        graph_config = parse_graph(SUGGESTION_DSL)
        compiler = GraphCompiler()
        compiled = compiler.compile(graph_config)
        assert compiled is not None


class TestWorkflowModelExecutionMode:
    """Test WorkflowModel execution_mode field via service layer."""

    async def test_create_default_mode(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        result = await service.create_workflow(data)

        assert result.execution_mode == "conversational"

    async def test_create_task_mode(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(
            name="test-task",
            graph_dsl=CONVERSATIONAL_DSL,
            execution_mode="task",
        )
        result = await service.create_workflow(data)

        assert result.execution_mode == "task"

    async def test_update_execution_mode(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        update = WorkflowUpdateSchema(execution_mode="task")
        updated = await service.update_workflow(created.id, update)

        assert updated.execution_mode == "task"

    async def test_update_mode_with_suggestion_graph_fails(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=SUGGESTION_DSL)
        created = await service.create_workflow(data)

        update = WorkflowUpdateSchema(execution_mode="task")
        with pytest.raises(GraphValidationError, match="task mode"):
            await service.update_workflow(created.id, update)


class TestWorkflowPublishVersion:
    """Test WorkflowService.publish_version()."""

    async def test_publish_sets_pointer(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        result = await service.publish_version(created.id, 1)
        assert result.published_version == 1

    async def test_publish_adds_production_label(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        await service.publish_version(created.id, 1)
        version = await service.get_version(created.id, 1)
        assert "production" in version.labels

    async def test_republish_moves_label(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        update = WorkflowUpdateSchema(graph_dsl=MULTI_NODE_DSL)
        await service.update_workflow(created.id, update)

        await service.publish_version(created.id, 1)
        await service.publish_version(created.id, 2)

        v1 = await service.get_version(created.id, 1)
        assert "production" not in v1.labels

        v2 = await service.get_version(created.id, 2)
        assert "production" in v2.labels

    async def test_publish_nonexistent_version_raises(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        with pytest.raises(ValueError, match="not found"):
            await service.publish_version(created.id, 99)

    async def test_publish_nonexistent_workflow_raises(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        with pytest.raises(ValueError, match="not found"):
            await service.publish_version(uuid.uuid4(), 1)


class TestWorkflowVersionLabel:
    """Test WorkflowService.get_version_by_label()."""

    async def test_find_by_label(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        await service.publish_version(created.id, 1)

        result = await service.get_version_by_label(created.id, "production")
        assert result is not None
        assert result.version == 1

    async def test_find_by_label_not_found(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        result = await service.get_version_by_label(created.id, "staging")
        assert result is None


class TestWorkflowPublishedVersion:
    """Test WorkflowService.get_published_version()."""

    async def test_get_published(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        await service.publish_version(created.id, 1)

        result = await service.get_published_version(created.id)
        assert result.version == 1

    async def test_get_published_none_raises(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        with pytest.raises(ValueError, match="no published version"):
            await service.get_published_version(created.id)


class TestWorkflowDiffVersions:
    """Test WorkflowService.diff_versions()."""

    async def test_identical_versions(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        update = WorkflowUpdateSchema(graph_dsl=CONVERSATIONAL_DSL)
        await service.update_workflow(created.id, update)

        result = await service.diff_versions(created.id, 1, 2)
        assert result["identical"] is True
        assert result["v1"] == 1
        assert result["v2"] == 2

    async def test_different_versions(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        update = WorkflowUpdateSchema(graph_dsl=MULTI_NODE_DSL)
        await service.update_workflow(created.id, update)

        result = await service.diff_versions(created.id, 1, 2)
        assert result["identical"] is False
        assert result["summary"]["values_changed"] > 0 or result["summary"]["dictionary_item_added"] > 0

    async def test_diff_nonexistent_version_raises(self, db_session: AsyncSession) -> None:
        from hecate.services.workflow_service import WorkflowService

        service = WorkflowService(db_session)
        data = WorkflowCreateSchema(name="test", graph_dsl=CONVERSATIONAL_DSL)
        created = await service.create_workflow(data)

        with pytest.raises(ValueError, match="not found"):
            await service.diff_versions(created.id, 1, 99)


class TestWorkflowAPIPublishDiffPublished:
    """Test publish, diff, published API endpoints."""

    async def test_publish_endpoint(self, client: AsyncClient) -> None:
        create_data = {"name": "test", "graph_dsl": CONVERSATIONAL_DSL}
        create_resp = await client.post("/api/workflows", json=create_data)
        workflow_id = create_resp.json()["id"]

        response = await client.post(f"/api/workflows/{workflow_id}/publish/1")
        assert response.status_code == 200

        result = response.json()
        assert result["published_version"] == 1

    async def test_diff_endpoint(self, client: AsyncClient) -> None:
        create_data = {"name": "test", "graph_dsl": CONVERSATIONAL_DSL}
        create_resp = await client.post("/api/workflows", json=create_data)
        workflow_id = create_resp.json()["id"]

        update_data = {"graph_dsl": MULTI_NODE_DSL}
        await client.put(f"/api/workflows/{workflow_id}", json=update_data)

        response = await client.get(
            f"/api/workflows/{workflow_id}/diff?v1=1&v2=2",
        )
        assert response.status_code == 200

        result = response.json()
        assert "identical" in result
        assert "summary" in result

    async def test_published_endpoint(self, client: AsyncClient) -> None:
        create_data = {"name": "test", "graph_dsl": CONVERSATIONAL_DSL}
        create_resp = await client.post("/api/workflows", json=create_data)
        workflow_id = create_resp.json()["id"]

        await client.post(f"/api/workflows/{workflow_id}/publish/1")

        response = await client.get(f"/api/workflows/{workflow_id}/published")
        assert response.status_code == 200

        result = response.json()
        assert result["version"] == 1

    async def test_published_not_found(self, client: AsyncClient) -> None:
        create_data = {"name": "test", "graph_dsl": CONVERSATIONAL_DSL}
        create_resp = await client.post("/api/workflows", json=create_data)
        workflow_id = create_resp.json()["id"]

        response = await client.get(f"/api/workflows/{workflow_id}/published")
        assert response.status_code == 404

    async def test_publish_not_found_version(self, client: AsyncClient) -> None:
        create_data = {"name": "test", "graph_dsl": CONVERSATIONAL_DSL}
        create_resp = await client.post("/api/workflows", json=create_data)
        workflow_id = create_resp.json()["id"]

        response = await client.post(f"/api/workflows/{workflow_id}/publish/99")
        assert response.status_code == 404

    async def test_create_with_task_mode(self, client: AsyncClient) -> None:
        create_data = {
            "name": "task-workflow",
            "graph_dsl": CONVERSATIONAL_DSL,
            "execution_mode": "task",
        }
        response = await client.post("/api/workflows", json=create_data)
        assert response.status_code == 201

        result = response.json()
        assert result["execution_mode"] == "task"

    async def test_create_task_mode_with_suggestion_fails(self, client: AsyncClient) -> None:
        create_data = {
            "name": "bad-task",
            "graph_dsl": SUGGESTION_DSL,
            "execution_mode": "task",
        }
        response = await client.post("/api/workflows", json=create_data)
        assert response.status_code == 422

    async def test_default_execution_mode_is_conversational(self, client: AsyncClient) -> None:
        create_data = {"name": "default-mode", "graph_dsl": CONVERSATIONAL_DSL}
        response = await client.post("/api/workflows", json=create_data)
        assert response.status_code == 201

        result = response.json()
        assert result["execution_mode"] == "conversational"
        assert result["published_version"] is None
