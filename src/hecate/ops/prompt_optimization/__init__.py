"""Prompt self-optimization pipeline (6.19).

Evaluation-dataset-driven multi-round prompt optimization: a run pins a
prompt version, an agent under test, and a named dataset version; rounds
roll out candidate templates against the real agent via per-invocation
prompt override, score them with the evaluation engine, and gate them
before human review. See ``openspec/changes/prompt-self-optimization/``.
"""
