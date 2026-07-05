"""A2A Server components for Hecate as an A2A agent."""

from hecate.a2a.server.card import generate_agent_card
from hecate.a2a.server.executor import HecateAgentExecutor

__all__ = [
    "HecateAgentExecutor",
    "generate_agent_card",
]
