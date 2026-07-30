## Context — 背景

Hecate 已经拥有多 Agent handoff 的所有部件：

- **DSL**：`trigger="handoff"` 和 `trigger="dynamic_handoff"` 是有效的边触发器（`graph-dsl.schema.json`）。
- **解析器 + 编译器**：`_validate_handoff_edges()` 对两种触发器执行循环检测。
- **辅助模块**：`services/orchestration/handoff.py` 提供了 `build_handoff_tool_schema`、`inject_handoff_tools`、`validate_handoff_target`、`is_handoff_tool_call`、`create_handoff_worker_result` — 均已进行单元测试。
- **运行时**：`PregelRuntime._resolve_next_nodes()`（pregel.py:502）已支持 `Command(goto=...)`。
- **先例**：`invocation_mode="tool"`（agent-as-callable-tool，2026-07-18 发布）执行反向操作 — 使用相同的 `_agent_tools` 通道和 `AgentDefinition` 配置将 Agent 注册为父工具。

链条在中间断裂：没有执行器调用 `inject_handoff_tools` 或检测 LLM 响应中的 `handoff_to_agent`，因此从未产生 `Command(goto=...)`。辅助模块是死代码。

行业趋同（OpenAI Swarm 2024、OpenAI Agents SDK 2025、Google ADK 2025、LangGraph、AutoGen v0.4+）确认了设计模式：

1. Handoff 是一种特殊的工具调用（每个目标一个工具，或带 `target` 枚举的单个工具）。
2. 接收 Agent 的上下文工程至关重要 — 根据用例传递完整历史、新上下文或摘要上下文。
3. 工具调用/工具响应对必须在 handoff 边界上保持完整，否则下游 LLM 会看到格式错误的历史记录。

约束条件（来自 AGENTS.md）：

- `engine/` 不能从 `services/`、`api/` 或 `models/` 导入（仅 `jsonschema`）。
- 所有公共代码需要类型注解；没有 `as any`，没有 `@ts-ignore` 等效物。
- 每个文件顶部都有 `from __future__ import annotations`。
- 每个模块 250 行上限 — handoff.py 目前 180 行，留有空间。

## Goals / Non-Goals — 目标 / 非目标

**目标：**

- 使现有的 `handoff.py` 可从执行路径到达，以便当 LLM 调用 `handoff_to_agent` 时实际产生 `Command(goto=...)`。
- 支持三种上下文传递策略（`inherited`、`isolated`、`summarized`）用于下游 Agent。
- 在 handoff 间维护有效的对话历史记录（AIMessage + ToolMessage 配对），使下游 LLM 不会中断。
- 保持向后兼容：没有编写 `handoff.context_mode` 的图继续使用默认的 `inherited`。
- 保持 `engine/` 层不包含 `services/` 导入。

**非目标：**

- **没有新的路由原语。** DSL 已支持 `trigger="handoff"` 和 `trigger="dynamic_handoff"`；我们在接入，不是重新设计。
- **本次变更中没有 `OnHandoffHook`。** 提案将其列为可选项；推迟到后续变更以保持焦点。
- **没有对 CONVERSATION 节点（LLMWorker）的 handoff。** 范围仅限于 AGENT 节点。AGENT 节点 handoff 验证后，LLMWorker handoff 可以跟进。
- **没有新的 UI 工作。** React Flow 画布已支持 `DynamicHandoffEdge`。`context_mode` 的视觉编辑是单独的变更。
- **没有分布式 handoff。** 跨进程或跨租户 handoff 不在范围内；此变更保持在单个 Pregel 运行时实例内。
- **没有工具调用并行性变化。** Bedrock 风格的并行协作者执行不在范围内；handoff 保持顺序。

## Decisions — 决策

### 决策 1：端口驱动的 handoff（探索阶段的选项 B）

**选择：** `AgentExecutionPort.agent_execute()` 拥有 handoff 生命周期（注入工具、检测调用、在结果字典中返回 `handoff_to`）。`AgentWorker` 将 `handoff_to` 转换为 `WorkerResult(command=Command(goto=...))`。

**考虑的替代方案：**

- **选项 A — 将 `handoff.py` 移到 `engine/`** 以便 `AgentWorker` 可以直接调用。拒绝：违反分层规则（引擎无依赖）并且为本质上是编排关注点的事项增加了引擎表面积。
- **选项 C — 端口注入，Worker 检测。** 拒绝：将 handoff 知识分散到两层；端口知道准备，Worker 知道路由语义。更难独立测试。

**理由：** 保持引擎纯净（项目硬性规定），将 handoff 逻辑集中在 `services/orchestration/` 中，现有模块已在那里。唯一的契约变更是端口结果字典中的一个可选键（`handoff_to`）。

### 决策 2：通过 `execution_context` 传递 `handoff_targets`

**选择：** `PregelRuntime._dispatch_node()`（或等效的 Worker 分发点）检查出边，并在调用 Worker 之前用 `{node_id, description}` 字典列表填充 `execution_context["handoff_targets"]`。

**考虑的替代方案：**

- **将完整的 CompiledGraph 传递给 Worker。** 拒绝：Worker 不需要整个图；它们需要一个切片。更大的 API 表面，更难审计。
- **让端口直接查询图。** 拒绝：端口是服务层，已经承担了太多职责；它不应该遍历图的边。

**理由：** 最小的 API 变更（`execution_context` 中的一个字典键），将图内部保持在运行时中，给 Worker 它们确切需要的内容。

### 决策 3：三个 `context_mode` 值，存储在节点配置中

**选择：** 在 `graph-dsl.schema.json` 的 AGENT 节点配置中添加可选的 `handoff` 对象：

```json
{
  "handoff": {
    "context_mode": "inherited" | "isolated" | "summarized",
    "description": "optional per-node handoff description override"
  }
}
```

行为：

- **`inherited`**（默认）：下游 Agent 按原样接收完整的 `messages` 通道。匹配 OpenAI Swarm 默认值。
- **`isolated`**：下游 Agent 只从触发用户消息和合成的系统通知（"Handed off from {source_agent} for {reason}"）开始。匹配 Claude Code 子 Agent。
- **`summarized`**：上游消息被折叠成一个 `system` 消息，包含结构化摘要（`from`、`intent`、`key_facts`、`open_questions`）。匹配 OpenAI `nest_handoff_history`。

实现：`handoff.py` 中的 `filter_messages_for_handoff(messages, context_mode, source_node_id, target_node_id)` 辅助函数。AgentWorker 在构建 handoff WorkerResult 的 `channel_updates` 时应用过滤器。

**考虑的替代方案：**

- **每个边的 `context_mode`**（来自同一源的不同目标使用不同模式）。拒绝：组合爆炸，边际价值低，没有客户需求。如果需要，以后可以在边配置上添加。
- **Python 中的自由格式过滤函数。** 拒绝：在 JSON DSL 中不可序列化，破坏 spec/解析器契约。

### 决策 4：handoff 上的 AIMessage + ToolMessage 配对

**选择：** 当 `AgentWorker` 检测到 handoff 时，生成的 `WorkerResult.channel_updates["messages"]` 必须恰好包含：

1. 触发性的 `AIMessage`（LLM 的工具调用消息，以原始 tool_call_id 重新发出）。
2. 合成的 `ToolMessage`，`tool_call_id` 与 #1 匹配，内容为 `"Handed off to {target}"`。

没有其他消息。下游 Agent 的 LLM 提供者获得格式良好的对话。

**理由：** LangGraph 明确记录了这一要求。没有配对，下游提供者（尤其是 OpenAI）会返回 `400` 错误或为未配对的工具调用生成幻觉补全。当前的 `create_handoff_worker_result` 写入单个 `{"role": "assistant", "content": "Transferring to {target}..."}` — 这已损坏，需要在此次变更中修复。

### 决策 5：每个目标的 handoff 工具描述

**选择：** `build_handoff_tool_schema(target_node_ids, descriptions_by_target)` 接受可选的 `descriptions_by_target: dict[str, str]`。当提供时，工具描述包含每个目标的角色：`"Transfer to {target}: {description}"`。当不存在时，回退到当前通用描述。

描述来源：

1. 目标 AGENT 节点的 `handoff.description` 字段（如果在节点配置中设置）。
2. 目标 Agent 的 `AgentModel.description`（在端口调用时查询）。
3. 目标 AGENT 节点的 `name`（最坏情况）。

**理由：** OpenAI Agents SDK `handoff_description`、Google ADK `agent.description`、Salesforce Agentforce `go_to_X description` — 都趋同于每个目标的描述，因为 LLM 路由准确性依赖于此。通用的 "Transfer to another agent" 描述会导致错误路由。

### 决策 6：通过默认值实现向后兼容

**选择：** 在此次变更之前编写的图继续工作：

- 没有 `handoff.context_mode` 的 AGENT 节点 → 默认为 `inherited`。
- 没有 `handoff.description` 的 AGENT 节点 → 使用目标 Agent 在 AgentModel 中的 `description`。
- `execution_context` 中没有 `handoff_targets`（旧运行时）→ 端口不注入 handoff 工具，保持当前无操作行为。

**理由：** 强制性的，因为现有的 DSL + 辅助工具已经在生产中使用。没有 flag day。

## Risks / Trade-offs — 风险 / 权衡

- **[风险] `AgentExecutionPort` 变成巨型模块。** 目前 360 行；此变更添加约 80 行用于 handoff 注入 + 检测。→ **缓解：** 250 行上限；如果端口超过，在相同模块中提取 `HandoffExecutor` 类。在 PR 审查中关注此指标。
- **[风险] `summarized` context_mode 需要 LLM 调用来生成摘要。** 这带来额外的延迟 + 成本 + 潜在的故障点。→ **缓解：** 使用廉价模型（Haiku 级别）；通过消息哈希缓存摘要；失败时，回退到 `isolated` 模式并记录 WARNING。在设计说明中记录。
- **[风险] 工具调用 ID 来源。** 当 LLM 调用 `handoff_to_agent` 时，我们需要精确保留 `tool_call_id` 以便配对在下游工作。如果 LLM 提供者返回非唯一 ID（罕见但可能），配对会中断。→ **缓解：** 在冲突时生成 UUID 后缀；记录 WARNING；永远不要静默丢弃 ID。
- **[风险] 测试面积增长。** 需要新测试：handoff 注入、handoff 检测、三种 context_mode 变体、消息配对、向后兼容路径。→ **缓解：** 使用参数化测试；每个 context_mode 一个测试夹具；模拟 LLM 服务返回预设的工具调用。估计 8-12 个新测试函数。
- **[风险] 具有循环 handoff 边的图。** 已由 `_validate_handoff_edges()`（循环检测）强制执行 — 没有新风险。→ **缓解：** 不需要；现有的编译器检查已覆盖。
- **[权衡] 端口结果字典增加了另一个可选键（`handoff_to`）。** 轻微的契约扩展。→ **缓解：** 在 `AgentExecutionPort.agent_execute()` 的文档字符串中记录该键；如果字典持续膨胀，添加 `AgentExecutionResult` 数据类。
- **[权衡] `summarized` 模式引入非确定性。** 同一 handoff 的两次运行可能产生略有不同的摘要。→ **缓解：** 接受它（匹配行业行为）；在 spec 中记录；建议测试断言使用 `inherited`。
