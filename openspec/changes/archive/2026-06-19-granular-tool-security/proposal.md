## Why

Feature 9.4 (Execution Security) established tool-name-level access control via `ToolAccessPolicy` with glob pattern matching on tool names (e.g., `terminal(git:*)`). However, real-world security requires **argument-level inspection** — distinguishing `write_file({"path": ".env"})` (sensitive) from `write_file({"path": "output.txt"})` (safe). Current rules cannot express this difference. Additionally, workspace boundary enforcement (9.4b) is needed so file operations within the workspace directory are auto-allowed while operations outside require explicit approval. A 10-platform survey (Claude Code, HermesAgent, AgentScope, AgentArts, openJiuwen, Salesforce, Google ADK, IBM watsonx, OpenClaw, Dify) confirms glob-based argument matching as the industry-standard approach.

## What Changes

- **Extend `ToolRule`** with optional `arg_conditions: dict[str, str] | None` for glob-based argument value matching (Claude Code `Bash(rm *)` pattern)
- **Extend `ToolAccessPolicy.evaluate()`** to accept tool call arguments and match `arg_conditions` after tool-name match
- **Add built-in dangerous pattern list** — predefined `DENY` patterns for common dangerous operations (`rm -rf /`, `DROP TABLE`, `curl | sh`, `os.system`, `~/.ssh`, `~/.env`, `/etc/passwd`) that cannot be overridden by `ALLOW` rules
- **Add workspace boundary evaluation** — auto-allow file operations within `workspace_root`, require approval for operations outside
- **Extend `ToolPolicyModel`** with `arg_conditions` JSON column for persisted argument-level rules
- **Extend `ToolWorker._check_access()`** to pass parsed arguments to `ToolAccessPolicy.evaluate()`
- **Add `WorkspaceBoundaryPolicy`** helper class that checks `path`-type arguments against `workspace_root` from context

## Capabilities

### New Capabilities
- `granular-tool-security`: Argument-level tool security with glob pattern matching on tool call arguments, built-in dangerous pattern detection, and workspace boundary enforcement

### Modified Capabilities
- `execution-security`: ToolRule dataclass gains `arg_conditions` field; ToolAccessPolicy.evaluate() signature extended to accept arguments; ToolWorker._check_access() passes arguments to policy evaluator

## Impact

- **Engine layer** (`engine/tool_access.py`): Extend `ToolRule`, `ToolAccessPolicy`, add `DANGEROUS_PATTERNS` constant and `WorkspaceBoundaryPolicy` class
- **Engine layer** (`engine/workers/tool_worker.py`): Pass arguments to `_check_access()`, extend `_check_access` to forward arguments to policy
- **Models layer** (`models/tool_policy.py`): Add `arg_conditions` JSON column to `ToolPolicyModel`
- **Migration**: New Alembic migration to add `arg_conditions` column to `tool_policies` table
- **Tests**: New test suite for argument matching, dangerous pattern detection, workspace boundary checking
- **Backward compatibility**: All changes are additive — existing name-only rules continue to work unchanged
