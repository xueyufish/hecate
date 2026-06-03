"""Tests for the SchedulerStrategy abstract interface and FIFOScheduler.

Validates the pluggable scheduling contract:

- SchedulerStrategy ABC cannot be instantiated directly.
- FIFOScheduler returns nodes in input order.
- FIFOScheduler.set_weights is a no-op.
- FIFOScheduler handles empty node lists.
"""

from __future__ import annotations

import pytest

from hecate.engine.scheduler import FIFOScheduler, SchedulerStrategy

# --- SchedulerStrategy ABC ---


def test_scheduler_strategy_is_abstract():
    """SchedulerStrategy SHALL NOT be instantiable directly."""
    with pytest.raises(TypeError):
        SchedulerStrategy()  # type: ignore[abstract]


# --- FIFOScheduler ---


@pytest.fixture
def scheduler() -> FIFOScheduler:
    return FIFOScheduler()


def test_fifo_select_next_returns_nodes_unchanged(scheduler: FIFOScheduler):
    """FIFOScheduler SHALL return nodes in their original order."""
    nodes = ["node_b", "node_a", "node_c"]
    result = scheduler.select_next(nodes, {})
    assert result == ["node_b", "node_a", "node_c"]


def test_fifo_select_next_returns_copy(scheduler: FIFOScheduler):
    """FIFOScheduler SHALL return a new list, not the original."""
    nodes = ["node_a", "node_b"]
    result = scheduler.select_next(nodes, {})
    assert result is not nodes
    assert result == nodes


def test_fifo_select_next_empty_list(scheduler: FIFOScheduler):
    """FIFOScheduler SHALL return empty list for empty input."""
    result = scheduler.select_next([], {})
    assert result == []


def test_fifo_set_weights_is_noop(scheduler: FIFOScheduler):
    """FIFOScheduler.set_weights SHALL not affect select_next behavior."""
    scheduler.set_weights({"node_a": 10.0, "node_b": 1.0})
    result = scheduler.select_next(["node_b", "node_a"], {})
    assert result == ["node_b", "node_a"]


def test_fifo_ignores_context(scheduler: FIFOScheduler):
    """FIFOScheduler SHALL ignore context and return nodes unchanged."""
    context = {"superstep": 5, "snapshot": {"messages": ["test"]}}
    nodes = ["node_a", "node_b"]
    result = scheduler.select_next(nodes, context)
    assert result == ["node_a", "node_b"]
