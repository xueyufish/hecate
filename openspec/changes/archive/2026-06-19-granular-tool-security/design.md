## Context

Feature 9.4 (Execution Security) established three-layer tool access control: rule engine (tool-name glob) → risk-level fallback → sandbox routing. The rule engine matches tool names only (`fnmatch(tool_name, rule.pattern)`) — it cannot distinguish between different argument values for the same tool.

A 10-platform survey was conducted to inform the design:

| Platform | Argument Inspection | Technique |
|----------|-------------------|-----------|
| Claude Code | ✅ | Glob on command strings, compound command parsing (`Bash(rm *)`) |
| HermesAgent | ✅ | Regex dangerous patterns + LLM-assisted approval |
| AgentScope 2.0 | ✅ | Tree-sitter AST + prefix patterns + 7-layer analysis |
| AgentArts | ❌ | Sandbox isolation only, no argument inspection |
| openJiuwen | ❌ | Kernel-level (Landlock + seccomp) |
| Salesforce/Dify/IBM/Google | ❌ | Tool-name level only |

**Current state**: `ToolAccessPolicy.evaluate()` receives `tool_meta` (risk_level, approval_required, sandbox_enabled), `rules` (list of `ToolRule`), and `context` (tool_name). The `ToolWorker._check_access()` method already receives parsed `arguments` dict but does not forward it to the policy evaluator.

**Constraint**: Engine layer maintains zero external dependencies (stdlib only). All runtime context (workspace_root, session info) must be passed via context dict.

## Goals / Non-Goals

**Goals:**
- Extend `ToolRule` with optional `arg_conditions` for glob-based argument value matching
- Extend `ToolAccessPolicy.evaluate()` to match argument conditions after tool-name match
- Add built-in dangerous pattern detection that cannot be overridden by user rules
- Add workspace boundary enforcement for file-path arguments
- Maintain full backward compatibility with existing name-only rules

**Non-Goals:**
- Compound command parsing (`&&`, `||`, `;`) — deferred to future enhancement (Claude Code does this, but adds complexity)
- Regex-based argument matching — glob patterns cover 80% of use cases (per survey)
- LLM-assisted approval classification (HermesAgent smart approval) — future enhancement
- Tree-sitter AST command analysis (AgentScope approach) — over-engineering for MVP
- Kernel-level isolation (Landlock/seccomp) — orthogonal to argument inspection, already handled by Docker sandbox executor (9.4c)

## Decisions

### D31: Glob pattern matching for argument conditions

Use `fnmatch` glob patterns for argument value matching, consistent with existing tool-name matching.

```python
ToolRule(DENY, "write_file", arg_conditions={"path": "*.env"})
ToolRule(ASK,   "execute_code", arg_conditions={"code": "*os.system*"})
ToolRule(ALLOW, "read_file", arg_conditions={"path": "src/*"})
```

**Rationale**: Claude Code uses glob (`Bash(rm *)`), AgentScope uses prefix patterns (`npm run:*`). Both are glob-like. Glob is already in our codebase (`fnmatch`), requires zero new dependencies, and is well-understood by users. Regex (HermesAgent) was rejected for higher learning cost and debugging difficulty.

**Alternatives rejected**:
- Regex matching — higher complexity, users struggle with regex patterns
- Tree-sitter AST parsing — requires external dependency (tree-sitter), over-engineering for MVP
- Prefix-only matching (`npm run:*`) — subset of glob, no advantage

### D32: Built-in dangerous patterns as DENY baseline

Define `DANGEROUS_PATTERNS` constant in `engine/tool_access.py` — a list of `(tool_name_glob, arg_key, arg_glob, description)` tuples. These patterns are checked BEFORE user-defined rules and cannot be overridden by `ALLOW` rules.

```python
DANGEROUS_PATTERNS: list[DangerousPattern] = [
    # Shell commands
    DangerousPattern("bash", "command", "rm -rf /",       "recursive root delete"),
    DangerousPattern("bash", "command", "mkfs*",           "filesystem format"),
    DangerousPattern("bash", "command", "dd if=*of=/dev/", "disk overwrite"),
    DangerousPattern("bash", "command", "*curl*|*sh",      "remote code execution"),
    DangerousPattern("bash", "command", ":*()*{*}*",        "fork bomb"),
    # Code execution
    DangerousPattern("execute_code", "code", "*os.system*",   "OS system call"),
    DangerousPattern("execute_code", "code", "*subprocess*",   "subprocess invocation"),
    DangerousPattern("execute_code", "code", "*eval(*",         "eval execution"),
    DangerousPattern("execute_code", "code", "*exec(*",         "exec execution"),
    # Sensitive files
    DangerousPattern("write_file", "path", "*/.ssh/*",      "SSH key write"),
    DangerousPattern("write_file", "path", "*/.env*",       "env file write"),
    DangerousPattern("write_file", "path", "*/.bashrc",     "shell config write"),
    DangerousPattern("write_file", "path", "/etc/*",        "system config write"),
    DangerousPattern("read_file",  "path", "/etc/passwd",   "password file read"),
    DangerousPattern("read_file",  "path", "*/.ssh/id_*",   "SSH key read"),
    # SQL dangerous operations
    DangerousPattern("*", "code",  "*DROP TABLE*",          "SQL table drop"),
    DangerousPattern("*", "code",  "*DELETE FROM*",         "SQL delete without WHERE"),
]
```

**Rationale**: HermesAgent's `DANGEROUS_PATTERNS` provides an effective security baseline. AgentScope's 7-layer analysis confirmed the same categories. These patterns give out-of-the-box security without requiring users to configure every rule manually. The patterns are checked first (before user rules) and result in `DENY` — they cannot be overridden.

**Alternatives rejected**:
- Configurable dangerous patterns (stored in DB) — adds complexity, these patterns rarely change
- ASK instead of DENY for dangerous patterns — DENY is safer; users can add explicit ALLOW rules with `arg_conditions` for safe variants (e.g., `rm -rf node_modules/`)

### D33: Workspace boundary as policy layer (not model field)

Add `WorkspaceBoundaryPolicy` as a helper class in `engine/tool_access.py` that checks if file-path arguments resolve within `workspace_root`. The `workspace_root` is passed via `context` dict (from services layer).

```python
class WorkspaceBoundaryPolicy:
    def check(self, tool_name: str, arguments: dict, workspace_root: str) -> AccessDecision | None:
        """Check if tool operates on files within workspace boundary.

        Returns EXECUTE if path is inside workspace, REQUIRE_APPROVAL if outside,
        None if tool has no path argument.
        """
```

Evaluation happens between user rules and risk-level fallback:

```
Layer 1: Dangerous patterns (DENY — cannot override)
Layer 2: User rules (DENY → ASK → ALLOW with arg_conditions)
Layer 3: Workspace boundary (inside → ALLOW, outside → ASK)
Layer 4: Risk level fallback
Layer 5: Sandbox routing
```

**Rationale**: Workspace boundary is conceptually between explicit rules (user knows best) and risk-level defaults (system fallback). If a user explicitly configures a rule, it takes precedence. If no rule matches, workspace boundary provides a sensible default: inside = trusted, outside = untrusted.

**Alternatives rejected**:
- Auto-generate ALLOW/ASK rules from workspace boundary — less transparent, harder to debug
- Put workspace boundary in services layer — breaks engine's zero-dependency constraint
- Check workspace boundary before user rules — user should be able to override workspace defaults

### D34: arg_conditions in ToolPolicyModel (JSON column)

Add `arg_conditions` JSON column to `ToolPolicyModel` for persisted argument-level rules.

```python
class ToolPolicyModel(BaseModel):
    # ... existing fields ...
    arg_conditions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

Example row:
```json
{
  "workspace_id": "...",
  "rule_action": "deny",
  "tool_pattern": "write_file",
  "arg_conditions": {"path": "*.env"},
  "priority": 10
}
```

**Rationale**: Consistent with existing `ToolPolicyModel` storage pattern. JSON column is flexible enough for any argument key-value combinations. SQLAlchemy JSON column type works across PostgreSQL, MySQL, and SQLite.

**Alternatives rejected**:
- Separate `ToolArgPolicyModel` table — premature normalization, adds join complexity
- Store as semicolon-separated string — fragile, no type safety
- Don't persist (runtime only) — users lose ability to configure persistent argument-level rules

### D35: Evaluation order — dangerous patterns first, arg_conditions after name match

The extended evaluation flow:

```
1. DANGEROUS_PATTERNS check (DENY — bypass-immune, cannot be overridden)
2. User rule matching (DENY → ASK → ALLOW)
   For each tier, sorted by priority:
     a. Check tool name with fnmatch
     b. If name matches AND rule has arg_conditions:
        Check each arg_condition with fnmatch(arg_value, pattern)
        Only match if ALL conditions match
     c. If name matches AND rule has NO arg_conditions:
        Match immediately (name-only match — backward compatible)
3. Workspace boundary check (if no rule matched and tool has path arg)
4. Risk level fallback
5. Sandbox routing
```

**Rationale**: Dangerous patterns must be checked first to ensure they cannot be bypassed. Within user rules, name-only rules are "broader" and arg_conditions rules are "more specific" — priority field controls ordering within each tier. Backward compatibility is maintained: existing rules without arg_conditions work exactly as before.

## Risks / Trade-offs

- **[Glob limitation]** Glob cannot express complex patterns like "DELETE FROM without WHERE clause" → Mitigation: Use `*DELETE FROM*` as dangerous pattern (over-matches but safer), users can add ALLOW rules for safe variants
- **[No compound command parsing]** `bash(safe-cmd *)` won't protect against `safe-cmd && dangerous-cmd` → Mitigation: Document as known limitation; future enhancement to parse `&&`, `||`, `;` (Claude Code approach)
- **[Dangerous pattern false positives]** `*subprocess*` in code blocks legitimate use of subprocess module → Mitigation: Users can add `ALLOW` rules for specific safe patterns; dangerous patterns use DENY which is fail-safe
- **[Workspace root availability]** If `context["workspace_root"]` is not set, workspace boundary check is skipped → Mitigation: Services layer must provide workspace_root; documented as requirement
- **[Performance]** Each tool call now checks dangerous patterns + user rules + workspace boundary → Mitigation: Pattern lists are small (< 20 entries), fnmatch is fast; measured < 0.1ms per evaluation
