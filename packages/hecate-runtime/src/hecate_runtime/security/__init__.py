"""Runtime security companions — zero-dependency safety primitives.

Phase R PR-D.2.a establishes this directory as the home for the
runtime-adjacent security modules that have **no services/ or
ops/ dependencies** — they live next to the engine so the
self-sufficiency guard (tests/test_runtime_self_sufficiency.py) is
preserved.

Three files in this PR-D.2.a scope:

- ``anonymizer`` — PII anonymize / deanonymize (used by hooks for
  streaming PII redaction).
- ``encryption`` — Fernet symmetric encryption for stored
  API keys (the cryptographic primitive for the
  ``mask_and_encrypt`` mode).
- ``llm_guard`` — LLM Guard scanner for input/output safety
  checks (delegates to the optional ``llm-guard`` extra).

Out of scope (recorded for follow-up)
------------------------------------

- ``approval`` (fail-closed approval callback), ``guardrail_assembly``
  (the facade that wires the runtime security pipeline), ``egress``
  (DLP gate), and ``hooks/`` (input_security / output_security /
  stream_deanonymizer / tool_result_security) all stay in
  ``services/security/`` until PR-D.2.b. Reason: they depend on
  ``services.security.dlp.scanner`` (a DLP package member that
  becomes ``ops/dlp/`` in PR-D.2.b), and
  ``services.security.finding_writer`` (becomes ``ops/security/`` in
  PR-D.2.b). Once those targets exist, the remaining files
  relocate in a single follow-up PR.

History
-------

PR-D.2.a (this commit) lands the zero-dependency half. PR-D.2.b
will move the remaining 8 files (plus dlp/, siem/, output/,
finding_*, decision_*) once ops/ is the canonical home for
the DLP / SIEM / security-finding plumbing.
"""

from __future__ import annotations
