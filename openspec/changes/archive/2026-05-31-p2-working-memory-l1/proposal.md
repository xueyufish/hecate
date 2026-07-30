## Why — 动机

L1 工作内存在**后端已完全实现** — MemoryBlockModel、WorkingMemoryService、API 端点和测试都已存在。然而，该功能对用户不可见，因为没有**前端 UI** 来管理内存块，没有与 Agent 配置器集成，也没有常见用例的模板。用户必须直接使用 API 来创建和管理内存块，这违背了低代码平台的目的。

本次变更填补前端差距，使 L1 工作内存成为面向用户的功能。

## What Changes — 变更内容

- 在 Agent 配置器中添加**内存选项卡**，显示 Agent 的内存块并支持内联编辑
- 添加**内存块管理器**组件，用于创建、编辑和删除内存块
- 添加**内存块模板**（persona、user_profile、domain_context、task_tracker），用户一键添加
- 在 **Agent 详情页面**显示活跃内存块，带快速编辑功能
- 在**聊天页面**添加内存块指示器，显示哪些块处于活跃状态

## Capabilities — 能力

### New Capabilities — 新增能力
- `memory-block-management`：L1 工作内存块 CRUD 的前端 UI — Agent 配置器集成、内联编辑、模板和聊天页面指示器

### Modified Capabilities — 修改的能力
- `agent-configurator`：添加 Memory 选项卡，用于管理 Agent 的内存块，带模板支持
- `session-memory`：添加在聊天中前端显示活跃内存块的需求

## Impact — 影响范围

- **仅前端** — 无需后端更改（所有 API 已存在）
- `web/src/components/agent/` — 新的 memory-block-editor 组件
- `web/src/components/agent/agent-configurator.tsx` — 添加 Memory 选项卡
- `web/src/app/(dashboard)/agents/[id]/page.tsx` — 显示内存块部分
- `web/src/app/(dashboard)/chat/[conversationId]/page.tsx` — 显示活跃内存块指示器
- **Tests**：内存块编辑器的 Vitest 组件测试
