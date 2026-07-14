## 1. Pipeline Core

- [x] 1.1 Create `src/hecate/engine/policy_pipeline.py` — `PolicyDecision` enum (ALLOW/DENY/HIDE/REQUIRE_APPROVAL/EXECUTE_SANDBOX/PASSTHROUGH), `PolicyContext` dataclass (tool_name, tool_meta, arguments, agent_id, workspace_id, channel_snapshot, execution_context), `PolicyLayer` ABC (`evaluate(tool_info, context) -> PolicyDecision`), `ToolPolicyPipeline` class (`evaluate_visibility(tools, context) -> list` and `evaluate_execution(tool, context) -> PolicyDecision` with deny short-circuit)
- [x] 1.2 Create `src/hecate/engine/policy_layers.py` — 5 concrete layer implementations

## 2. Layer Implementations

- [x] 2.1 `PluginAvailabilityLayer` — checks plugin enabled status via in-memory dict lookup (plugin name → enabled bool). For built-in tools (source="builtin"), always ALLOW. For MCP tools (source="mcp"), checks MCPServerRegistry.has_server(). For custom tools, always ALLOW.
- [x] 2.2 `ProfileLayer` — evaluates `ToolPolicyRuleModel` rules from DB. Loads workspace-level + agent-level rules, sorts by action (DENY→ASK→ALLOW) then priority, matches tool name via fnmatch, checks arg_conditions via fnmatch. Returns ALLOW/DENY/REQUIRE_APPROVAL or PASSTHROUGH if no rules match.
- [x] 2.3 `VisibilityLayer` — wraps existing `ToolGateEvaluator`. Evaluates `available_when` expression. Returns HIDE (during visibility filtering) or ALLOW. Preserves fail-closed semantics.
- [x] 2.4 `SecurityLayer` — wraps existing `ToolAccessPolicy`. Calls `tool_access_policy.evaluate(tool_meta, rules, context, arguments)` and maps `AccessDecision` to `PolicyDecision`. Zero internal logic change.
- [x] 2.5 `ModeLayer` — evaluates `PermissionMode`. DEFAULT: returns SecurityLayer decision unchanged. RESTRICTED: returns DENY if tool not in allowlist. AUDIT: overrides DENY→ALLOW with WARNING log, preserves REQUIRE_APPROVAL.

## 3. Data Models

- [x] 3.1 Create `src/hecate/models/tool_policy.py` — `ToolPolicyRuleModel` (id, workspace_id, agent_id nullable, tool_pattern str, action str [allow/deny/ask], priority int, arg_conditions JSON), `AgentPolicyConfigModel` (id, workspace_id, agent_id unique, mode str, tool_allowlist JSON, tool_denylist JSON), Pydantic schemas (Create/Update/Read)
- [x] 3.2 Create Alembic migration `alembic/versions/v0c1d2e3f4a5_add_tool_policy_models.py`

## 4. Worker Integration

- [x] 4.1 Update `src/hecate/engine/workers/llm_worker.py` — replace `_filter_tools()` call to `ToolGateEvaluator` with `pipeline.evaluate_visibility(tools, context)`. Pipeline constructed from layers, VisibilityLayer handles HIDE decisions.
- [x] 4.2 Update `src/hecate/engine/workers/tool_worker.py` — replace `_check_access()` call to `ToolAccessPolicy` with `pipeline.evaluate_execution(tool, context)`. Map PolicyDecision to existing execution flow (EXECUTE/EXECUTE_SANDBOX/REQUIRE_APPROVAL/DENY).
- [x] 4.3 Update `src/hecate/services/orchestration/engine_port_adapter.py` — construct `ToolPolicyPipeline` with all 5 layers, inject into LLMWorker and ToolWorker.

## 5. REST API

- [x] 5.1 Create `src/hecate/api/management/tool_policies.py` — router with prefix `/api/tool-policies`: `GET /rules` (list, filter by agent_id), `POST /rules` (create), `PUT /rules/{id}` (update), `DELETE /rules/{id}` (delete), `GET /agents/{agent_id}/config` (get agent config), `PUT /agents/{agent_id}/config` (update agent config)
- [x] 5.2 Register `tool_policies_router` in `src/hecate/main.py`

## 6. Audit Logging

- [x] 6.1 Add audit logging to `ToolPolicyPipeline.evaluate_execution()` — log every layer's decision at DEBUG level with tool_name, agent_id, layer name, decision, reason. In AUDIT mode, log WARNING when DENY overridden to ALLOW.

## 7. Backend Tests

- [x] 7.1 Test `ToolPolicyPipeline` — deny short-circuit, hide short-circuit (visibility only), all-pass returns ALLOW
- [x] 7.2 Test `PluginAvailabilityLayer` — plugin enabled/disabled, MCP server registered/unregistered, built-in tool always allowed
- [x] 7.3 Test `ProfileLayer` — workspace-level rule, agent-level rule precedence, glob matching, arg_conditions matching, no rules passthrough
- [x] 7.4 Test `VisibilityLayer` — expression passes/fails, fail-closed, no expression passthrough
- [x] 7.5 Test `SecurityLayer` — dangerous pattern deny, high-risk require_approval, sandbox routing (verify existing ToolAccessPolicy behavior preserved)
- [x] 7.6 Test `ModeLayer` — DEFAULT passthrough, RESTRICTED deny non-allowlisted, AUDIT override deny→allow with warning, AUDIT preserves require_approval
- [x] 7.7 Test REST API — CRUD rules, agent config, 404 for non-existent
- [x] 7.8 Test backward compatibility — agent without policy config uses DEFAULT mode, existing behavior unchanged

## 8. Verification

- [x] 8.1 Run `ruff check src/hecate/ tests/ && ruff format --check src/ tests/` — 0 errors
- [x] 8.2 Run `mypy src/` — 0 errors
- [x] 8.3 Run `python -m pytest tests/test_engine/test_policy_pipeline.py tests/test_api/test_tool_policies.py -q` — all pass
- [x] 8.4 Run `python -m pytest tests/test_engine/test_tool_access.py tests/test_engine/test_tool_gate.py -q` — existing tests still pass (backward compatible)
