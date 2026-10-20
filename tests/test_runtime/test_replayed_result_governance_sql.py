"""SQL-ledger replay governance across executor instances (step7).

The durable ledger mirrors the pre-governance result, so a receipt whose
first run was blocked or sanitized by a PostTool hook must re-run that
governance when a fresh executor recovers it from SQL — the raw payload
stored across the restart is exposed only when the current policy allows
it, and the business tool still dispatched exactly once.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from hecate_runtime.ports import StubRuntimePort
from hecate_runtime.tool_call_executor import ToolCallExecutor


class StubPort(StubRuntimePort):
    """Count business dispatches and return a sensitive payload once."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def tool_execute(self, name, args, context=None):
        self.calls.append((name, args, dict(context or {})))
        return {"ok": True, "secret": "CLASSIFIED-PAYLOAD"}


class ScriptedHook:
    """Legacy PostToolHook stand-in with a scripted outcome."""

    matcher = None  # match every tool, like the base-class default

    def __init__(self, action: str, masked: str = "masked-on-replay") -> None:
        self.action = action
        self.masked = masked
        self.calls = 0

    async def on_post_tool_call(self, name, result, context):
        self.calls += 1
        if self.action == "block":
            from hecate_runtime.guardrail import GuardrailAction, GuardrailResult

            return GuardrailResult(action=GuardrailAction.BLOCK, reason="sql policy")
        if self.action == "sanitize":
            from hecate_runtime.guardrail import GuardrailAction, GuardrailResult

            return GuardrailResult(action=GuardrailAction.SANITIZE, modified_data={"result": self.masked})
        from hecate_runtime.guardrail import GuardrailAction, GuardrailResult

        return GuardrailResult(action=GuardrailAction.ALLOW)


@pytest.mark.parametrize("replay_action", ["block", "sanitize", "allow"], ids=["block", "sanitize", "allow"])
async def test_sql_ledger_replay_governs_pre_governance_payload(tmp_path, replay_action) -> None:
    """SQL-backed replay applies the current output policy without re-execution."""
    from hecate_durable.runtime_hook import SqlActionLedgerHook
    from hecate_durable.storage import SqlDurableStore

    from hecate.contracts.execution.references import run_ref, task_ref

    url = f"sqlite:///{tmp_path / 'replay-governance.db'}"
    task, run = task_ref("platform", "task"), run_ref("platform", "run")
    session_id, port = uuid.uuid4(), StubPort()
    first_hook = ScriptedHook("block")  # first run: model saw the placeholder only

    for attempt in range(2):
        durable = SqlDurableStore(url)
        try:
            if attempt == 0:
                durable.create_schema()
            hook = first_hook if attempt == 0 else ScriptedHook(replay_action)
            ledger_hook = SqlActionLedgerHook(durable, task_ref=task, run_ref=run)
            executor = ToolCallExecutor(port=port, action_hook=ledger_hook, post_tool_hook=hook)
            result = await executor.execute_call(
                {"id": "call-sql", "name": "write_file", "arguments": {"path": "f"}},
                {},
                {"session_id": session_id},
                "standalone",
            )
            if attempt == 0:
                assert result["content"] == "Result sanitized: sql policy"
            elif replay_action in ("block", "sanitize"):
                expected = "Result sanitized: sql policy" if replay_action == "block" else "masked-on-replay"
                assert result["content"] == expected
            else:
                assert "CLASSIFIED-PAYLOAD" in result["content"]
        finally:
            durable.dispose()

    assert len(port.calls) == 1  # the business effect happened exactly once
    durable = SqlDurableStore(url)
    try:
        actions: list[dict[str, Any]] = durable.list_run_actions(run)
        assert len(actions) == 1
        assert actions[0]["result_payload"] == {"ok": True, "secret": "CLASSIFIED-PAYLOAD"}
    finally:
        durable.dispose()
