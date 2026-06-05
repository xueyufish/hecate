from __future__ import annotations

from hecate.engine.types import StreamMode, WorkerResult
from hecate.engine.worker import Worker


class TokenWorker(Worker):
    async def execute(self, node_id, node_config, channel_snapshot):
        return WorkerResult(node_id=node_id, channel_updates={"messages": [{"role": "assistant", "content": "final"}]})

    async def execute_stream(self, node_id, node_config, channel_snapshot):
        for token in ["Hello", " ", "world"]:
            yield {"content": token}
        yield WorkerResult(
            node_id=node_id,
            channel_updates={"messages": [{"role": "assistant", "content": "Hello world"}]},
        )


class SimpleWorker(Worker):
    async def execute(self, node_id, node_config, channel_snapshot):
        return WorkerResult(node_id=node_id, channel_updates={"messages": [{"role": "assistant", "content": "simple"}]})


class TestStreamModeMessages:
    async def test_token_worker_yields_tokens(self) -> None:
        worker = TokenWorker()
        events = []
        async for event in worker.execute_stream("n", {}, {}):
            events.append(event)
        token_events = [e for e in events if isinstance(e, dict) and "content" in e]
        final_events = [e for e in events if isinstance(e, WorkerResult)]
        assert len(token_events) == 3
        assert token_events[0]["content"] == "Hello"
        assert token_events[1]["content"] == " "
        assert token_events[2]["content"] == "world"
        assert len(final_events) == 1
        assert final_events[0].channel_updates["messages"][0]["content"] == "Hello world"

    async def test_simple_worker_delegates_to_execute(self) -> None:
        worker = SimpleWorker()
        events = []
        async for event in worker.execute_stream("n", {}, {}):
            events.append(event)
        assert len(events) == 1
        assert isinstance(events[0], WorkerResult)
        assert events[0].channel_updates["messages"][0]["content"] == "simple"

    async def test_stream_mode_messages_enum(self) -> None:
        assert StreamMode.MESSAGES.value == "messages"
        assert StreamMode.VALUES.value == "values"
        assert StreamMode.UPDATES.value == "updates"

    async def test_multiple_workers_streaming(self) -> None:
        class ConcatWorker(Worker):
            def __init__(self, tokens):
                self.tokens = tokens

            async def execute(self, node_id, node_config, channel_snapshot):
                return WorkerResult(node_id=node_id, channel_updates={"messages": []})

            async def execute_stream(self, node_id, node_config, channel_snapshot):
                for t in self.tokens:
                    yield {"content": t}
                yield WorkerResult(node_id=node_id, channel_updates={"messages": []})

        w1 = ConcatWorker(["A", "B"])
        w2 = ConcatWorker(["C", "D"])

        all_tokens = []
        for w in [w1, w2]:
            async for event in w.execute_stream("n", {}, {}):
                if isinstance(event, dict) and "content" in event:
                    all_tokens.append(event["content"])

        assert all_tokens == ["A", "B", "C", "D"]
