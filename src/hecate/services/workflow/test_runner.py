"""Workflow test runner service.

Executes compiled workflow graphs in test mode with per-node status tracking.
Supports mock mode for testing without LLM API consumption.
Persists run results to the database for history retrieval.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from hecate.engine.checkpoint import InMemoryCheckpointStore
from hecate.engine.compiler import GraphCompiler
from hecate.engine.graph_dsl import parse_graph
from hecate.engine.pregel import PregelRuntime
from hecate.engine.types import NodeType, StreamMode, WorkerResult
from hecate.engine.worker import Worker
from hecate.models.workflow import WorkflowRunModel, WorkflowVersionModel

logger = logging.getLogger(__name__)


@dataclass
class NodeExecutionResult:
    """Result of a single node execution in a test run."""

    node_id: str
    node_type: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    duration_ms: int = 0


@dataclass
class TestRunResult:
    """Complete result of a workflow test run."""

    run_id: uuid.UUID
    status: str
    nodes: list[NodeExecutionResult] = field(default_factory=list)
    total_duration_ms: int = 0
    error: str | None = None


class _TestWorker(Worker):
    """Worker that tracks per-node execution results for test runs.

    Produces type-specific mock outputs for each NodeType:
    - conversation: assistant message
    - tool-call: tool result
    - condition: evaluates expression (simplified)
    - agent: delegated agent response
    - knowledge-retrieval: mock retrieved documents
    - variable-set: writes variable to channel
    """

    def __init__(self, mock: bool = False, node_types: dict[str, NodeType] | None = None) -> None:
        self._mock = mock
        self._node_results: dict[str, NodeExecutionResult] = {}
        self._node_types: dict[str, NodeType] = node_types or {}

    async def execute(self, node_id: str, node_config: dict, channel_snapshot: dict) -> WorkerResult:
        start = time.monotonic()
        node_type_enum = self._node_types.get(node_id, NodeType.CONVERSATION)
        node_type = node_type_enum.value
        channel_updates: dict[str, Any] = {}
        error: Exception | None = None

        try:
            if self._mock:
                channel_updates = self._mock_execute(node_id, node_type, node_config, channel_snapshot)
            else:
                channel_updates = self._real_execute(node_id, node_type, node_config, channel_snapshot)
        except Exception as e:
            error = e

        duration_ms = int((time.monotonic() - start) * 1000)

        status = "error" if error else "completed"
        self._node_results[node_id] = NodeExecutionResult(
            node_id=node_id,
            node_type=node_type,
            status=status,
            output=channel_updates,
            error_message=str(error) if error else None,
            duration_ms=duration_ms,
        )

        if error:
            return WorkerResult(node_id=node_id, error=error)

        return WorkerResult(node_id=node_id, channel_updates=channel_updates)

    def _mock_execute(
        self,
        node_id: str,
        node_type: str,
        node_config: dict,
        channel_snapshot: dict,
    ) -> dict[str, Any]:
        """Produce type-specific mock output based on node type."""
        if node_type == NodeType.CONVERSATION.value:
            return {"messages": [{"role": "assistant", "content": f"Mock LLM response from {node_id}"}]}
        if node_type == NodeType.TOOL_CALL.value:
            tool_name = node_config.get("tool_name", "unknown_tool")
            return {"messages": [{"role": "tool", "content": f"Mock result from {tool_name}"}]}
        if node_type == NodeType.CONDITION.value:
            return {"messages": []}
        if node_type == NodeType.AGENT.value:
            return {"messages": [{"role": "assistant", "content": f"Mock agent response from {node_id}"}]}
        if node_type == NodeType.KNOWLEDGE_RETRIEVAL.value:
            kb_ids = node_config.get("kb_ids", [])
            return {
                "messages": [
                    {
                        "role": "system",
                        "content": f"Retrieved 3 docs from {len(kb_ids)} knowledge bases",
                    }
                ],
                "context": "Mock knowledge retrieval results",
            }
        if node_type == NodeType.VARIABLE_SET.value:
            var_name = node_config.get("variable_name", "unknown")
            var_value = node_config.get("value", "")
            return {
                "messages": [],
                var_name: var_value,
            }
        return {"messages": [{"role": "assistant", "content": f"Mock response from {node_id}"}]}

    def _real_execute(
        self,
        node_id: str,
        node_type: str,
        node_config: dict,
        channel_snapshot: dict,
    ) -> dict[str, Any]:
        """Produce generic output for non-mock test runs."""
        if node_type == NodeType.KNOWLEDGE_RETRIEVAL.value:
            return {"messages": [{"role": "system", "content": f"Executed knowledge retrieval on {node_id}"}]}
        if node_type == NodeType.VARIABLE_SET.value:
            var_name = node_config.get("variable_name", "unknown")
            return {"messages": [], var_name: node_config.get("value", "")}
        return {"messages": [{"role": "assistant", "content": f"Executed node {node_id}"}]}

    def get_results(self) -> list[NodeExecutionResult]:
        return list(self._node_results.values())


class WorkflowTestRunner:
    """Service for running workflow test executions with per-node tracking."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run_test(
        self,
        workflow_id: uuid.UUID,
        input_data: dict[str, Any],
        mock: bool = False,
    ) -> TestRunResult:
        """Execute a workflow in test mode and return per-node results.

        Args:
            workflow_id: The workflow to test.
            input_data: Input payload (e.g., ``{"messages": [...]}``).
            mock: If True, replace LLM calls with canned responses.

        Returns:
            TestRunResult with per-node execution status and output.

        Raises:
            ValueError: If workflow has no compiled version.
        """
        run_id = uuid.uuid4()
        start = time.monotonic()

        version = await self._get_current_version(workflow_id)
        if version is None:
            raise ValueError(f"Workflow {workflow_id} has no compiled version")

        try:
            graph_config = parse_graph(version.graph_dsl)
            compiled = GraphCompiler().compile(graph_config)

            node_types = {nid: ncfg.type for nid, ncfg in compiled.nodes.items()}
            worker = _TestWorker(mock=mock, node_types=node_types)
            checkpoint_store = InMemoryCheckpointStore()
            session_id = uuid.uuid4()

            runtime = PregelRuntime(
                graph=compiled,
                worker=worker,
                checkpoint_store=checkpoint_store,
            )

            async for _event in runtime.execute(
                session_id=session_id,
                initial_input=input_data,
                stream_mode=StreamMode.UPDATES,
            ):
                pass

            node_results = worker.get_results()
            executed_ids = {r.node_id for r in node_results}
            for nid, ncfg in compiled.nodes.items():
                if nid not in executed_ids:
                    node_results.append(
                        NodeExecutionResult(
                            node_id=nid,
                            node_type=ncfg.type.value,
                            status="skipped",
                        )
                    )

            total_ms = int((time.monotonic() - start) * 1000)
            result = TestRunResult(
                run_id=run_id,
                status="completed",
                nodes=node_results,
                total_duration_ms=total_ms,
            )
            await self._persist_run(workflow_id, result, input_data, mock)
            return result
        except Exception as e:
            total_ms = int((time.monotonic() - start) * 1000)
            logger.warning(f"Test run failed for workflow {workflow_id}: {e}")
            result = TestRunResult(
                run_id=run_id,
                status="error",
                total_duration_ms=total_ms,
                error=str(e),
            )
            await self._persist_run(workflow_id, result, input_data, mock)
            return result

    async def _get_current_version(self, workflow_id: uuid.UUID) -> WorkflowVersionModel | None:
        result = await self.db.execute(
            select(WorkflowVersionModel)
            .where(
                WorkflowVersionModel.workflow_id == workflow_id,
                WorkflowVersionModel.deleted_at.is_(None),
            )
            .order_by(WorkflowVersionModel.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _persist_run(
        self,
        workflow_id: uuid.UUID,
        result: TestRunResult,
        input_data: dict[str, Any],
        mock: bool,
    ) -> None:
        """Persist test run results to the database."""
        node_results_data = [
            {
                "node_id": n.node_id,
                "node_type": n.node_type,
                "status": n.status,
                "output": n.output,
                "error_message": n.error_message,
                "duration_ms": n.duration_ms,
            }
            for n in result.nodes
        ]
        run = WorkflowRunModel(
            workflow_id=workflow_id,
            run_id=result.run_id,
            status=result.status,
            mock=mock,
            input_data=input_data,
            node_results=node_results_data,
            total_duration_ms=result.total_duration_ms,
            error=result.error,
        )
        self.db.add(run)
        await self.db.flush()

    async def list_runs(
        self,
        workflow_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """List test run history for a workflow with pagination.

        Args:
            workflow_id: The workflow to query runs for.
            page: Page number (1-indexed).
            page_size: Number of items per page.

        Returns:
            Dict with ``items`` (list of WorkflowRunModel) and ``total`` count.
        """
        base_query = (
            select(WorkflowRunModel)
            .where(
                WorkflowRunModel.workflow_id == workflow_id,
                WorkflowRunModel.deleted_at.is_(None),
            )
            .order_by(WorkflowRunModel.created_at.desc())
        )

        total_result = await self.db.execute(
            select(func.count()).select_from(base_query.with_only_columns(WorkflowRunModel.id).subquery())
        )
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        items_result = await self.db.execute(base_query.offset(offset).limit(page_size))
        items = list(items_result.scalars().all())

        return {"items": items, "total": total}
