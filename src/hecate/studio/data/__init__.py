"""Studio data — orchestration and agent template fixtures.

JSON files only; loaded at runtime by the matching API router
(``api/management/agent_templates.py`` reads from
``studio/data/agent_templates/``). Orchestration templates
(``orchestration_templates/``) are the preset fan-out / reflection-
loop / conditional-pipeline / debate / hierarchical-supervisor /
negotiation / etc. fixtures that show up in the management UI's
orchestration template picker.

Moved from ``src/hecate/data/`` during Phase R-complete (commit
``refactor(domain): move services/workflow + multi_agent + meta_agents
+ data to studio/``).
"""

from __future__ import annotations
