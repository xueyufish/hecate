## Why

P2 的多 Agent 编排只支持 Graph 模板（层级委派、移交、流水线、广播），不支持动态任务分配、P2P 协商、事件驱动通信。P3 需要更灵活的 Agent 间通信机制。

## What Changes

- **新增 `AgentMessageBus`**：事件驱动消息总线，支持发布-订阅模式
- **新增 `DynamicTaskAllocator`**：基于 LLM 的动态任务分配，替代静态 Graph
- **新增 `P2PNegotiator`**：Agent-to-Agent 自主协商协议
- **扩展 `WorkflowService`**：支持动态节点插入和运行时路由

## Capabilities

### New Capabilities

- `agent-message-bus`: 事件驱动发布-订阅消息总线
- `dynamic-task-allocation`: LLM 驱动的动态任务分配
- `p2p-negotiation`: Agent-to-Agent 自主协商协议

### Modified Capabilities

（无）

## Impact

- **新增代码**：`services/multi_agent/` 目录，约 600-900 行
- **新增依赖**：无（复用已有基础设施）
- **修改代码**：无（纯新增，Graph 编排保留）
- **架构影响**：Graph 编排 + 动态分配双模式并存
