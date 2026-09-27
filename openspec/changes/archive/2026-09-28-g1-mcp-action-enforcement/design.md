# Design: g1-mcp-action-enforcement

## Context

调查基于当前分支代码；proposal 的 Why 节记录了风险摘要。

- `AuthContext.role` 来自经验证的 workspace 成员资格；MCP middleware 将其放入 request state，工具经 `get_transport_auth_context()` 读取。
- `require_workspace_viewer/editor/admin` 已定义，但相关路由没有统一消费它们；MCP Agent、Knowledge 和 Tool 写工具主要只检查身份/workspace。
- MCP `tool_execute` 使用全局 `settings.WORKSPACE_ROOT`，再以名称调用 `ToolRegistry.execute()`。Registry 对内置名称优先执行静态内置 handler；其他工具另按名称查数据库。因此 MCP 先查到的一条 `ToolModel` 不能自动证明 Registry 最终会执行同一条记录。
- `BuiltInToolExecutor` 的路径解析会检查 resolved path 是否位于其根内；根目录需按调用者 workspace 派生。实现和测试还需覆盖符号链接指向根外的情况。
- `ToolModel` 与内置定义包含风险信息，但并非所有工具都具备可由 MCP 入口验证的完整资源范围、主体上下文和审批绑定。单看 `risk_level` 或 `approval_required` 两个字段不足以证明可执行。
- `MCP_AUTH_TYPE=none` 当前会跳过 middleware 认证，而工具 `_auth()` 因无 `AuthContext` 失败。该负例应通过真实工具调用和零副作用断言钉死。

## Goals / Non-Goals

**Goals**

- FastAPI 依赖与 MCP 工具共用同一个 workspace role 判定规则。
- 对明确列出的 REST Agent 写操作及 MCP 变更工具实施 editor 门槛和 workspace 资源过滤。
- MCP 工具预检查与最终执行使用同一确定工具身份和可信元数据；无法唯一解析或无法证明满足入口授权条件时，执行前拒绝。
- MCP 文件工具使用服务端派生 workspace 根，并通过路径解析和边界检查阻止越界读写。
- 先添加失败复现，确认旧行为确实触发缺陷，再实现并验证修复；拒绝路径断言无副作用。

**Non-Goals**

- 不实现完整 Action 应用服务、审批流转或 `tools/policy` 全策略流水线；`approval_required` 和 HIGH 风险工具在 MCP 直接执行入口拒绝。
- 不声称本 change 关闭所有 G1 路径。Workflow/Chat 工具执行、其他 REST 配置写入口、生产环境禁止 `MCP_AUTH_TYPE=none` 仍需后续处理。
- 不要求所有注册工具都可通过 MCP 直接执行。缺少可信元数据、执行身份/资源范围或授权绑定的工具在该入口失败关闭。

## Decisions

### D1:共享 workspace 角色检查

在 `core/deps_workspace.py` 增加纯函数 `ensure_workspace_role(ctx: AuthContext, minimum: WorkspaceRole) -> None`。system scope 依照现有系统权限规则通过；其他身份必须有显式 workspace 和有效角色，且角色等级达到最低要求，否则抛 `PermissionError`。`require_workspace_viewer/editor/admin` 调用该函数并在 FastAPI 边界翻译为现有 403 结构；MCP 边界转为明确授权错误。

角色判断与资源归属判断分开：拥有 editor 角色不代表可以访问其他 workspace 的 Agent、Knowledge 或 Tool。写操作须同时通过角色检查和目标资源 workspace 检查。

### D2:写操作范围

本 change 的 REST Agent 写范围为 `create/update/delete/import` 及技能关联、晋升、移除等会改变 Agent 或其关联状态的端点。每个端点须在数据库写入、外部存储操作或其他副作用前检查 editor 角色和资源归属。

MCP 变更范围为 `agent_create/update/delete`、`knowledge_create/ingest`、`tool_create`。执行/读取类工具继续要求有效 workspace 成员身份；其中 `tool_execute` 另执行 D3 的工具级准入。Workflow、模板、Tool policy 等不属于本次角色扫描范围，列为后续 G1 门槛。

### D3:工具解析、授权与执行必须绑定

MCP `tool_execute` 不允许以“按名称查询元数据，再把名称交给 Registry”作为授权保证。查询须按调用者 workspace 与明确允许共享的目录限定，并产生一个唯一、不可变的解析结果；授权检查和最终 handler/adapter 必须消费同一个结果。若现有 Registry 接口无法做到，增加受控解析/执行接口，禁止重新按名称解析出另一条数据库记录。

内置工具的权威风险元数据来自静态内置定义；数据库记录不得覆盖内置 handler 的授权属性。自定义、MCP、REST 等数据库工具必须解析到调用者可访问的唯一记录，且来源、目标及执行 adapter 与授权元数据一致。名称冲突、已删除/不可用记录或元数据缺失一律拒绝。

执行前拒绝条件包括：`approval_required=true`、`risk_level=HIGH`、风险元数据未知、身份或 workspace 不完整、工具要求但无法建立的 agent/session 上下文，以及没有可验证授权绑定的执行路径。仅允许具备明确服务端资源范围和可信授权元数据的工具通过。代码执行、Memory、浏览器和远程工具只有在本入口能建立并验证其所需上下文和策略时才可放行；否则显式拒绝，不以工具名称属于内置集合作为授权依据。

对文件工具，根目录为 `Path(settings.WORKSPACE_ROOT) / str(ctx.workspace_id)`，仅从服务端上下文派生。解析最终目标后必须确认仍位于该根内；拒绝绝对路径越界、`..` 路径穿越和解析后指向根外的符号链接。测试验证拒绝发生在读写前。非 system 身份无 workspace 时不得执行任何工具。

### D4:zero workspace 与共享资源

非 system 身份的写操作必须归属其显式 workspace，不允许通过 `ctx.workspace_id or _BUNDLED_WS` 隐式写入 zero workspace。system scope 的 bootstrap/种子操作必须显式选择并记录目标 workspace，不能依赖空值回退。

zero workspace 的可读性按资源类型明确规定。只允许被定义为共享只读的种子资源对 workspace 成员可见；共享资源的可读不代表可更新或删除。MCP 的列表过滤和按 ID 查询须实现相同的可见性规则，并有测试覆盖。不得将“列表可见”描述为“所有资源均可按 ID 读取”。

### D5:`MCP_AUTH_TYPE=none` 的失败关闭

保留现有本地开发逃生门配置，但 middleware 在此模式标记认证被禁用，受保护工具必须在业务执行前失败关闭，错误能区分认证未提供和认证被禁用。默认认证配置继续在工具调用前拒绝无效凭据。该项只证明工具级失败关闭，不等同生产配置已禁止 none 模式。

## Risks / Trade-offs

- viewer 失去本 change 覆盖的 Agent、Knowledge、Tool 写入口；发布说明应列出具体端点。
- 文件工具根从全局目录变为 workspace 子目录，旧测试/本地文件不会自动迁移；不在本 change 搬迁数据。
- 失败关闭会暂时拒绝某些原本可调用但缺少足够授权上下文的工具。以后只有接入明确的授权绑定后才能放行。
- 单次修复仍不覆盖 Workflow/Chat 和其他配置写入口，因此不得将整个 G1 标记为关闭。
- system scope 维持受限的 bootstrap 特权，但目标 workspace 必须显式，避免隐式写入共享种子目录。

## Migration Plan

1. 先添加并运行失败复现，确认角色、工具歧义、文件越界和 none 模式路径在旧实现下被捕获。
2. 实现共享检查、入口接线、受控工具解析和 workspace 文件根；每个小步复跑对应负例，完成后跑相关回归和仓库门禁。
3. 部署后 viewer 写入口和 MCP 文件路径立即按新规则生效；旧全局目录不自动迁移。回滚会恢复原授权/隔离缺口，仅在评估风险后进行。
4. 合并后更新基线记录为“本 change 覆盖入口已收紧”，同时登记 Workflow/Chat、其他写入口、生产 none 配置门禁和 Action 服务接入；G1 总体保持未关闭，直到所有门槛通过。

## Open Questions

- 实现时逐项确认哪些内置工具能在不依赖缺失 agent/session 上下文的情况下满足 D3；未通过验证的工具保持拒绝。该判定通过失败复现和工具能力检查记录，不降低 D3 的准入要求。
