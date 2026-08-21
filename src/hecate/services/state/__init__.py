"""Agent State — per-session working state separated from durable Environment.

Post-13.4a-7: persistence moved to SessionStateStore (Redis /
PostgreSQL / Tiered). Only the in-memory model class remains for
thread-local scratch state.
"""

from __future__ import annotations

from hecate.services.state.state import AgentState

__all__ = ["AgentState"]
