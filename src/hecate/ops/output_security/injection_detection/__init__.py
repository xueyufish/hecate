"""Injection type detection (9.1a).

Regex-based recognizer registry for detecting injection patterns in LLM
output before it flows to downstream systems (code interpreter, SQL database,
template renderer, HTML page).
"""

from __future__ import annotations
