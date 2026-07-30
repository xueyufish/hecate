## Why — 为什么

Hecate 现有的工具安全系统（`ToolAccessPolicy` 5 层 + `ToolGateEvaluator` + `PreToolHook`）是硬编码的——层不能被添加、移除、重新排序或按代理配置。企业多租户部署需要按代理的工具访问策略、插件可用性检查和审计/dry-run 模式。对 14 个平台（Amazon Bedrock AgentCore、Salesforce Agentforce、Google Gemini Enterprise、AgentScope、OpenClaw、IBM watsonx、Huawei AgentArts、Dify）的研究表明，可组合的策略管道配合声明式规则配置是行业标准。

差距在于：OpenClaw 有 8 层管道、AgentScope 有 Mode + Rules + Built-in Checks、Salesforce 有每个动作的 `available when` 配合平台 RBAC、Bedrock 有 Cedar 策略即代码。Hecate 的 `ToolAccessPolicy` 在深度上相当，但缺乏可组合性和按代理配置。

## What Changes — 变更内容

- **ToolPolicyPipeline**：可组合的管道，包含可插拔的 `PolicyLayer` ABC。每层评估 `(tool, context) → PolicyDecision`（ALLOW/DENY/HIDE/REQUIRE_APPROVAL/EXECUTE_SANDBOX）。按顺序执行层；DENY 短路。
- **5 个管道层**：
  - `PluginAvailabilityLayer` [新] — 检查工具的插件/MCP 服务器是否启用
  - `ProfileLayer` [新] — 按代理和按工作空间的 allow/deny 规则（数据库支持，glob 模式 + 参数条件）
  - `VisibilityLayer` [替换 ToolGateEvaluator] — 评估 `available_when` 表达式，从 LLM 上下文隐藏工具
  - `SecurityLayer` [包装现有 ToolAccessPolicy] — DangerousPattern + RuleEngine + WorkspaceBoundary + RiskLevel + Sandbox 路由（零重写）
  - `ModeLayer` [新] — 全局 PermissionMode（DEFAULT / RESTRICTED / AUDIT）
- **PermissionMode**：3 种模式 — `DEFAULT`（正常行为）、`RESTRICTED`（仅白名单工具通过）、`AUDIT`（允许所有但记录每个决策，类似 Google SGP dry-run）
- **按代理策略配置**：`AgentPolicyConfigModel`（mode + 工具允许列表/拒绝列表）和 `ToolPolicyRuleModel`（声明式规则，带 glob 模式和参数条件）
- **REST API**：策略规则和代理策略配置的 CRUD
- **集成**：LLMWorker 使用管道进行工具可见性过滤；ToolWorker 使用管道进行执行时访问决策。现有的 `ToolAccessPolicy` 和 `ToolGateEvaluator` 被包装为管道层（无需重写，零迁移）

## Capabilities — 能力

### 新能力

- `tool-permission-control`：可组合的策略管道，包含 5 层（PluginAvailability、Profile、Visibility、Security、Mode）、按代理策略配置、PermissionMode（DEFAULT/RESTRICTED/AUDIT）、声明式规则引擎、用于策略管理的 REST API

### 变更的能力

- `platform-tool-gating`：ToolGateEvaluator 被管道中的 VisibilityLayer 替换；`available_when` 评估语义保留，但现通过管道运行
- `execution-security`：ToolAccessPolicy 被 SecurityLayer 包装；内部 5 层评估不变，但现在可在管道内组合

## Impact — 影响

- **新文件**：
  - `src/hecate/engine/policy_pipeline.py` — PolicyPipeline、PolicyLayer ABC、PolicyDecision 枚举、PolicyContext
  - `src/hecate/engine/policy_layers.py` — 5 个具体层实现
  - `src/hecate/models/tool_policy.py` — ToolPolicyRuleModel、AgentPolicyConfigModel + Pydantic schemas
  - `src/hecate/api/management/tool_policies.py` — 用于策略 CRUD 的 REST API
  - `tests/test_engine/test_policy_pipeline.py` — 管道 + 层测试
  - `alembic/versions/v0c1d2e3f4a5_add_tool_policy_models.py` — 数据库迁移
- **修改的文件**：
  - `src/hecate/engine/workers/llm_worker.py` — 使用管道而不是直接使用 ToolGateEvaluator
  - `src/hecate/engine/workers/tool_worker.py` — 使用管道而不是直接使用 ToolAccessPolicy
  - `src/hecate/services/orchestration/engine_port_adapter.py` — 管道构建和注入
  - `src/hecate/models/agent.py` — AgentModel 获得可选的 `policy_config_id` 外键
- **依赖**：无新增（使用现有的 engine ABC、SQLAlchemy、FastAPI）
- **迁移**：零风险——现有的 ToolAccessPolicy 和 ToolGateEvaluator 代码被保留并包装，不被重写。向后兼容：没有策略配置的代理使用 DEFAULT 模式。
