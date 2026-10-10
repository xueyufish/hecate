"""Resume-marker threading tests for the workflow execution service (step6d).

The kernel-level continuation mechanics (checkpoint restore + event-log
fold) are covered by the runtime suites; these tests pin the service
boundary: ``resume_interrupted=True`` MUST switch the Pregel invocation
to the resume path (no ``initial_input`` rewrite, non-None
``resume_value``) on the SAME engine session.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from hecate.studio.workflows.execution_service import WorkflowExecutionService


def _make_port(tokens: list[str] | None = None) -> MagicMock:
    port = MagicMock()
    tokens = tokens or ["Hello!"]

    async def fake_context_assemble(*args, **kwargs):
        return {"messages": kwargs.get("messages", []), "tools": kwargs.get("tools"), "metadata": {}}

    port.context_assemble = AsyncMock(side_effect=fake_context_assemble)

    async def fake_llm_invoke(*args, **kwargs):
        for t in tokens:
            yield t

    port.llm_invoke = fake_llm_invoke
    port.tool_execute = AsyncMock(return_value="tool result")
    port.knowledge_query = AsyncMock(return_value="knowledge context")
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    return port


class TestWorkflowExecutionServiceResumeThreading:
    async def test_resume_interrupted_switches_to_resume_path(self, monkeypatch) -> None:
        port = _make_port(["Resumed answer"])
        service = WorkflowExecutionService(port=port)
        captured: dict = {}

        async def fake_non_stream(runtime, session_id, initial_input, execution_mode, resume_value=None):
            captured["initial_input"] = initial_input
            captured["resume_value"] = resume_value
            captured["session_id"] = session_id
            return {"content": "Resumed answer", "model": "stub-model", "usage": {}, "finish_reason": "stop"}

        monkeypatch.setattr(service, "_non_stream_execute", fake_non_stream)
        session = uuid.uuid4()
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "continue the work"}],
            model="stub-model",
            session_id=session,
            resume_interrupted=True,
        )

        assert result["content"] == "Resumed answer"
        # The user message MUST NOT be rewritten as a new initial_input; the
        # kernel restores the interrupted session from checkpoint + log tail.
        assert captured["initial_input"] is None
        assert captured["resume_value"] is not None
        assert captured["resume_value"]["resumed"] == "interrupted_attempt"
        assert captured["session_id"] == session

    async def test_default_execution_keeps_initial_input_and_no_resume(self, monkeypatch) -> None:
        port = _make_port(["Fresh answer"])
        service = WorkflowExecutionService(port=port)
        captured: dict = {}

        async def fake_non_stream(runtime, session_id, initial_input, execution_mode, resume_value=None):
            captured["initial_input"] = initial_input
            captured["resume_value"] = resume_value
            return {"content": "Fresh answer", "model": "stub-model", "usage": {}, "finish_reason": "stop"}

        monkeypatch.setattr(service, "_non_stream_execute", fake_non_stream)
        result = await service.execute(
            agent_mode="chat",
            messages=[{"role": "user", "content": "start the work"}],
            model="stub-model",
        )

        assert result["content"] == "Fresh answer"
        assert captured["initial_input"] is not None
        assert captured["resume_value"] is None
