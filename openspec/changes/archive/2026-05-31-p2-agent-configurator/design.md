## Context — 背景

Hecate 的 Agent Model 定义在 `src/hecate/models/agent.py` 中，包含字段：`id, name, description, model_name, persona, system_prompt, temperature, max_tokens, knowledge_base_ids, tool_ids, metadata_, is_deleted, created_at, updated_at`。Agent CRUD API 在 `src/hecate/api/agents.py` 中提供——`POST /api/agents` 和 `GET /api/agents` 等存在。

目前，创建 agent 需要发送 HTTP POST 请求到 API。无 UI。P2 画布工作添加了 AGENT 节点类型，但 agent 在节点创建前须已存在。Agent 配置器弥合了这一差距——提供用于创建和管理 agent 的前端界面。

## Goals / Non-Goals — 目标 / 非目标

**目标:**
- G1: Agent 配置表单——用于创建和编辑 Agent 的专用页面，包含 persona/模型/工具/知识库字段
- G2: 画布集成——从 AGENT 节点上下文菜单编辑 agent 配置
- G3: Agent 复制——从现有 agent 复制配置用于新 agent
- G4: 内联测试面板——在配置界面中发送测试消息并查看 agent 响应
- G5: 表单验证——提交前的必填字段和字段格式验证

**非目标:**
- Streaming agent 测试响应——P3，使用现有的 SSE 基础设施
- Agent 版本控制——P4
- Agent 配置导入/导出——P3
- 分步 agent 创建向导——P3
- 批量 agent 操作——P4

## Decisions — 决策

### D1: 独立配置页面 + 画布链接

**决策**: Agent 配置器实现为独立路由页面（`/agents/new`, `/agents/{id}/edit`），画布中的 AGENT 节点有上下文菜单项链接到这些页面。与画布内联（模态框中的表单）相比，这提供了更多空间。

**理由**: Agent 表单有许多字段（名称、描述、persona、模型配置、工具列表、知识库列表）。内联模态框对表单输入较多时不太理想。画布内编辑会强制将 agent 创建绑定到工作流编辑上下文。

**考虑的替代方案**: 画布中的 Agent 配置模态框——由于字段数量多而被拒绝。

### D2: 内联测试面板在同一页面

**决策**: 测试面板位于 agent 配置页面内联，位于表单下方或侧边。它是一个简单的 non-streaming 聊天界面——发送消息，接收 agent 响应。

**理由**: 开发者需要测试 agent 配置而不离开页面。单独路由会增加摩擦。内联面板利用现有的 `POST /api/conversations` + agent 路由。

**考虑的替代方案**: 单独的测试页面——由于工作流额外步骤而被拒绝。

### D3: PATCH API 用于部分更新

**决策**: 向 `AgentController` 添加 `PATCH /api/agents/{id}` 用于部分更新。前端发送变更的字段而非整个 agent 对象。

**理由**: 表单保存应仅发送变更的字段。P1 API 仅支持完整的 `PUT` 风格更新（CreateSchema → MergeSchema）。PATCH 更符合 RESTful 实践。

**影响的端点**:
- `PATCH /api/agents/{id}` — 部分更新 agent 字段
- `POST /api/agents` — 已存在（创建），需增强以返回完整的 agent 响应

### D4: 表单布局

**决策**: 布局分为 3 个逻辑部分——
1. **基本信息**: 名称、描述、persona（system prompt 文本区域）
2. **模型配置**: 模型名称（带类型提示的输入 + 预设建议下拉列表）、温度滑块、最大 token 数
3. **工具和知识库**: 可用工具的可搜索复选框列表，可用知识库的可搜索复选框列表

**理由**: 逻辑分组使复杂表单更易使用。3 部分涵盖所有 `AgentModel` DB 字段，无多余字段。

## Risks / Trade-offs — 风险与权衡

- **[风险] 模型名称验证**: 用户可能输入无效的模型名称。→ 缓解措施：输入字段提供常见模型建议的下拉列表（gpt-4o, claude-3.5-sonnet 等），并验证名称模式。
- **[权衡] 非 streaming 测试面板**: 测试使用 blocking 请求而非 streaming。→ 对于"快速测试"用例可接受，节省 SSE 基础设施的复杂性。
