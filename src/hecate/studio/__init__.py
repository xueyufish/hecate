"""Studio domain — workflow authoring and orchestration.

This is the *authoring* half of the platform: where ``runtime/``
executes compiled graphs, ``studio/`` owns the artifacts that
compile-time / run-time operate on.

Sub-modules
-----------

- ``workflows/`` — graph DSL parser + validator (``graph_dsl.py``),
  orchestration patterns (``patterns.py``: fan-out / reflection-loop /
  conditional-pipeline / debate / ...), execution service
  (``execution_service.py``: compiled graphs → agent runtime), preset
  templates (``templates.py``), test runner (``test_runner.py``),
  CRUD service class (``service.py``: workflow / version
  create-read-update-delete + rollback + publish). The execution
  service is the bridge into runtime/ — it compiles a GraphDefinition
  and hands it to the Pregel runtime for execution.
- ``agents/`` — multi-agent coordination primitives: task allocator,
  agent-to-agent message bus, negotiator.
- ``meta_agents/`` — meta-level agents: compliance checker, drift
  detector, scheduler, garbage collector. These watch the agent fleet
  itself rather than user requests.
- ``data/`` — JSON template fixtures for orchestration patterns and
  agent templates. Loaded by ``api/management/agent_templates.py``
  via ``TEMPLATES_DIR = .../studio/data/agent_templates``.

History
-------

Phase R-complete moved this directory from the original
``src/hecate/services/{workflow,multi_agent,meta_agents,workflow_service.py}``
+ ``src/hecate/data/`` layout. The ``services/`` half is now empty of
studio business logic (orchestration/, a cross-domain hub, stays in
``services/`` pending the composition-root work in Phase R follow-ups
that closes the orchestration/ split).

Companion: ``api/management/agent_templates.py`` and the rest of the
api/management/ routes that surface studio entities — these routes
remain in ``src/hecate/api/management/`` until Phase R follow-up
triage relocates each to its domain.
"""

from __future__ import annotations
