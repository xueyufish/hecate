## 1. Engine Layer — DangerousPattern and DANGEROUS_PATTERNS

- [x] 1.1 Define `DangerousPattern` dataclass in `engine/tool_access.py` with fields: `tool_pattern` (str), `arg_key` (str), `arg_pattern` (str), `description` (str)
- [x] 1.2 Define `DANGEROUS_PATTERNS` module-level constant list with shell command patterns: `rm -rf /`, `mkfs*`, `dd if=*of=/dev/`, `*curl*|*sh`, fork bomb
- [x] 1.3 Add code execution patterns: `*os.system*`, `*subprocess*`, `*eval(*`, `*exec(*` for `execute_code` tool
- [x] 1.4 Add sensitive file patterns: `.ssh`, `.env`, `.bashrc`, `/etc/passwd`, SSH key access for `write_file` and `read_file` tools
- [x] 1.5 Add SQL dangerous patterns: `*DROP TABLE*`, `*DELETE FROM*` for wildcard tool with `code` argument

## 2. Engine Layer — ToolRule arg_conditions Extension

- [x] 2.1 Add `arg_conditions: dict[str, str] | None = None` field to `ToolRule` dataclass
- [x] 2.2 Add `from __future__ import annotations` check — ensure all existing code using ToolRule still works (backward compatible)
- [x] 2.3 Write unit tests: ToolRule construction with and without arg_conditions

## 3. Engine Layer — ToolAccessPolicy arg_conditions Matching

- [x] 3.1 Extend `evaluate()` signature to accept optional `arguments: dict[str, Any] | None = None` parameter
- [x] 3.2 Implement `_match_dangerous_patterns(tool_name, arguments)` method — returns `True` if any dangerous pattern matches
- [x] 3.3 Integrate dangerous pattern check at the START of evaluate() — before user rules, returns DENY if matched
- [x] 3.4 Extend `_match_rules()` to check `arg_conditions` after tool-name match — if rule has arg_conditions, all must match via fnmatch; if no arg_conditions, match on name only (backward compatible)
- [x] 3.5 Write unit tests: dangerous pattern detection (shell, code, file, SQL patterns)
- [x] 3.6 Write unit tests: arg_conditions matching (single condition, multiple conditions, no match, backward compat)
- [x] 3.7 Write unit tests: dangerous pattern overrides user ALLOW rules

## 4. Engine Layer — WorkspaceBoundaryPolicy

- [x] 4.1 Define `WorkspaceBoundaryPolicy` class with `check(tool_name, arguments, workspace_root) -> AccessDecision | None` method
- [x] 4.2 Implement path extraction — check if `arguments` dict contains known path keys (`path`, `file_path`, `directory`, `directory_path`)
- [x] 4.3 Implement path normalization using `os.path.normpath` and `os.path.join` to resolve relative paths and detect traversal (`../`)
- [x] 4.4 Implement boundary check — return `EXECUTE` if normalized path starts with `workspace_root`, `REQUIRE_APPROVAL` if outside, `None` if no path argument
- [x] 4.5 Integrate workspace boundary into `evaluate()` — after user rules, before risk-level fallback, only when `context["workspace_root"]` is set
- [x] 4.6 Write unit tests: path inside workspace (EXECUTE), outside workspace (REQUIRE_APPROVAL), traversal attack, no path argument (None), no workspace_root (skipped)

## 5. Models Layer — ToolPolicyModel arg_conditions Column

- [x] 5.1 Add `arg_conditions: Mapped[dict | None]` JSON column to `ToolPolicyModel` in `models/tool_policy.py`
- [x] 5.2 Add `arg_conditions: dict[str, str] | None` field to `ToolPolicyCreateSchema`
- [x] 5.3 Add `arg_conditions: dict[str, str] | None` field to `ToolPolicyReadSchema`
- [x] 5.4 Create Alembic migration to add `arg_conditions` column to `tool_policies` table (nullable, default None)
- [x] 5.5 Write model tests: create with arg_conditions, create without arg_conditions, ReadSchema from attributes
- [x] 5.6 Update `tests/conftest.py` if needed — ensure tool_policy model import already exists

## 6. ToolWorker Integration

- [x] 6.1 Extend `ToolWorker._check_access()` to pass `arguments` to `ToolAccessPolicy.evaluate()`
- [x] 6.2 Ensure backward compatibility — when no policy configured, return None as before
- [x] 6.3 Write integration tests: argument forwarding to policy, dangerous pattern blocks tool call, arg_conditions ASK triggers approval, workspace boundary auto-allows inside path

## 7. Verification

- [x] 7.1 Run `ruff check src/hecate/ tests/` — zero errors
- [x] 7.2 Run `ruff format --check src/ tests/` — zero issues
- [x] 7.3 Run `mypy src/` — zero errors
- [x] 7.4 Run `python -m pytest tests/ -q` — all tests passing
- [x] 7.5 Verify engine/tool_access.py has zero imports beyond stdlib (`__future__`, `abc`, `dataclasses`, `enum`, `fnmatch`, `logging`, `os.path`, `typing`)
