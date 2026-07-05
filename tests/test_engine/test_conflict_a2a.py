"""Tests for A2A-related conflict scenarios and EventBus extensions."""

from __future__ import annotations

from hecate.engine.eventbus import CollaborationEventType


def test_a2a_event_types_exist() -> None:
    """Test that A2A event types are defined in CollaborationEventType."""
    assert hasattr(CollaborationEventType, "A2A_TASK_DELEGATED")
    assert hasattr(CollaborationEventType, "A2A_TASK_RECEIVED")
    assert hasattr(CollaborationEventType, "A2A_ARTIFACT_SENT")
    assert hasattr(CollaborationEventType, "A2A_ARTIFACT_RECEIVED")
    assert hasattr(CollaborationEventType, "A2A_AGENT_DISCOVERED")


def test_a2a_event_type_values() -> None:
    """Test that A2A event type string values are correct."""
    assert CollaborationEventType.A2A_TASK_DELEGATED == "A2A_TASK_DELEGATED"
    assert CollaborationEventType.A2A_TASK_RECEIVED == "A2A_TASK_RECEIVED"
    assert CollaborationEventType.A2A_ARTIFACT_SENT == "A2A_ARTIFACT_SENT"
    assert CollaborationEventType.A2A_ARTIFACT_RECEIVED == "A2A_ARTIFACT_RECEIVED"
    assert CollaborationEventType.A2A_AGENT_DISCOVERED == "A2A_AGENT_DISCOVERED"


def test_original_event_types_preserved() -> None:
    """Test that original event types are still present."""
    assert CollaborationEventType.AGENT_MESSAGE == "AGENT_MESSAGE"
    assert CollaborationEventType.TASK_ASSIGNED == "TASK_ASSIGNED"
    assert CollaborationEventType.NEGOTIATION_PROPOSAL == "NEGOTIATION_PROPOSAL"
