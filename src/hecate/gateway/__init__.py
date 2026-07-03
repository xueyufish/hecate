"""Gateway — session routing and message normalization layer.

The Gateway sits between channel adapters and the agent runtime
(WorkflowExecutionService). It receives CanonicalMessage from channels,
resolves session context, and delegates to the service layer.
"""

from __future__ import annotations

from hecate.gateway.gateway import Gateway
from hecate.gateway.session import SessionRouter

__all__ = [
    "Gateway",
    "SessionRouter",
]
