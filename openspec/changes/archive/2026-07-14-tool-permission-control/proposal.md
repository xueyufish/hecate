## Why

Hecate's existing tool security system (`ToolAccessPolicy` 5-layer + `ToolGateEvaluator` + `PreToolHook`) is hardcoded — layers cannot be added, removed, reordered, or configured per-agent. Enterprise multi-tenant deployments need per-agent tool access policies, plugin availability checks, and audit/dry-run modes. Research across 14 platforms (Amazon Bedrock AgentCore, Salesforce Agentforce, Google Gemini Enterprise, AgentScope, OpenClaw, IBM watsonx, Huawei AgentArts, Dify) shows that composable policy pipelines with declarative rule configuration is the industry standard.

The gap: OpenClaw has an 8-layer pipeline, AgentScope has Mode + Rules + Built-in Checks, Salesforce has per-action `available when` with platform RBAC, Bedrock has Cedar policy-as-code. Hecate's `ToolAccessPolicy` is comparable in depth but lacks composability and per-agent configuration.

## What Changes

- **ToolPolicyPipeline**: Composable pipeline with pluggable `PolicyLayer` ABC. Each layer evaluates `(tool, context) → PolicyDecision` (ALLOW/DENY/HIDE/REQUIRE_APPROVAL/EXECUTE_SANDBOX). Layers execute in order; DENY short-circuits.
- **5 Pipeline Layers**:
  - `PluginAvailabilityLayer` [NEW] — checks tool's plugin/MCP server is enabled
  - `ProfileLayer` [NEW] — per-agent and per-workspace allow/deny rules (DB-backed, glob pattern + arg conditions)
  - `VisibilityLayer` [REPLACES ToolGateEvaluator] — evaluates `available_when` expressions, hides tools from LLM context
  - `SecurityLayer` [WRAPS existing ToolAccessPolicy] — DangerousPattern + RuleEngine + WorkspaceBoundary + RiskLevel + Sandbox routing (zero rewrite)
  - `ModeLayer` [NEW] — global PermissionMode (DEFAULT / RESTRICTED / AUDIT)
- **PermissionMode**: 3 modes — `DEFAULT` (normal behavior), `RESTRICTED` (only allowlisted tools pass), `AUDIT` (allow all but log every decision, like Google SGP dry-run)
- **Per-Agent Policy Configuration**: `AgentPolicyConfigModel` (mode + tool allowlist/denylist) and `ToolPolicyRuleModel` (declarative rules with glob patterns and arg conditions)
- **REST API**: CRUD for policy rules and agent policy config
- **Integration**: LLMWorker uses pipeline for tool visibility filtering; ToolWorker uses pipeline for execution-time access decisions. Existing `ToolAccessPolicy` and `ToolGateEvaluator` are wrapped as pipeline layers (no rewrite, zero migration)

## Capabilities

### New Capabilities

- `tool-permission-control`: Composable policy pipeline with 5 layers (PluginAvailability, Profile, Visibility, Security, Mode), per-agent policy configuration, PermissionMode (DEFAULT/RESTRICTED/AUDIT), declarative rule engine, REST API for policy management

### Modified Capabilities

- `platform-tool-gating`: ToolGateEvaluator is replaced by VisibilityLayer in the pipeline; `available_when` evaluation semantics preserved but now runs through pipeline
- `execution-security`: ToolAccessPolicy is wrapped by SecurityLayer; internal 5-layer evaluation unchanged but now composable within the pipeline

## Impact

- **New files**:
  - `src/hecate/engine/policy_pipeline.py` — PolicyPipeline, PolicyLayer ABC, PolicyDecision enum, PolicyContext
  - `src/hecate/engine/policy_layers.py` — 5 concrete layer implementations
  - `src/hecate/models/tool_policy.py` — ToolPolicyRuleModel, AgentPolicyConfigModel + Pydantic schemas
  - `src/hecate/api/management/tool_policies.py` — REST API for policy CRUD
  - `tests/test_engine/test_policy_pipeline.py` — pipeline + layer tests
  - `alembic/versions/v0c1d2e3f4a5_add_tool_policy_models.py` — DB migration
- **Modified files**:
  - `src/hecate/engine/workers/llm_worker.py` — uses pipeline instead of ToolGateEvaluator directly
  - `src/hecate/engine/workers/tool_worker.py` — uses pipeline instead of ToolAccessPolicy directly
  - `src/hecate/services/orchestration/engine_port_adapter.py` — pipeline construction and injection
  - `src/hecate/models/agent.py` — AgentModel gains optional `policy_config_id` FK
- **Dependencies**: None new (uses existing engine ABCs, SQLAlchemy, FastAPI)
- **Migration**: Zero-risk — existing ToolAccessPolicy and ToolGateEvaluator code is preserved and wrapped, not rewritten. Backward compatible: agents without policy config use DEFAULT mode.
