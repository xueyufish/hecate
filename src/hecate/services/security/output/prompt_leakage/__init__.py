"""System prompt leakage protection (9.2).

Winnowing n-gram fingerprint matching of LLM output against the system
prompt baseline to detect OWASP LLM07:2025 categories: exposure of
sensitive functionality, internal rules, filtering criteria, permissions,
or embedded secrets.
"""

from __future__ import annotations
