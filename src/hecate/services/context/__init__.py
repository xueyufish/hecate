"""Context Engineering services for Hecate Agent platform.

This module provides context management capabilities:

- **ContextAssembler** — Dynamically assembles context before LLM invocations
  based on task phase, message priority, and available capabilities.
- **BudgetManager** — Tracks token budgets per session and applies structured
  degradation strategies when context exceeds limits.
- **EvidenceTracker** — Captures and normalizes tool execution results with
  provenance tracking and importance scoring.
- **Provider Shaping** — Adapts assembled context to target LLM provider
  requirements (OpenAI, Anthropic, etc.).
- **ContextOffloader** — Writes overflow conversation messages to the
  AgentEnvironment filesystem so the agent can retrieve them via ``read_file``
  instead of losing them to context compression.
"""

from __future__ import annotations

from hecate.services.context.offloader import ContextOffloader

__all__ = ["ContextOffloader"]
