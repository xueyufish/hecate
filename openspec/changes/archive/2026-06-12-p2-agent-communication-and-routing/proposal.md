## Why — 动机

Hecate 的多 agent 编排支持 6 种协作模式（2.7a）和丰富的画布 UI，但当前 agent 之间没有强制性的通道访问边界 — 任何 agent 都可以读或写任何通道。此外，agent 之间的路由仅限于静态条件表达式和 handoff 边。企业平台（Google ADK、AutoGen、华为 AgentArts）将基于意图的和动态 LLM 驱动的路由作为一等公民功能提供。没有通道访问控制和高级路由，多 agent 图谱无法表达现实的隔离边界或智能路由决策。

## What Changes — 变更内容

- **通道访问验证**：编译器强制执行每个节点的 `readable`/`writable` 通道配置与声明的图谱通道一致。运行时对未授权的通道访问发出警告。
- **广播模式 UX**：增强 ChannelSelector 以显示广播参与（TOPIC 通道选择加入）和每个 agent 的通道访问摘要。
- **基于意图的路由**：condition 节点上新增 `routing_mode: "intent"` — LLM 分类用户意图并通过可配置的 `intent_patterns` 路由到匹配目标。
- **动态路由**：condition 节点上新增 `routing_mode: "dynamic"` — LLM 在运行时从候选 agent 列表中选择下一个发言者，灵感来自 AutoGen 的 SelectorGroupChat。
- **动态 handoff 边**：新增边触发器 `"dynamic_handoff"`，LLM 在运行时决定 handoff 目标（灵感来自 Google ADK 的 transfer_to_agent）。

## Capabilities — 能力

### 新能力
- `channel-access-control`：每节点通道读/写访问边界的编译时验证和运行时执行
- `advanced-routing-modes`：扩展 condition 节点的基于意图和动态 LLM 驱动的路由模式

### 修改的能力
- `graph-dsl`：向 CONDITION 节点配置添加 `routing_mode` 和 `routing_config` 字段；添加 `"dynamic_handoff"` 边触发器
- `multi-agent-canvas`：增强的 ChannelSelector（带广播模式 UX）；condition 节点的路由模式配置面板
- `agent-handoff`：支持 LLM 在运行时选择目标的动态 handoff 边

## Impact — 影响范围

- **引擎**：`compiler.py` 获得通道访问验证通行和路由模式编译；`pregel.py` 获得运行时通道访问警告和通过 `EnginePort.llm_invoke()` 的动态路由求值
- **Graph DSL 模式**：`schemas/graph-dsl.schema.json` — 向 CONDITION 节点配置添加 `routing_mode`、`routing_config`、`intent_patterns`、`candidate_agents`；向边触发器枚举添加 `"dynamic_handoff"`
- **API**：图谱验证端点获得通道访问检查和路由配置验证
- **前端**：`channel-selector.tsx` 增强（带广播模式和访问摘要）；condition 节点配置中新增路由配置面板；`edge-type-selector.tsx` 获得动态 handoff 选项
- **测试**：通道访问验证、路由模式编译、动态路由求值的新测试文件
