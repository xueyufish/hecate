from __future__ import annotations

import pytest

from hecate.engine.workers.condition_worker import ConditionWorker


@pytest.fixture
def worker() -> ConditionWorker:
    return ConditionWorker()


class TestConditionWorker:
    async def test_has_tool_call_true(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "has_tool_call"},
            channel_snapshot={"_has_tool_call": True},
        )
        assert result.channel_updates["_route"] == "true"

    async def test_has_tool_call_false(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "has_tool_call"},
            channel_snapshot={},
        )
        assert result.channel_updates["_route"] == "false"

    async def test_has_tool_call_none(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "has_tool_call"},
            channel_snapshot={"_has_tool_call": None},
        )
        assert result.channel_updates["_route"] == "false"

    async def test_equality_match(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "category == 'support'"},
            channel_snapshot={"category": "support"},
        )
        assert result.channel_updates["_route"] == "true"

    async def test_equality_mismatch(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "category == 'support'"},
            channel_snapshot={"category": "sales"},
        )
        assert result.channel_updates["_route"] == "false"

    async def test_equality_with_double_quotes(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": 'category == "support"'},
            channel_snapshot={"category": "support"},
        )
        assert result.channel_updates["_route"] == "true"

    async def test_truthy_key(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "enable_suggestions"},
            channel_snapshot={"enable_suggestions": True},
        )
        assert result.channel_updates["_route"] == "true"

    async def test_falsy_key(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "enable_suggestions"},
            channel_snapshot={"enable_suggestions": False},
        )
        assert result.channel_updates["_route"] == "false"

    async def test_missing_key(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "nonexistent"},
            channel_snapshot={},
        )
        assert result.channel_updates["_route"] == "false"

    async def test_empty_expression(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": ""},
            channel_snapshot={},
        )
        assert result.channel_updates["_route"] == "false"

    async def test_messages_channel_empty(self, worker: ConditionWorker) -> None:
        result = await worker.execute(
            node_id="cond",
            node_config={"expression": "has_tool_call"},
            channel_snapshot={},
        )
        assert result.channel_updates["messages"] == []
