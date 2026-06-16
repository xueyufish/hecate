"""Validation services for Hecate Agent platform.

This module provides validation capabilities:

- **ResultValidator** — JSON Schema validation for tool outputs
- **RetryPolicy** — Configurable retry strategies with circuit breaker
- **OutputSchemaValidator** — LLM output validation with auto-repair
"""

from __future__ import annotations
