## Why — 动机

P2 Workflow Canvas 已完成（49/51 任务），提供了包含 6 种节点类型的可视化 DAG 编辑器。然而，画布目前将每个工作流视为孤立图——用户无法将多个 Agent 组合为协作工作流。多 Agent 编排是 Agent 平台的核心用例（AD-7），也是画布之后的自然下一步。没有它，Hecate 只是一个带可视化编辑器的单 Agent 工具，而非多 Agent 平台。

根据 AD-7，所有编排模式（层级委派、移交、流水线、广播、选择器等）统一为 Graph 模板。P2 范围涵盖：**Handoff**（最常见模式——客服分类、通用→专家路由）和 **多 Agent 可视化编排**（将多个 Agent 拖入画布，定义协作拓扑）。Pipeline 和 Broadcast 推迟到 P3。

## What Changes — 变更内容

- **Agent 节点增强**: 现有的 `NodeType.AGENT` 节点目前委托给一个带模拟执行的子图。增强为实际解析和调用按 ID 配置的 Agent，具有正确的上下文隔离（父→子状态映射）和结果传播（子→父）。
- **Handoff 机制**: 引入基于 `Command(goto=agent_id)` 的控制转移，Agent 可在对话中途将执行移交给另一个 Agent。接收 Agent 继承对话上下文并继续执行。这映射到 Swarm 风格的 handoff。
- **AgentTool**: 将其他 Agent 暴露为可调用工具——Agent 可以像工具调用一样调用另一个 Agent，接收结果并继续。这实现了层级委派而无需子图嵌套。
- **多 Agent 画布支持**: 扩展工作流画布以支持从调色板拖入多个 Agent，用边连接，并配置 handoff/委派关系。可视化区分"作为工具调用"（同步，结果返回）和"移交到"（控制转移）。
- **编排模板**: 常见多 Agent 模式的预构建 Graph 模板——客服分类（路由器→专家）、内容管线（研究员→写手→评审）、层级委派（监督者→工作者）。
- **上下文隔离**: 每个 Agent 执行获得独立的上下文窗口——system prompt、工具、知识库限定于 Agent 定义，不从调用者继承。共享状态通过 Channel 映射流动。

## Capabilities — 能力

### New Capabilities — 新增能力
- `agent-handoff`: Agent 到 Agent 通过 Command(goto) 的控制转移，带对话连续性的上下文移交，为 LLM 自动生成 handoff 工具
- `agent-invocation`: 将 Agent 作为工具调用（同步委派），结果传播回调用者，错误处理和超时
- `multi-agent-canvas`: 多 Agent 工作流的画布增强——Agent 调色板、handoff 边、编排模板、多 Agent 可视化调试
- `orchestration-templates`: 常见多 Agent 模式的预构建 Graph 模板（分类、管线、层级）

### Modified Capabilities — 修改的能力
- （无——现有 spec 不需要需求级别的变更）

## Impact — 影响

- **引擎**: `NodeType.AGENT` worker 需要真实的 Agent 解析和执行（当前为模拟）。`Command` 已支持 `goto`——无需变更。
- **服务**: 新增 `AgentOrchestrationService` 处理 handoff 逻辑、Agent 解析、上下文映射。依赖现有 `AgentService` 和 `ConversationService`。
- **API**: 新增编排模板端点（`GET /api/orchestration-templates`），增强工作流测试运行器中 Agent 节点执行。
- **前端**: 画布 Agent 调色板（列出可用 Agent）、handoff 边类型（虚线 vs 实线）、编排模板选择器、多 Agent 执行可视化。
- **数据库**: 无 schema 变更——使用现有 `agents`, `workflows`, `sessions` 表。Agent 间关系通过 Graph DSL 边表达，而非新的关联表。
- **依赖**: 无新增外部依赖。Handoff 构建在现有 `Command` + `PregelRuntime` 之上。Agent 调用复用 `EnginePort` 抽象。
