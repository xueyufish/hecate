"""Composition root — the single place that knows how to wire the application.

This package centralises what used to be scattered through ``main.py``'s
lifespan handler plus three resolver modules in ``services/orchestration``.
Plan §1.1 calls this the composition root (DI standard terminology —
the unique place that knows how to construct and assemble all
top-level objects for the running application).

Sub-modules
-----------

- ``wiring`` — the ``compose_application(app)`` function. Every piece
  of state that used to live inlined in ``main.py``'s lifespan
  (seed_builtin_tools, secret provider registration, event store,
  session state store, DLP scanner, plugin discovery, meta-agent
  scheduler, IM channel registration, budget forecast scheduler,
  tracing setup, monitoring + audit batch writer, tool decision
  pipeline, security findings, SIEM export, sandbox pool) is now
  one helper per block.
- ``llm_gateway`` — entry-point-driven LLM gateway resolver
  (relocated from services/orchestration/, PR #114 + #119 history).
- ``memory_provider`` — entry-point-driven memory backend resolver
  (relocated from services/orchestration/, PR #107 history).
- ``runtime_port_adapter`` — factory for the production RuntimePort
  implementation (relocated from services/orchestration/, PR0.1
  history).

Out of scope (recorded for follow-up work)
-------------------------------------------

- ``services/orchestration/agent_tool.py`` and ``handoff.py`` stay
  there pending PR-E.2 (orchestration decomposition into runtime/
  agents / composition halves).
- ``services/orchestration/agent_execution_port.py`` and
  ``session_state_materializer.py`` stay there pending PR-E.2
  (they are runtime-adjacent adapters, not composition glue).

History
-------

PR-E.1 (this commit) establishes the composition directory as the
single assembly point. Future plugin / workspace additions plug in
here rather than touching ``main.py``. The shipped scope stays
modest — composition has exactly four files; growing it further is
the natural next PR.
"""

from __future__ import annotations
