"""Ops domain — observability, alerts, cost / quota / retention / scheduling.

Where ``runtime/`` is the engine and ``studio/`` is the authoring
half, ``ops/`` owns the platform's management-plane concerns: the
schedulers, audit / retention / SIEM pipelines, alerts, cost / quota
accounting, and evaluation harnesses.

Sub-modules
-----------

- ``alerts/`` — alert rules, evaluation, signal providers. Replaces
  the historical ``services/alert_{service,evaluator}.py`` and
  ``services/signal_provider.py``.
- ``audit/`` — audit log persistence and query.
- ``backup/`` — PostgreSQL / MinIO / S3 / Qdrant backup engine.
- ``cost.py`` — model-pricing cost tracking.
- ``evaluation/`` — Ragas-backed RAG / agent evaluation.
- ``notification.py`` — notification dispatcher with IM templates.
- ``ops_center/`` — agent health / fleet monitoring.
- ``quota.py`` — quota definitions + usage tracking.
- ``retention.py`` — event-log retention policy.
- ``scheduling/`` — scheduled task execution.

Out of scope (deliberately — recorded for follow-up triage)
---------------------------------------------------------

- ``core/feature_flags/`` — moved there in this same PR (platform-
  level concern that fits ``core/`` better than ``ops/``).
- ``services/security/`` (9 files + subdirs) — DLP / guardrail /
  SIEM / hooks / approval span both runtime-adjacent concerns and
  ops governance; plan §1.1 does not name a security domain. The
  follow-up PR-D triage decides whether security becomes its own
  in-main-package domain or splits between runtime/ (guardrail
  hooks) + ops/ (DLP / SIEM / findings).
- ``services/orchestration/`` (7 files) — cross-domain hub. Its
  decomposition depends on ``core/composition/`` landing first.

History
-------

Phase R-complete moved this directory from ``src/hecate/services/``
(13 source trees: alerts, audit, backup, cost, evaluation, feature
flags, notification, ops_center, quota, retention, scheduling,
signal_provider). ``feature_flags`` landed in ``core/`` per
plan §1.1's framing of feature flags as a cross-cutting concern.
"""

from __future__ import annotations
