# Engine & model gotchas

Non-obvious behaviors that are not apparent from reading the code in a few
minutes. Environment/git gotchas live in the root `AGENTS.md`.

## ORM / Pydantic aliases

- **AgentModel.model_config_db** — ORM column `model_config` via
  `mapped_column("model_config", JSON)` (avoids Pydantic `model_config`
  collision). CreateSchema `alias="model_config"`, ReadSchema
  `serialization_alias="model_config"`.
- **metadata_ alias** — 11 models across 10 modules map `metadata_` (Python)
  → `metadata` (SQL); ReadSchema uses `Field(validation_alias="metadata_")`.

## Runtime engine semantics

- **compiler._detect_unreachable()** uses BFS from entry; logs WARNING for
  unreachable nodes (does not raise).
- **ChannelManager**: `write()` silently skips unregistered channels;
  `read()` raises KeyError (`ChannelNotFoundError`); `restore()` bypasses
  write semantics and sets `_value` directly.
- **StreamMode** has only VALUES, UPDATES, MESSAGES — DEBUG no longer exists.
  MESSAGES is the SSE streaming mode.
- **PERSISTENT_TOPIC** channel type is deprecated — auto-migrated to `topic`
  + `persistent: true` by `studio/workflows/graph_dsl.py` (formerly
  `services/workflow/graph_dsl.py`).
- **Guardrail wiring** — hooks and middleware chains are assembled by
  `src/hecate/runtime/security/guardrail_assembly.py` (formerly
  `services/security/`), on both the Pregel path and the
  `channel/api/v1/chat.py` direct tool loop.

## Type checking

- **mypy** runs `strict=true` but many error codes are disabled in
  `pyproject.toml` — not truly strict. Do not assume a passing mypy run means
  full strict coverage.
