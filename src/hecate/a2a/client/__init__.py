"""A2A Client module for discovering and communicating with remote agents."""

from hecate.a2a.client.client import A2AClient
from hecate.a2a.client.discovery import discover_agent_card

__all__ = [
    "A2AClient",
    "discover_agent_card",
]
