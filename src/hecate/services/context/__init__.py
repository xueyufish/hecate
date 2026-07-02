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
"""

from __future__ import annotations
