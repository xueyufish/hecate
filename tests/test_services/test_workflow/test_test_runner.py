"""Tests for WorkflowTestRunner service."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.models.workflow import WorkflowModel, WorkflowVersionModel
from hecate.studio.workflows.test_runner import WorkflowTestRunner

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

    from hecate.runtime.compiler import GraphCompiler
    from hecate.studio.workflows.graph_dsl import parse_graph

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
async def test_run_result_has_timing(db_session: AsyncSession, monkeypatch) -> None:
    """Verify elapsed milliseconds without depending on wall-clock speed."""
    from types import SimpleNamespace

    ticks = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr("hecate.studio.workflows.test_runner.time", SimpleNamespace(monotonic=lambda: next(ticks)))
    wf_id = await _create_workflow_with_dsl(db_session, LINEAR_DSL)
    runner = WorkflowTestRunner(db_session)

    result = await runner.run_test(
        workflow_id=wf_id,
        input_data={},
        mock=True,
    )

    assert result.total_duration_ms == 5000
    assert result.run_id is not None

    for node in result.nodes:
        if node.status == "completed":
            assert node.duration_ms == 1000


@pytest.mark.asyncio
async def test_run_real_mode(db_session: AsyncSession) -> None:
    """Real (non-mock) mode executes conversation nodes through the LLM service."""
    from unittest.mock import AsyncMock, patch

    from hecate_llm.service import LLMResponse, llm_service

    wf_id = await _create_workflow_with_dsl(db_session, LINEAR_DSL)
    runner = WorkflowTestRunner(db_session)

    chat_mock = AsyncMock(return_value=LLMResponse(content="real output", model="mock"))
    with patch.object(llm_service, "chat", chat_mock):
        result = await runner.run_test(
            workflow_id=wf_id,
            input_data={},
            mock=False,
        )

    assert result.status == "completed"
    assert len(result.nodes) >= 2
    # Every conversation node invoked the LLM and emitted its response.
    assert chat_mock.await_count >= 2
    for node in result.nodes:
        assert node.status == "completed"
        assert node.output["messages"][-1]["content"] == "real output"


@pytest.mark.asyncio
async def test_run_real_mode_propagates_llm_failure(db_session: AsyncSession) -> None:
    """A failing LLM call marks the run as errored instead of faking output."""
    from unittest.mock import AsyncMock, patch

    from hecate_llm.service import llm_service

    wf_id = await _create_workflow_with_dsl(db_session, LINEAR_DSL)
    runner = WorkflowTestRunner(db_session)

    with patch.object(llm_service, "chat", AsyncMock(side_effect=RuntimeError("no credentials"))):
        result = await runner.run_test(
            workflow_id=wf_id,
            input_data={},
            mock=False,
        )

    assert result.status == "error"
    assert result.error is not None
