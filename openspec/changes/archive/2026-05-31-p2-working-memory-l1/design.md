## Context — 背景

L1 工作内存在后端已完全实现：
- **Model**：`MemoryBlockModel`，包含 agent_id、label、content、position、limit
- **Service**：`WorkingMemoryService`，支持 CRUD + `inject_blocks()` + `update_memory_block` 工具
- **API**：`POST/GET/PUT/DELETE /api/agents/{id}/memory-blocks`
- **Integration**：`ConversationService` 在每轮对话前加载块，传递给 `ContextAssembler`
- **Tests**：`tests/test_api/test_memory.py` 中有完整的 API 测试覆盖

然而，该功能**完全没有前端 UI**。如果不直接使用 API，用户无法查看、创建、编辑或删除内存块。Agent 配置器没有内存管理部分。聊天页面不显示哪些内存块处于活跃状态。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 在 Agent 配置器中添加 Memory 选项卡用于管理内存块
- 提供内存块内容的内联编辑
- 为常见块类型提供一键模板（persona、user_profile、domain_context、task_tracker）
- 在 Agent 详情页面显示活跃内存块
- 在聊天头部显示内存块指示器

**非目标：**
- 后端更改（所有 API 已存在）
- 内存块版本管理或历史
- 内存块在 Agent 间共享
- 内存分析或访问追踪 UI
- 内存块的实时协作编辑

## Decisions — 决策

### D1：内存选项卡在现有 AgentConfigurator 中（非独立页面）

**决策**：在现有 `AgentConfigurator` 组件的 Basic、Knowledge、Tools、Advanced 旁添加"Memory"选项卡。

**理由**：与现有 UX 模式一致。用户在 Agent 设置期间配置内存块。无需新页面导航。

**考虑过的替代方案**：
- 单独的 `/agents/[id]/memory` 页面 — 增加了导航复杂度，将 Agent 配置分散到多个页面
- 模态对话框 — 编辑多个块时空间有限

### D2：带保存/取消的内联编辑（非自动保存）

**决策**：内存块内容内联编辑，每个块带有显式的 Save/Cancel 按钮。

**理由**：内存块是重要的配置。自动保存可能导致意外覆盖。显式保存给用户控制权。

**考虑过的替代方案**：
- 失焦时自动保存 — 对重要配置有风险，无法撤销
- 编辑模式切换 — 增加 UX 复杂度

### D3：带预定义块类型的模板系统

**决策**：提供 4 个模板：persona、user_profile、domain_context、task_tracker。每个都有预定义的标签、建议内容、位置和限制。

**理由**：减少常见用例的使用摩擦。用户仍然可以创建自定义块。

**模板**：
| 模板 | 标签 | 内容提示 | 位置 | 限制 |
|----------|-------|--------------|----------|-------|
| Persona | `persona` | "You are a helpful assistant that..." | 0 | 2000 |
| User Profile | `user_profile` | "The user prefers..." | 1 | 1000 |
| Domain Context | `domain_context` | "This agent operates in the domain of..." | 2 | 2000 |
| Task Tracker | `task_tracker` | "Current task: ... Progress: ..." | 3 | 1500 |

### D4：聊天页面仅显示块标签（不显示内容）

**决策**：聊天页面头部将内存块标签显示为徽章，与 KB 指示器类似。不显示内容以避免杂乱。

**理由**：保持聊天 UI 整洁。用户可以看到哪些块活跃，而不被内容干扰。

## Risks / Trade-offs — 风险 / 权衡

- **[模板字段无后端验证]** — 模板仅为前端便利。后端已验证标签唯一性和字段约束。
- **[大内存块可能膨胀上下文]** — 每个块的 `limit` 字段已强制执行 token 限制。前端应突出显示限制。
- **[并发编辑的竞态条件]** — 多用户同时编辑同一 Agent 的块可能冲突。缓解措施：最后写入胜出（单用户模式可接受）。
