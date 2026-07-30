## Context — 背景

Hecate 的 P2 Workflow Canvas 已完成，包含 6 种节点类型（conversation, tool-call, condition, agent, knowledge-retrieval, variable-set）和可视化 DAG 编辑器。引擎已支持：

- `NodeType.AGENT` — 已定义但仅在 `_TestWorker` 中有模拟执行
- `Command(goto=...)` — 引擎级别控制转移，覆盖正常边解析
- `EnginePort` — 引擎和服务之间的抽象边界（llm_invoke, tool_execute, knowledge_query 等）
- `Worker` / `WorkerPool` — 节点执行的分发抽象
- `PregelRuntime` — 带中断/恢复和 checkpoint 的 BSP 执行循环

缺失的部分：
1. **真实 Agent 执行**: AGENT 节点需要解析 AgentModel，构建其上下文，调用其 LLM，并返回结果
2. **Handoff 机制**: Agent 应能在对话中途将控制转移给另一个 Agent（Swarm 风格）
3. **Agent-as-Tool**: Agent 应可作为工具被其他 Agent 调用（同步委派）
4. **画布多 Agent 支持**: 用于组合多 Agent 工作流的可视化工具

根据 AD-7，所有编排模式都是 Graph 模板。P2 范围 = Handoff + 多 Agent 可视化编排。Pipeline 和 Broadcast 为 P3。

## Goals / Non-Goals — 目标 / 非目标

**目标:**
- G1: AGENT 节点类型执行真实的 Agent 执行——按 ID 解析 AgentModel，构建隔离上下文（system prompt、工具、知识库），调用 LLM，将结果返回父图
- G2: Handoff——Agent 可返回 `Command(goto=target_agent_node_id)` 转移控制；对话上下文随 handoff 流动
- G3: Agent-as-Tool——将其他 Agent 暴露为可调用工具，使 LLM 可通过工具调用调用它们（层级委派）
- G4: 编排模板——常见模式的预构建 Graph DSL 定义（分类、管线、层级）
- G5: 画布多 Agent 支持——Agent 调色板、handoff/委派边类型、模板选择器

**非目标:**
- Pipeline 模式（顺序确定性链）——推迟到 P3
- Broadcast 模式（共享消息空间）——推迟到 P3
- Agent 到 Agent 通信协议（A2A）——P4
- 跨进程分布式多 Agent 执行——已通过 WorkerPool 设计，P3 Temporal
- Agent 间的记忆隔离——使用现有 Channel 隔离，每个 Agent 的专用 L1/L3 记忆是 P2 记忆系统的工作
- 并发多 Agent 写入的冲突解决——P3

## Decisions — 决策

### D1: 通过 EnginePort 的 Agent 执行

**决策**: 向 `EnginePort` 添加 `agent_execute` 方法，用于真实的 Agent 节点执行。引擎在处理 AGENT 类型节点时调用此端口。

**理由**: 引擎按设计零外部依赖。在引擎内部添加 Agent 解析逻辑会破坏层边界。端口模式保持引擎干净——它只需将 agent_id 和 channel 快照传递给端口，取回 WorkerResult。

**考虑的替代方案**: 在引擎层的 `AgentWorker` 类内直接查找 AgentModel——被拒绝，因为它会将 SQLAlchemy 模型导入引擎。

**端口方法**:
```python
async def agent_execute(
    self,
    agent_id: UUID,
    messages: list[dict],
    channel_snapshot: dict,
    context: dict | None = None,
) -> dict:
    """执行 agent 并返回其响应。
    
    返回包含以下键的 dict:
    - response: agent 的响应消息
    - tool_calls: 执行期间产生的工具调用
    - usage: token 使用统计
    """
```

### D2: 通过 Command(goto) + HandoffTool 的 Handoff

**决策**: Handoff 实现为一个特殊工具（`handoff_to_agent`），LLM 可调用。该工具返回 `Command(goto=target_node_id)`。Pregel 运行时已在 `_resolve_next_nodes()` 中处理 `Command(goto=...)`。

**理由**: 引擎已支持 `Command(goto=...)`——无需引擎变更。当图具有通过 handoff 边连接的 agent 节点时，handoff 工具自动注入到 Agent 的工具列表中。这遵循 Swarm 模式，其中 handoff 是一个工具调用。

**流程**:
1. Graph DSL 在 agent 节点之间定义一条带有 `type: "handoff"` 的边
2. 源 Agent 的 LLM 被调用时，注入 `handoff_to_agent` 工具
3. LLM 调用 `handoff_to_agent(target="specialist")`
4. Worker 返回 `Command(goto="specialist")`
5. Pregel 将下一个节点解析为 "specialist"，执行该 agent 节点

**考虑的替代方案**: Handoff 作为单独的节点类型——被拒绝，因为它需要引擎变更且不符合 Swarm 心智模型。

### D3: 通过动态工具注册的 Agent-as-Tool

**决策**: Agent 可作为工具暴露给其他 Agent。在配置 AGENT 节点时设置 `invocation_mode: "tool"`，目标 Agent 注册为源 Agent 工具列表中的可调用工具。LLM 像调用其他工具一样调用它。

**理由**: 层级委派（父→子）是最常见的多 Agent 模式。将 Agent 暴露为工具让 LLM 决定何时委派，这比硬编码的图边更灵活。

**工具 schema**:
```json
{
  "name": "agent_{agent_name}",
  "description": "委派给 {agent_name}: {agent_persona}",
  "parameters": {
    "type": "object",
    "properties": {
      "task": {"type": "string", "description": "要委派的任务"}
    }
  }
}
```

**考虑的替代方案**: 子图嵌套（agent 节点包含完整的子图）——推迟到 P3，因为需要父图和子图之间的状态映射。

### D4: 上下文隔离策略

**决策**: 每个 Agent 执行获得隔离的上下文。System prompt、工具和知识库来自 AgentModel 定义。对话消息通过 channel 映射从父图传递。

**理由**: Agent 应是自包含的单元。专业 Agent 不应看到通用 Agent 的 system prompt，反之亦然。这匹配"Agent 作为独立实体"的心智模型。

**实现**: 
- `agent_execute` 端口方法接收 agent_id 和 messages
- 端口实现加载 AgentModel，构建隔离上下文（system_prompt + agent 的工具 + agent 的知识库）
- 来自父图的消息作为对话历史传递
- Agent 的响应返回给父图

### D5: 编排模板作为 Graph DSL JSON

**决策**: 编排模板作为 Graph DSL JSON 文件存储，通过 API 提供。无需新 DB 表——模板是与应用捆绑的静态资源。

**理由**: 模板是不可变的起点。用户选择模板，画布加载它，用户自定义。无需模板管理系统。简单且与"一切都是 Graph"原则一致。

**模板**:
1. **客服分类**: 路由器 agent →（账单 | 技术 | 通用）专业 agent
2. **内容管线**: 研究员 → 写手 → 评审（顺序 agent 链）
3. **层级监督者**: 监督者 agent → N 个工作 agent（基于工具的委派）

### D6: 画布多 Agent 增强

**决策**: 用 Agent 特定功能扩展现有画布：
- Agent 调色板：侧边栏显示可拖入画布的可用 Agent
- 边类型区分：实线 = invoke-as-tool（同步），虚线 = handoff（控制转移）
- 模板选择器：用于选择和加载编排模板的模态框
- 多 Agent 执行视图：在测试运行期间显示当前正在执行的 Agent

**理由**: 复用现有画布基础设施。无需新画布组件——只需增强 agent 节点、添加 agent 调色板、区分边渲染。

## Risks / Trade-offs — 风险与权衡

- **[风险] Agent 执行延迟**: 每个 agent 节点调用一次 LLM，延迟累积。→ 缓解措施：每个 agent 节点支持流式，开发模式使用测试运行器模拟模式。
- **[风险] 循环 handoff**: Agent A 移交给 B，B 移交给 A。→ 缓解措施：Pregel max_supersteps 限制（默认 100）。为 handoff 边在编译器中添加循环检测。
- **[风险] 上下文膨胀**: 多 agent 图传递增长的对话历史。→ 缓解措施：每个 agent 节点的上下文预算（复用 BudgetManager），P2 记忆工作中的 L2 压缩。
- **[权衡] 子图嵌套推迟**: AGENT 节点直接执行 agent 的 LLM（通过端口），而不是作为嵌套子图。这更简单但不如完整状态映射灵活。→ P2 可接受；子图嵌套是 P3。
- **[权衡] 模板为静态文件，非 DB 管理**: 无法通过 API 创建/编辑模板。→ P2 可接受；模板市场是 P4。
