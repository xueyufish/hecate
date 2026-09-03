"""Unit tests for chat.py EventStore Depends wiring."""

from __future__ import annotations

import inspect

from hecate.channel.api.v1.chat import _process_chat, create_chat_completion


def test_chat_endpoint_has_event_store_depends():
    """``create_chat_completion`` SHALL declare an ``event_store`` Depends parameter."""
    sig = inspect.signature(create_chat_completion)
    assert "event_store" in sig.parameters
    param = sig.parameters["event_store"]
    annotation_repr = repr(param.annotation)
    assert "get_event_store" in annotation_repr


def test_process_chat_accepts_event_store_parameter():
    """``_process_chat`` SHALL accept ``event_store`` as the 5th positional parameter."""
    sig = inspect.signature(_process_chat)
    params = list(sig.parameters.keys())
    assert "event_store" in params
    assert params.index("event_store") == 4
