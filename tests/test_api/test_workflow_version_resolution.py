"""Tests for workflow runtime version resolution (1.3.20).

``_load_workflow_graph`` selection semantics:

- ``published_preferred`` (default) runs the published version when one
  exists, falling back to the latest for never-published workflows.
- ``latest_for_studio`` always runs the latest draft (workflow editor
  test-runs).
- An explicit ``version`` (an agent snapshot's pinned workflow reference)
  wins over both.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workflow import WorkflowModel, WorkflowVersionModel
from hecate.studio.workflows.execution_service import WorkflowExecutionService

DSL_V1 = {
    "version": "1.0",
    "name": "wf-v1",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {"A": {"type": "conversation", "config": {"model": "gpt-4o"}}},
    "edges": [],
    "entry": "A",
}
DSL_V2 = {
    "version": "1.0",
    "name": "wf-v2",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
    },
    "edges": [{"source": "A", "target": "B"}],
    "entry": "A",
}


async def _seed_workflow(db: AsyncSession, published_version: int | None) -> uuid.UUID:
    workflow = WorkflowModel(name="resolver-wf", current_version=2, published_version=published_version)
    db.add(workflow)
    await db.flush()
    db.add(
        WorkflowVersionModel(
            workflow_id=workflow.id,
            version=1,
            graph_dsl=DSL_V1,
            workspace_id=workflow.workspace_id,
        )
    )
    db.add(
        WorkflowVersionModel(
            workflow_id=workflow.id,
            version=2,
            graph_dsl=DSL_V2,
            workspace_id=workflow.workspace_id,
        )
    )
    await db.flush()
    return workflow.id


def _service(db: AsyncSession) -> WorkflowExecutionService:
    return WorkflowExecutionService(port=None, db=db)


@pytest.mark.asyncio
async def test_published_preferred_runs_published_version(db_session: AsyncSession) -> None:
    workflow_id = await _seed_workflow(db_session, published_version=1)
    graph = await _service(db_session)._load_workflow_graph(workflow_id)
    assert set(graph.nodes.keys()) == {"A"}


@pytest.mark.asyncio
async def test_never_published_falls_back_to_latest(db_session: AsyncSession) -> None:
    workflow_id = await _seed_workflow(db_session, published_version=None)
    graph = await _service(db_session)._load_workflow_graph(workflow_id)
    assert set(graph.nodes.keys()) == {"A", "B"}


@pytest.mark.asyncio
async def test_latest_for_studio_runs_draft(db_session: AsyncSession) -> None:
    workflow_id = await _seed_workflow(db_session, published_version=1)
    graph = await _service(db_session)._load_workflow_graph(workflow_id, selector="latest_for_studio")
    assert set(graph.nodes.keys()) == {"A", "B"}


@pytest.mark.asyncio
async def test_exact_version_wins_over_selector(db_session: AsyncSession) -> None:
    workflow_id = await _seed_workflow(db_session, published_version=1)
    service = _service(db_session)
    graph = await service._load_workflow_graph(workflow_id, version=2)
    assert set(graph.nodes.keys()) == {"A", "B"}
    graph = await service._load_workflow_graph(workflow_id, version=1, selector="latest_for_studio")
    assert set(graph.nodes.keys()) == {"A"}


@pytest.mark.asyncio
async def test_missing_pinned_version_raises(db_session: AsyncSession) -> None:
    workflow_id = await _seed_workflow(db_session, published_version=1)
    with pytest.raises(ValueError, match="no compiled version"):
        await _service(db_session)._load_workflow_graph(workflow_id, version=99)
