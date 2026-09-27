# Proposal: g1-mcp-action-enforcement

## Why

G1 基线已确认两个具体旁路：MCP 变更类工具仅验证身份和 workspace，没有 editor 角色门槛；MCP `tool_execute` 使用全局 `WORKSPACE_ROOT` 创建 `BuiltInToolExecutor`，并按工具名调用 `ToolRegistry`，没有把授权时检查的工具身份与最终执行对象绑定。REST Agent 写入口也缺少角色门槛。仅增加 workspace 子目录还不足以证明安全：同名工具可能使预检查元数据与 Registry 实际执行记录不同；路径工具还必须防止解析后落到 workspace 根之外。

本 change 在完整 Action 应用服务接入之前，收紧明确列出的 MCP 写入口、REST Agent 写入口和 MCP 直接工具执行入口。它为尚不能建立服务端资源范围或可信授权依据的工具定义失败关闭行为。它不宣称关闭 Workflow/Chat 等其他入口的 G1 风险。

## What Changes

- MCP `agent_create/update/delete`、`knowledge_create/ingest`、`tool_create` 要求显式 workspace 的 editor 及以上角色；无 workspace 的受限身份不得写入 zero workspace。
- REST Agent `create/update/delete/import` 及技能关联、晋升、移除等 Agent 写入口要求 editor 及以上角色，并在数据库或外部副作用前完成角色和资源归属校验。
- MCP `tool_execute` 从已验证的 `AuthContext` 派生 workspace 与文件根；授权检查和最终执行必须绑定到同一个确定的工具记录或权威内置定义。审批必需、高风险、元数据不可信、解析歧义或缺少授权上下文的工具在副作用前显式拒绝。
- 仅放行具有明确服务端资源范围和可信授权元数据的工具。调用者不能通过传入 workspace、身份、资源根或绝对路径扩大权限；文件工具限定在 workspace 根内，路径穿越和指向根外的符号链接均拒绝。
- `MCP_AUTH_TYPE=none` 保持开发逃生门配置，但该模式下没有任何受保护工具可执行；默认认证模式仍在工具调用前拒绝匿名请求。
- 先加入失败复现，再修复并复跑。覆盖 viewer 写入、跨租户同名工具与文件、受保护工具拒绝、无认证模式，以及零副作用断言。

**BREAKING**：viewer 失去本 change 列出的 Agent、Knowledge、Tool 配置写入口；MCP 文件操作转到 `<WORKSPACE_ROOT>/<workspace_id>/`。这是权限和资源范围收紧，可能影响现有测试或本地文件布局。

## Capabilities

### New Capabilities

- `action-authorization`：为本 change 覆盖的 REST Agent 写操作和 MCP 变更工具规定一致的角色门槛、服务端主体/租户绑定、共享种子资源写保护，以及不能安全授权时失败关闭的要求。该能力是 step7 完整 Action 应用服务的基础，不代表所有入口已统一。

### Modified Capabilities

- `mcp-server`：Agent/Knowledge/Tool 写工具增加角色门槛；`tool_execute` 增加工具身份解析一致性、执行上下文、文件资源根及失败关闭要求；无认证模式下受保护工具不可用。

## Impact

- 代码：`src/hecate/tools/mcp/server.py`、`src/hecate/core/deps_workspace.py`、`src/hecate/studio/api/agents.py`；若现有 Registry 无法把预检查的确定工具对象传入执行路径，需在 `src/hecate/tools/tool/registry.py` 增加受控解析/执行接口。
- 测试：MCP server、Agent API、tenant isolation 和 tool registry/executor 相关测试；新增失败复现必须先验证当前旁路，再随修复转绿。
- 无数据库迁移、无新依赖；本 change 不实现完整 Action 应用服务，也不修改 Workflow/Chat 所有工具入口。
- 后续门槛：Workflow/Chat 工具执行、其他 REST 写配置入口、生产配置禁止 `MCP_AUTH_TYPE=none`，以及 step7 的统一 Action 服务接入。本 change 完成后只能将其覆盖范围记为已收紧，不能将 G1 总体标记为关闭。
