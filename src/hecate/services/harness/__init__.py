"""Harness optimization services for Hecate Agent platform.

This module provides self-improvement capabilities:

- **FailureAnalyzer** — LLM-driven failure classification and root cause analysis
- **ConstraintGenerator** — Generate constraint rules from failure analysis
- **ConstraintInjector** — Inject constraints into system prompts
"""

from __future__ import annotations
