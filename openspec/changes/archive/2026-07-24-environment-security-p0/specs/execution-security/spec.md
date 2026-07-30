## MODIFIED Requirements — 修改的需求

### 需求：ToolWorker 沙箱路由
ToolWorker 应基于 `ToolAccessPolicy.evaluate()` 决策路由工具。当 `AGENT_ENV_SANDBOX_ENFORCEMENT=true` 且决策为 `EXECUTE_SANDBOX` 时，ToolWorker 应将 shell/exec 工具和 `sandbox_enabled=True` 的 MCP 工具路由到 `DockerEnvironment.exec_shell()` 进行容器隔离执行。具有 `EXECUTE_SANDBOX` 决策的 Python 内置工具应直接执行（由 WorkspaceBoundaryPolicy 管理）。当 `AGENT_ENV_SANDBOX_ENFORCEMENT=false`（默认）时，`EXECUTE_SANDBOX` 被视为 `EXECUTE`（向后兼容）。

#### 场景：启用沙箱的工具路由到沙箱执行器
- **当** ToolWorker 执行 `sandbox_enabled=True` 的工具调用且 `AGENT_ENV_SANDBOX_ENFORCEMENT=false` 时
- **则** 调用 `port.tool_execute_sandbox()`（保留现有行为）

#### 场景：EXECUTE_SANDBOX 将 shell 工具路由到 DockerEnvironment
- **当** `AGENT_ENV_SANDBOX_ENFORCEMENT=true` 且 `ToolAccessPolicy.evaluate()` 为工具 `bash` 返回 `EXECUTE_SANDBOX` 时
- **则** 工具通过 `exec_shell()` 在 Agent 的 DockerEnvironment 容器内执行
- **且** shell 工具不调用 `port.tool_execute_sandbox()`

#### 场景：非沙箱工具路由到正常执行器
- **当** ToolWorker 执行决策为 `EXECUTE` 的工具调用时
- **则** 像之前一样调用 `port.tool_execute()`

#### 场景：沙箱不绕过审批
- **当** 工具的 `sandbox_enabled=True` 且 `risk_level="critical"` 时
- **且** 未授予审批
- **则** 工具不执行（REQUIRE_APPROVAL 优先）

## ADDED Requirements — 新增需求

### 需求：从 ToolAccessPolicy 发出 SecurityAuditEvent
ToolAccessPolicy.evaluate() 应为每次评估发出一个 SecurityAuditEvent，捕获工具名、访问决策、匹配规则、风险级别和策略版本。发射应通过 AuditSink 接口进行，以保持引擎层零依赖约束。

#### 场景：REQUIRE_APPROVAL 决策发出审计事件
- **当** `ToolAccessPolicy.evaluate()` 返回 `REQUIRE_APPROVAL` 时
- **则** 发出带有 decision="require_approval"、匹配规则和风险级别的 SecurityAuditEvent

#### 场景：DENY 决策发出审计事件
- **当** `ToolAccessPolicy.evaluate()` 因危险模式匹配返回 `DENY` 时
- **则** 发出带有 decision="deny"、reason="dangerous_pattern_matched" 的 SecurityAuditEvent
