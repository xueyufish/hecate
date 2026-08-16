"""Read-side execution replay services (8.20).

Aggregates the event log into a trace-partitioned timeline with body content,
guardrail derivation, and timing/usage enrichment from OTel TraceModel.
"""

from __future__ import annotations
