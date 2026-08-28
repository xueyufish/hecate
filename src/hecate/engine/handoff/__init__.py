"""Engine-side handoff primitives.

Public API for engine-layer handoff message construction. The functions here
are pure data transforms (no I/O, no port calls, no DB) and have no
dependency on services/orchestration.

Other handoff concerns (tool schema generation, target validation, the
``handoff_to_agent`` detection helper) remain in
``hecate.services.orchestration.handoff`` because ``AgentExecutionPort`` (a
concrete RuntimePort adapter) consumes them — splitting them out would
invert the layering.
"""

from __future__ import annotations

from hecate.engine.handoff.channel_updates import (
    build_handoff_channel_updates,
    filter_messages_for_handoff,
)

__all__ = [
    "build_handoff_channel_updates",
    "filter_messages_for_handoff",
]
