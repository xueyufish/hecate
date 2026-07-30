## Why — 动机

Agent 已经在数据库中以 JSON 列表形式存储了 `knowledge_base_ids`，RAG 流水线已经可以遍历多个 KB ID，Agent 配置器 UI 也已经提供了多选组件。然而，系统缺乏**端到端集成**：与 Agent 聊天时，前端未将 Agent 关联的 KB ID 传递给聊天端点，未验证引用的 KB ID 是否实际存在，删除 KB 会在 Agent 记录中留下过期引用。本次变更填补这些缺口，使多 KB 关联成为一个完全可用、生产就绪的功能。

## What Changes — 变更内容

- 在创建或更新 Agent 时添加 KB ID 验证 — 拒绝不存在或已软删除的 KB ID，返回清晰的 400 错误
- 添加级联清理 — 当知识库被软删除时，自动从所有 Agent 的 `knowledge_base_ids` 数组中移除其 ID
- 在聊天流程中自动加载 Agent 的 KB ID — 前端与 Agent 聊天时，获取并传递 Agent 的 `knowledge_base_ids` 到 `/v1/chat/completions` 端点
- 在聊天 UI 中显示活跃的 KB 指示器 — 展示对话期间哪些知识库正在被用于上下文
- 添加反向查找 API 端点 — `GET /api/knowledge-bases/{id}/agents` 用于查找哪些 Agent 使用了特定 KB
- 改进跨 KB 搜索结果排序 — 跨所有 KB 聚合结果并使用全局分数排序，而非每个 KB 取 Top-N 后再合并

## Capabilities — 能力

### New Capabilities — 新增能力
- `multi-kb-association`：Agent 的端到端多 KB 支持 — 验证、级联清理、聊天中自动加载、反向查找和跨 KB 结果排序

### Modified Capabilities — 修改的能力
- `citation-display`：需求变更 — 通过关联了 KB 的 Agent 聊天时，应自动生成引用（不仅限于显式传递 `kb_ids` 时）
- `agent-configurator`：需求变更 — Agent 配置器应显示 KB 验证错误，并在选择器中显示 KB 状态（活跃/已删除）

## Impact — 影响范围

- **后端模型**：`AgentModel`（无模式变更，保留 `knowledge_base_ids` JSON 列）
- **后端 API**：`agents.py`（创建/更新时的验证）、`knowledge.py`（删除时的级联清理、新增反向查找端点）、`chat.py`（无需变更 — 已支持 `kb_ids`）
- **后端服务**：`conversation.py`（无需变更 — 已支持遍历多个 KB）、Agent 服务中新增验证辅助方法
- **前端**：聊天页面（`chat/[conversationId]/page.tsx`）— 加载 Agent 的 KB ID 并传递给聊天端点；显示活跃 KB 徽章
- **前端**：Agent 配置器 — 显示 KB 验证错误
- **测试**：Agent CRUD 验证测试、KB 级联清理测试、带自动加载 KB ID 的聊天集成测试
- **无需 Alembic 迁移** — 现有的 JSON 列对于 M:N 关系已足够
