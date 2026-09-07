from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from hecate.runtime.evidence import EvidenceTracker
from hecate.runtime.guardrail import GuardrailAction, GuardrailResult
from hecate.runtime.workers.tool_worker import ToolWorker


def _make_port(tool_result: str = "tool output") -> MagicMock:
    port = MagicMock()
    port.tool_execute = AsyncMock(return_value=tool_result)
    port.create_span = AsyncMock(return_value=None)
    port.end_span = AsyncMock(return_value=None)
    return port


class TestToolWorker:
    async def test_no_tool_calls(self) -> None:
        port = _make_port()
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert result.channel_updates["messages"] == []
        port.tool_execute.assert_not_called()

    async def test_tool_call_execution(self) -> None:
        port = _make_port("search results")
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {"query": "test"}}}],
                    }
                ]
            },
        )
        assert len(result.channel_updates["messages"]) == 1
        assert result.channel_updates["messages"][0]["role"] == "tool"
        assert result.channel_updates["messages"][0]["tool_call_id"] == "tc_1"
        assert "search results" in result.channel_updates["messages"][0]["content"]
        port.tool_execute.assert_called_once()

    async def test_multiple_tool_calls(self) -> None:
        port = MagicMock()
        port.tool_execute = AsyncMock(side_effect=["result_1", "result_2"])
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "tc_1", "function": {"name": "tool_a", "arguments": {}}},
                            {"id": "tc_2", "function": {"name": "tool_b", "arguments": {}}},
                        ],
                    }
                ]
            },
        )
        assert len(result.channel_updates["messages"]) == 2
        assert port.tool_execute.call_count == 2

    async def test_tool_execution_error(self) -> None:
        port = _make_port()
        port.tool_execute = AsyncMock(side_effect=RuntimeError("Tool failed"))
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "bad_tool", "arguments": {}}}],
                    }
                ]
            },
        )
        assert result.channel_updates["messages"][0]["is_error"] is True
        assert "Tool failed" in result.channel_updates["messages"][0]["content"]

    async def test_json_string_arguments(self) -> None:
        port = _make_port("parsed")
        worker = ToolWorker(port=port)
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "tc_1", "function": {"name": "search", "arguments": '{"query": "test"}'}}
                        ],
                    }
                ]
            },
        )
        call_args = port.tool_execute.call_args
        assert call_args.kwargs["args"] == {"query": "test"}

    async def test_invalid_json_arguments(self) -> None:
        port = _make_port("fallback")
        worker = ToolWorker(port=port)
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": "not-json"}}],
                    }
                ]
            },
        )
        call_args = port.tool_execute.call_args
        assert call_args.kwargs["args"] == {}

    async def test_pre_hook_blocks(self) -> None:
        port = _make_port()
        pre_hook = MagicMock()
        pre_hook.matcher = None
        pre_hook.on_pre_tool_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="Dangerous tool")
        )
        worker = ToolWorker(port=port, pre_tool_hook=pre_hook)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "dangerous", "arguments": {}}}],
                    }
                ]
            },
        )
        assert result.channel_updates["messages"][0]["is_error"] is True
        assert "Dangerous tool" in result.channel_updates["messages"][0]["content"]
        port.tool_execute.assert_not_called()

    async def test_post_hook_sanitizes(self) -> None:
        port = _make_port("sensitive data")
        post_hook = MagicMock()
        post_hook.matcher = None
        post_hook.on_post_tool_call = AsyncMock(
            return_value=GuardrailResult(action=GuardrailAction.BLOCK, reason="PII detected")
        )
        worker = ToolWorker(port=port, post_tool_hook=post_hook)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {}}}],
                    }
                ]
            },
        )
        assert "Result sanitized" in result.channel_updates["messages"][0]["content"]
        assert "PII detected" in result.channel_updates["messages"][0]["content"]

    async def test_post_hook_allows(self) -> None:
        port = _make_port("clean result")
        post_hook = MagicMock()
        post_hook.matcher = None
        post_hook.on_post_tool_call = AsyncMock(return_value=GuardrailResult(action=GuardrailAction.ALLOW))
        worker = ToolWorker(port=port, post_tool_hook=post_hook)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {}}}],
                    }
                ]
            },
        )
        assert "clean result" in result.channel_updates["messages"][0]["content"]
        assert result.channel_updates["messages"][0].get("is_error") is None


class TestToolWorkerAccessPolicy:
    async def test_no_policy_backward_compat(self) -> None:
        port = _make_port()
        worker = ToolWorker(port=port)
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "test", "arguments": {}}}]}
                ],
                "risk_level": "critical",
                "approval_required": True,
            },
        )
        port.tool_execute.assert_called_once()

    async def test_sandbox_routing(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ToolAccessPolicy

        worker = ToolWorker(port=port, access_policy=ToolAccessPolicy())
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "test", "arguments": {}}}]}
                ],
                "risk_level": "medium",
                "sandbox_enabled": True,
            },
        )
        port.tool_execute_sandbox.assert_called_once()

    async def test_approval_approved(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ApprovalCallback, ApprovalDecision, ToolAccessPolicy

        class AutoApprove(ApprovalCallback):
            async def request_approval(self, tool_name, arguments, risk_level, context):
                return ApprovalDecision(approved=True)

        worker = ToolWorker(port=port, access_policy=ToolAccessPolicy(), approval_callback=AutoApprove())
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "test", "arguments": {}}}]}
                ],
                "risk_level": "high",
            },
        )
        port.tool_execute.assert_called_once()

    async def test_approval_denied(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ApprovalCallback, ApprovalDecision, ToolAccessPolicy

        class Deny(ApprovalCallback):
            async def request_approval(self, tool_name, arguments, risk_level, context):
                return ApprovalDecision(approved=False, reason="No")

        worker = ToolWorker(port=port, access_policy=ToolAccessPolicy(), approval_callback=Deny())
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "test", "arguments": {}}}]}
                ],
                "risk_level": "high",
            },
        )
        port.tool_execute.assert_not_called()
        assert result.channel_updates["messages"][0]["is_error"] is True

    async def test_fail_closed_no_callback(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ToolAccessPolicy

        worker = ToolWorker(port=port, access_policy=ToolAccessPolicy())
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "test", "arguments": {}}}]}
                ],
                "risk_level": "high",
            },
        )
        port.tool_execute.assert_not_called()
        assert result.channel_updates["messages"][0]["is_error"] is True

    async def test_critical_sandbox_still_approval(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ApprovalCallback, ApprovalDecision, ToolAccessPolicy

        class AutoApprove(ApprovalCallback):
            async def request_approval(self, tool_name, arguments, risk_level, context):
                return ApprovalDecision(approved=True)

        worker = ToolWorker(port=port, access_policy=ToolAccessPolicy(), approval_callback=AutoApprove())
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {"role": "assistant", "tool_calls": [{"id": "tc1", "function": {"name": "test", "arguments": {}}}]}
                ],
                "risk_level": "critical",
                "sandbox_enabled": True,
            },
        )
        port.tool_execute_sandbox.assert_called_once()

    async def test_dangerous_pattern_blocks_tool(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ToolAccessPolicy

        worker = ToolWorker(port=port, access_policy=ToolAccessPolicy())
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "function": {
                                    "name": "bash",
                                    "arguments": {"command": "rm -rf /"},
                                },
                            }
                        ],
                    }
                ],
                "risk_level": "low",
            },
        )
        port.tool_execute.assert_not_called()
        assert result.channel_updates["messages"][0]["is_error"] is True

    async def test_arg_conditions_ask_triggers_approval(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import (
            ApprovalCallback,
            ApprovalDecision,
            RuleAction,
            ToolAccessPolicy,
            ToolRule,
        )

        class AutoApprove(ApprovalCallback):
            async def request_approval(self, tool_name, arguments, risk_level, context):
                return ApprovalDecision(approved=True)

        policy = ToolAccessPolicy()
        worker = ToolWorker(
            port=port,
            access_policy=policy,
            approval_callback=AutoApprove(),
        )
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "function": {
                                    "name": "write_file",
                                    "arguments": {"path": ".env", "content": "SECRET=123"},
                                },
                            }
                        ],
                    }
                ],
                "risk_level": "low",
                "tool_rules": [
                    ToolRule(
                        RuleAction.ASK,
                        "write_file",
                        arg_conditions={"path": "*.env"},
                    )
                ],
            },
        )
        port.tool_execute.assert_called_once()

    async def test_workspace_boundary_auto_allows_inside(self) -> None:
        port = _make_port()
        from hecate.runtime.tool_access import ToolAccessPolicy

        policy = ToolAccessPolicy()
        worker = ToolWorker(port=port, access_policy=policy)
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "tc1",
                                "function": {
                                    "name": "write_file",
                                    "arguments": {"path": "src/app.py", "content": "x = 1"},
                                },
                            }
                        ],
                    }
                ],
                "risk_level": "high",
                "workspace_root": "/workspace",
            },
        )
        port.tool_execute.assert_called_once()


class TestParallelToolExecution:
    """1.3.2 — multiple tool calls in one assistant turn execute concurrently."""

    async def test_multiple_calls_dispatched_concurrently(self) -> None:
        """N parallel calls complete in wall-clock time of one slow call."""
        import asyncio
        import time

        delays = [0.05, 0.05, 0.05]
        in_flight = 0
        peak_in_flight = 0
        lock = asyncio.Lock()

        async def _slow(name, args, context):
            nonlocal in_flight, peak_in_flight
            async with lock:
                in_flight += 1
                peak_in_flight = max(peak_in_flight, in_flight)
            try:
                await asyncio.sleep(delays[hash(name) % len(delays)])
                return f"result-{name}"
            finally:
                async with lock:
                    in_flight -= 1

        port = MagicMock()
        port.tool_execute = AsyncMock(side_effect=_slow)
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        worker = ToolWorker(port=port)

        start = time.monotonic()
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "tc_a", "function": {"name": "a", "arguments": {}}},
                            {"id": "tc_b", "function": {"name": "b", "arguments": {}}},
                            {"id": "tc_c", "function": {"name": "c", "arguments": {}}},
                        ],
                    }
                ]
            },
        )
        elapsed = time.monotonic() - start

        # If sequential, total = 3 * 0.05 = ~0.15s. Parallel target: ~0.05s.
        # Allow generous headroom for CI jitter but still demonstrate speedup.
        assert elapsed < 0.12, f"Tool calls ran sequentially (elapsed={elapsed:.3f}s)"
        assert port.tool_execute.call_count == 3
        # The three calls should overlap in time — peak concurrency is 3
        # (all three awaiting asyncio.sleep simultaneously).
        assert peak_in_flight >= 2, f"Calls did not overlap (peak={peak_in_flight})"
        # Result ordering matches input ordering regardless of completion order.
        results = result.channel_updates["messages"]
        assert [r["tool_call_id"] for r in results] == ["tc_a", "tc_b", "tc_c"]

    async def test_parallel_calls_preserve_result_ordering(self) -> None:
        """``asyncio.gather`` returns results in submission order even when
        individual tasks complete out-of-order."""
        port = MagicMock()
        port.tool_execute = AsyncMock(side_effect=_sleeper_then)
        port.create_span = AsyncMock(return_value=None)
        port.end_span = AsyncMock(return_value=None)
        worker = ToolWorker(port=port)

        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            # submitted order: fast, slow, medium — completion
                            # order will differ; output order must still match input.
                            {"id": "tc_fast", "function": {"name": "fast", "arguments": {}}},
                            {"id": "tc_slow", "function": {"name": "slow", "arguments": {}}},
                            {"id": "tc_med", "function": {"name": "med", "arguments": {}}},
                        ],
                    }
                ]
            },
        )
        ids = [m["tool_call_id"] for m in result.channel_updates["messages"]]
        contents = [m["content"] for m in result.channel_updates["messages"]]
        assert ids == ["tc_fast", "tc_slow", "tc_med"]
        assert contents == ["ok-fast", "ok-slow", "ok-med"]


async def _sleeper_then(name: str, args: dict | None = None, context: dict | None = None) -> str:
    """Return after a name-dependent delay so out-of-order completion is reliable."""
    import asyncio

    delays = {"fast": 0.001, "med": 0.02, "slow": 0.04}
    await asyncio.sleep(delays.get(name, 0))
    return f"ok-{name}"


class TestEvidenceCapture:
    """ToolWorker captures tool outcomes into the run's EvidenceTracker (4.8)."""

    async def test_successful_tool_call_captured(self) -> None:
        port = _make_port("search results")
        worker = ToolWorker(port=port)
        tracker = EvidenceTracker(session_id="s1")
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {"q": "x"}}}],
                    }
                ]
            },
            execution_context={"evidence_tracker": tracker, "session_id": "s1", "superstep": 1},
        )
        assert len(tracker) == 1
        record = tracker.records[0]
        assert record.tool_name == "search"
        assert record.is_error is False
        assert record.provenance == {"node_id": "tool", "superstep": 1}

    async def test_failed_tool_call_captured_as_error(self) -> None:
        port = _make_port()
        port.tool_execute = AsyncMock(side_effect=RuntimeError("boom"))
        worker = ToolWorker(port=port)
        tracker = EvidenceTracker()
        await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {}}}],
                    }
                ]
            },
            execution_context={"evidence_tracker": tracker, "session_id": "s1", "superstep": 0},
        )
        assert len(tracker) == 1
        assert tracker.records[0].is_error is True

    async def test_repeated_call_boosts_prior_evidence(self) -> None:
        port = _make_port("same result")
        worker = ToolWorker(port=port)
        tracker = EvidenceTracker()
        tool_calls = [{"id": "tc_1", "function": {"name": "search", "arguments": {"q": "x"}}}]
        messages = [{"role": "assistant", "content": "", "tool_calls": tool_calls}]
        ctx = {"evidence_tracker": tracker, "session_id": "s1", "superstep": 0}
        for turn in range(2):
            tool_calls[0]["id"] = f"tc_{turn}"
            await worker.execute(
                node_id="tool", node_config={}, channel_snapshot={"messages": messages}, execution_context=ctx
            )
        assert len(tracker) == 2
        assert tracker.records[0].references == 2
        assert tracker.records[0].importance == 0.7

    async def test_no_tracker_is_noop(self) -> None:
        port = _make_port("ok")
        worker = ToolWorker(port=port)
        result = await worker.execute(
            node_id="tool",
            node_config={},
            channel_snapshot={
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": "tc_1", "function": {"name": "search", "arguments": {}}}],
                    }
                ]
            },
            execution_context=None,
        )
        assert result.error is None
