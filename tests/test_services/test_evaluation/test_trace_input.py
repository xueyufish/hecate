"""Tests for the session-event-log → EvalInput projection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from hecate.ops.evaluation.tasks.trace_input import build_eval_input
from hecate.runtime.eventstore import Event, EventType, InMemoryEventStore

_SESSION = uuid.UUID("00000000-0000-0000-0000-000000000001")
_BASE = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


def _msg_event(role: str, content: str, at: datetime, via_channel: bool = False) -> Event:
    if via_channel:
        payload = {"channel": "messages", "value": {"role": role, "content": content}}
        return Event(
            session_id=_SESSION, superstep=1, event_type=EventType.CHANNEL_WRITE, timestamp=at, payload=payload
        )
    return Event(
        session_id=_SESSION,
        superstep=1,
        event_type=EventType.LLM_REQUEST,
        timestamp=at,
        payload={"messages": [{"role": role, "content": content}]},
    )


def _tool_event(name: str, at: datetime) -> Event:
    return Event(
        session_id=_SESSION,
        superstep=1,
        event_type=EventType.TOOL_CALL,
        timestamp=at,
        payload={"tool_name": name, "arguments": {"q": name}, "tool_call_id": "t1"},
    )


async def _store_with(*events: Event) -> InMemoryEventStore:
    store = InMemoryEventStore()
    for event in events:
        await store.append(event)
    return store


class TestBuildEvalInput:
    async def test_projects_last_exchange_with_history(self) -> None:
        store = await _store_with(
            _msg_event("user", "first question", _BASE + timedelta(seconds=1)),
            _msg_event("assistant", "first answer", _BASE + timedelta(seconds=2)),
            _msg_event("user", "what is X?", _BASE + timedelta(seconds=3)),
            _msg_event("assistant", "X is a thing", _BASE + timedelta(seconds=4)),
            _tool_event("search", _BASE + timedelta(seconds=3, microseconds=5)),
        )

        eval_input = await build_eval_input(_SESSION, _BASE, _BASE + timedelta(minutes=1), store)

        assert eval_input is not None
        assert eval_input.query == "what is X?"
        assert eval_input.generated_answer == "X is a thing"
        # History = all window messages before the final user query,
        # including the previous assistant turn (multi-turn context).
        assert eval_input.conversation_history == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]
        assert eval_input.tool_calls == [{"name": "search", "args": {"q": "search"}, "tool_call_id": "t1"}]

    async def test_window_excludes_earlier_and_later_messages(self) -> None:
        store = await _store_with(
            _msg_event("user", "before window", _BASE - timedelta(seconds=10)),
            _msg_event("user", "in window", _BASE + timedelta(seconds=1)),
            _msg_event("assistant", "also in window", _BASE + timedelta(seconds=2)),
            _msg_event("assistant", "after window", _BASE + timedelta(minutes=5)),
        )

        eval_input = await build_eval_input(_SESSION, _BASE, _BASE + timedelta(minutes=1), store)

        assert eval_input is not None
        assert eval_input.query == "in window"
        assert eval_input.generated_answer == "also in window"
        assert eval_input.conversation_history is None

    async def test_no_complete_exchange_returns_none(self) -> None:
        store = await _store_with(
            _msg_event("user", "only a question", _BASE + timedelta(seconds=1)),
        )
        eval_input = await build_eval_input(_SESSION, _BASE, _BASE + timedelta(minutes=1), store)
        assert eval_input is None

    async def test_channel_write_messages_included(self) -> None:
        store = await _store_with(
            _msg_event("user", "via channel", _BASE + timedelta(seconds=1), via_channel=True),
            _msg_event("assistant", "channel reply", _BASE + timedelta(seconds=2), via_channel=True),
        )
        eval_input = await build_eval_input(_SESSION, _BASE, _BASE + timedelta(minutes=1), store)
        assert eval_input is not None
        assert eval_input.query == "via channel"
        assert eval_input.generated_answer == "channel reply"

    async def test_open_window_scores_everything_so_far(self) -> None:
        store = await _store_with(
            _msg_event("user", "q", _BASE + timedelta(seconds=1)),
            _msg_event("assistant", "a", _BASE + timedelta(hours=5)),
        )
        eval_input = await build_eval_input(_SESSION, _BASE, None, store)
        assert eval_input is not None
        assert eval_input.generated_answer == "a"

    async def test_oversize_window_rejected(self) -> None:
        events = [
            Event(
                session_id=_SESSION,
                superstep=1,
                event_type=EventType.LLM_REQUEST,
                timestamp=_BASE + timedelta(seconds=1),
                payload={"messages": [{"role": "user", "content": f"q{i}"}]},
            )
            for i in range(6)
        ]
        store = await _store_with(*events)
        eval_input = await build_eval_input(_SESSION, _BASE, None, store, max_messages=5)
        assert eval_input is None
