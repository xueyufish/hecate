"""Tools domain — registration catalog half.

Companion to ``packages/hecate-sandbox`` (the execution environment
half). Tools are *defined* in this domain; their *execution* path lives
in the sandbox package and is wired in at runtime via RuntimePort.

Sub-packages
------------

- ``tool/`` — tool registry, builtin tool definitions, on-disk cache,
  search backend adapters (duckduckgo / serper / tavily), shell-command
  analysis and hook. Moved from ``src/hecate/services/tool/`` as part
  of Phase R-complete (tools/ domain filling).
- ``policy/`` — composable tool policy pipeline (5 ordered layers:
  PluginAvailability → Profile → Visibility → Security → Mode).
  Founded earlier in the same change as ``phase-r-domain-reorg-followups``.
- ``skill/`` — skill loader and parser. Moved from
  ``src/hecate/services/skill/``.
- ``skill_registry/`` — runtime skill registry + data types. Moved
  from the top-level ``src/hecate/skill_registry/`` directory.

History
-------

Phase R-MVP established ``runtime/`` as the future ``hecate-runtime``
wheel source; this domain is the corollary for the ``tools`` side
of the platform. Phase R follow-ups founded ``tools/policy/``; this
PR fills the rest of the tools/ sub-trees in one move.

Tool definitions live in core; tool *execution* (Docker sandbox,
browser, environment) lives in ``packages/hecate-sandbox``. The split
is the same one for skill definitions vs skill execution.
"""

from __future__ import annotations
