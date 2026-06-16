"""Tests for WorkflowTestRunner service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workflow import WorkflowModel, WorkflowVersionModel
from hecate.services.workflow.test_runner import WorkflowTestRunner

LINEAR_DSL = {
    "version": "1.0",
    "name": "linear",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "A": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "B": {"type": "conversation", "config": {"model": "gpt-4o"}},
    },
    "edges": [{"source": "A", "target": "B"}],
    "entry": "A",
}

CONDITION_DSL = {
    "version": "1.0",
    "name": "conditional",
    "state": {"messages": {"type": "topic", "reduce": "append"}},
    "nodes": {
        "start": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "check": {"type": "condition", "config": {"expression": "true"}},
        "yes": {"type": "conversation", "config": {"model": "gpt-4o"}},
        "no": {"type": "conversation", "config": {"model": "gpt-4o"}},
    },
    "edges": [
        {"source": "start", "target": "check"},
        {"source": "check", "target": {"true": "yes", "false": "no"}},
    ],
    "entry": "start",
}


async def _create_workflow_with_dsl(db: AsyncSession, dsl: dict) -> uuid.UUID:
    """Helper: insert a workflow + version, return the workflow ID."""
    wf_id = uuid.uuid4()
    wf = WorkflowModel(id=wf_id, name=dsl["name"], current_version=1)
    db.add(wf)

    from hecate.engine.compiler import GraphCompiler
    from hecate.engine.graph_dsl import parse_graph

    graph_config = parse_graph(dsl)
    compiled = GraphCompiler().compile(graph_config)

    version = WorkflowVersionModel(
        workflow_id=wf_id,
        version=1,
        graph_dsl=dsl,
        compiled_graph=compiled.to_json(),
    )
    db.add(version)
    await db.flush()
    return wf_id


@pytest.mark.asyncio
async def test_run_mock_linear(db_session: AsyncSession) -> None:
    """Test mock run of a linear graph executes all nodes."""
    wf_id = await _create_workflow_with_dsl(db_session, LINEAR_DSL)
    runner = WorkflowTestRunner(db_session)

    result = await runner.run_test(
        workflow_id=wf_id,
        input_data={"messages": [{"role": "user", "content": "test"}]},
        mock=True,
    )

    assert result.status == "completed"
    assert len(result.nodes) >= 2

    node_ids = {n.node_id for n in result.nodes}
    assert "A" in node_ids
    assert "B" in node_ids

    for node in result.nodes:
        if node.node_id in ("A", "B"):
            assert node.status in ("completed", "skipped")


@pytest.mark.asyncio
async def test_run_mock_conditional(db_session: AsyncSession) -> None:
    """Test mock run of a conditional graph."""
    wf_id = await _create_workflow_with_dsl(db_session, CONDITION_DSL)
    runner = WorkflowTestRunner(db_session)

    result = await runner.run_test(
        workflow_id=wf_id,
        input_data={"messages": []},
        mock=True,
    )

    assert result.status == "completed"
    assert len(result.nodes) >= 1

    node_ids = {n.node_id for n in result.nodes}
    assert "start" in node_ids


@pytest.mark.asyncio
async def test_run_no_version(db_session: AsyncSession) -> None:
    """Test run with non-existent workflow raises ValueError."""
    runner = WorkflowTestRunner(db_session)

    with pytest.raises(ValueError, match="no compiled version"):
        await runner.run_test(
            workflow_id=uuid.uuid4(),
            input_data={},
            mock=True,
        )


@pytest.mark.asyncio
async def test_run_result_has_timing(db_session: AsyncSession) -> None:
    """Test that result includes timing information."""
    wf_id = await _create_workflow_with_dsl(db_session, LINEAR_DSL)
    runner = WorkflowTestRunner(db_session)

    result = await runner.run_test(
        workflow_id=wf_id,
        input_data={},
        mock=True,
    )

    assert result.total_duration_ms > 0
    assert result.run_id is not None

    for node in result.nodes:
        if node.status == "completed":
            assert node.duration_ms >= 0


@pytest.mark.asyncio
async def test_run_real_mode(db_session: AsyncSession) -> None:
    """Test real (non-mock) mode still runs without LLM."""
    wf_id = await _create_workflow_with_dsl(db_session, LINEAR_DSL)
    runner = WorkflowTestRunner(db_session)

    result = await runner.run_test(
        workflow_id=wf_id,
        input_data={},
        mock=False,
    )

    # Real mode still works — _TestWorker just produces different canned text
    assert result.status == "completed"
    assert len(result.nodes) >= 2
