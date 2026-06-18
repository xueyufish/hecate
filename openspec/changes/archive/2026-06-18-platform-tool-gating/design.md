## Context

Hecate's LLMWorker passes the full tool list to the LLM on every invocation via `node_config.get("tools")` → `context_assemble` → `llm_invoke`. There is no mechanism to conditionally hide tools based on runtime state. This causes context bloat when agents have many tools, and prevents business-rule enforcement (e.g., "admin tools only visible to admins").

10-platform research findings:
- **Salesforce Agentforce** uses `available when` with a custom DSL (`@variables.verified == True`) — soft gate, per-invocation, platform-evaluated
- **Google ADK** uses `ToolPredicate` (Python callable) — soft gate, per-invocation
- **Alibaba AgentScope** uses Tool Group + Meta Tool — LLM self-manages group activation
- **OpenClaw** uses multi-layer policy system (profile → allow/deny → provider → sandbox)
- **All platforms use soft gate only** — no execution-layer hard block
- **Dify** has open issue #27887 requesting this exact feature

Current codebase injection point: `LLMWorker.execute()` line 191 (`tools = node_config.get("tools")`) and `execute_stream()` line 330.

## Goals / Non-Goals

**Goals:**
- Add `available_when` field to ToolModel for declarative tool visibility conditions
- Evaluate conditions per-invocation before LLM call (soft gate)
- Support Python-safe expression language with runtime context variables
- Zero new external dependencies (use Python built-in `eval()` with restricted namespace)
- Backward compatible — tools without `available_when` behave exactly as before

**Non-Goals:**
- Hard gate at execution layer (10-platform consensus: not needed)
- LLM self-management of tool groups (AgentScope pattern — deferred to future feature)
- Tool search / deferred loading (Claude pattern — orthogonal concern)
- Visual canvas UI for editing `available_when` expressions (future enhancement)
- Migration of existing tools (field is nullable, defaults to None = always available)

## Decisions

### Decision 1: Expression Language — Python-safe `eval()` with restricted namespace

**Choice**: Use Python's built-in `eval()` with a restricted namespace (`__builtins__: {}`, no `__import__`).

**Rationale**: 
- Salesforce uses a custom DSL; Google ADK uses Python callables; IBM uses natural language
- Python expressions are immediately familiar to Hecate's developer audience
- No new dependency (unlike CEL which needs `cel-python`)
- The `eval()` namespace is restricted to only the provided context variables — no builtins, no imports, no attribute access to dangerous objects

**Expression examples**:
```python
# Simple equality
"user_role == 'admin'"

# Compound with and/or
"phase == 'EXECUTE' and budget_remaining > 1000"

# Membership check
"'delete' in user_permissions"

# Negation
"not user_role == 'guest'"
```

**Alternatives considered**:
- **CEL (Common Expression Language)**: Google's expression language. Safe, well-specified, but adds `cel-python` dependency. Overkill for simple conditions.
- **JSON Logic**: Structured JSON conditions. Safe but verbose and hard to read/write. Example: `{"and": [{"==": [{"var": "phase"}, "EXECUTE"]}, {">": [{"var": "budget_remaining"}, 1000]}]}`
- **ast.literal_eval**: Too restrictive — only supports literals, no comparisons or boolean logic.

### Decision 2: Injection Point — LLMWorker `_filter_tools()` method

**Choice**: Add a private `_filter_tools(tools, execution_context, channel_snapshot)` method in `LLMWorker`, called after `tools = node_config.get("tools")` and before `PreLLMHook`.

```
LLMWorker.execute()
  ① tools = node_config.get("tools")              ← L191 extract
  ② tools = self._filter_tools(tools, ...)         ← NEW: evaluate available_when
  ③ PreLLMHook.on_pre_llm_call(tools=tools)        ← L196 hook sees filtered list
  ④ context_assemble(tools=tools)                  ← L224 shape filtered list
  ⑤ llm_invoke(tools=shaped_tools)                 ← L251 LLM sees filtered list
```

**Rationale**: 
- Filtering before PreLLMHook means hooks see the already-filtered list (consistent)
- All platforms filter before LLM call — this is the earliest sensible point
- Only LLMWorker needs the filter — ToolWorker already uses PreToolHook as execution-level guard

**Alternatives considered**:
- **Extend PreLLMHook to modify tools**: Would require changing GuardrailResult to support tool list modification. Adds complexity to the hook contract for a single use case.
- **New ToolGateHook ABC**: Over-engineered for a simple filter. Would be the 6th hook type.
- **Filter in ToolRegistry**: Too late — ToolRegistry handles execution routing, not LLM visibility. Tools need to be filtered before the LLM sees them.

### Decision 3: ToolGateEvaluator — standalone evaluator class

**Choice**: Implement `ToolGateEvaluator` as a standalone class in `engine/tool_gate.py` (not an ABC).

```python
class ToolGateEvaluator:
    """Evaluates available_when expressions against runtime context."""

    def evaluate(self, expression: str, context: dict) -> bool:
        """Evaluate a single available_when expression. Returns True if tool is available."""

    def filter_tools(
        self, tools: list[dict], context: dict
    ) -> list[dict]:
        """Filter tool list, removing tools whose available_when evaluates to False."""
```

**Rationale**:
- Keeps expression evaluation logic in one place (testable, reusable)
- Not an ABC because there's only one evaluation strategy (Python eval with restricted namespace)
- Follows engine layer zero-deps rule
- If we later want pluggable evaluators (CEL, JSON Logic), this class can become an ABC

**Alternatives considered**:
- **Inline in LLMWorker**: Simpler but harder to test in isolation and duplicates logic between `execute()` and `execute_stream()`.
- **New engine ABC**: Over-engineered. No need for pluggability at this stage. Can extract ABC later if needed.

### Decision 4: Context Variables — flat dict from execution_context + channel_snapshot

**Choice**: Build a flat context dict by merging:
- `execution_context` keys: `session_id`, `superstep`, `trace_id`
- `channel_snapshot` keys: `_user_id`, `_agent_id`, `_turn_index`
- Derived values: `phase` (from Task Phase Detection 4.9), `budget_remaining` (from Token Budget 4.10), `user_role` (from RBAC context)

```python
context = {
    "session_id": "...",
    "superstep": 3,
    "user_id": "...",
    "user_role": "admin",        # from RBAC
    "turn_index": 5,
    "phase": "EXECUTE",           # from Task Phase Detection
    "budget_remaining": 8000,     # from Token Budget
}
```

**Rationale**:
- Flat namespace is simplest for expression authors (`user_role == 'admin'` vs `context.user.role == 'admin'`)
- Variables that aren't available (e.g., no RBAC context) simply aren't in the dict — expression referencing them raises NameError, which is caught and treated as "tool unavailable" (fail-closed)

**Alternatives considered**:
- **Nested context object** (`context.user.role`): More structured but verbose for expression authors and requires a context object class.
- **Prefixed variables** (`@variables.user_role` like Salesforce): Adds DSL complexity without benefit in a Python-native system.

### Decision 5: Fail-closed on evaluation errors

**Choice**: If `available_when` expression raises any exception (NameError, SyntaxError, TypeError), the tool is treated as **unavailable** (filtered out).

**Rationale**:
- Security-first: if we can't determine a tool is safe, hide it
- Prevents error-driven tool exposure (malformed expression accidentally showing sensitive tools)
- Logs a WARNING so developers can debug their expressions

**Alternatives considered**:
- **Fail-open** (show tool on error): More permissive but risky for security-sensitive tools. One typo could expose admin tools.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| **`eval()` security**: Malicious expression could attempt to access dangerous functions | Restricted namespace (`__builtins__: {}`, no `__import__`). Only context variables are in scope. Expression authors are developers with code access anyway. |
| **Performance**: Evaluating expressions per-tool per-invocation adds overhead | Expressions are short (< 100 chars typically). `eval()` on simple expressions is microsecond-level. For 20 tools, total overhead < 1ms. Negligible vs LLM latency. |
| **Derived variables unavailable**: `phase`, `budget_remaining`, `user_role` may not always be populated | Fail-closed: missing variables cause NameError → tool hidden. Developers must ensure context is populated before relying on it. |
| **Expression debugging**: Developers may struggle with expression syntax errors | Log WARNING on evaluation failure with expression text and available variables. Future: dry-run validator in tool config UI. |
| **No hard gate**: LLM could theoretically hallucinate a call to a gated tool | PreToolHook in ToolWorker serves as execution-level guard. ToolRegistry.execute() also checks tool existence. Two layers of defense. |
