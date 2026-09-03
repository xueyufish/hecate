"""Channel-domain admin HTTP endpoints.

Moved from ``src/hecate/api/management/`` during Phase R-complete. The
files in this directory are domain-scoped admin endpoints: ``alerts``
for IM-channel alert rules. Future Phase R-complete work unpacks the
rest of ``api/management/`` and routes each file to its true domain
(``studio/`` for agents/workflows/conversations/prompts, ``ops/`` for
budgets/cost/quota/traces/dlp/feature_flags, ``tools/`` for the tool
cache + tool analytics + tool policies routes, and ``channel/`` for
A2A / MCP / plugins routes once those are triaged).

Until that triage lands, the un-triaged ``api/management/`` files
stay where they are. They are owned by ``api/`` today and routed
by ``main.py`` with the ``/api/management/`` prefix.
"""

from __future__ import annotations
