## Why — 动机

Agent 配置目前在三个地方手动管理：1) `AgentModel` 关系数据库表，2) 工作流画布中的 AGENT 节点配置，3) 创建 agent 时无 UI 界面。用户每次创建或编辑 agent 时需要使用 SQL 或 API 客户端。P1 完成了基础的 Agent CRUD API（`src/hecate/api/agents.py`），但未提供面向用户的配置界面，也未将 Agent 配置集成到画布体验中。没有专用的配置工具，Agent 管理仍然是开发人员专属的体验。

P2 多 Agent 编排引入了 agent 调色板和画布集成；Agent 配置器是完成体验的必要前提——用户须能在画布 UI 中创建、编辑和配置 agent，然后立即将其拖入编排中。

## What Changes — 变更内容

- **Agent 配置 UI**: 用于创建和编辑 Agent 的专用表单页面，包含所有配置字段（名称、描述、persona、模型、温度、工具选择、知识库选择）
- **画布内联配置**: 从画布中的 AGENT 节点上下文菜单双击或点击"编辑"时，直接编辑 agent
- **Agent 测试面板**: 用于 non-streaming 对话测试的内联聊天界面——在配置界面中发送消息并查看 agent 响应，无需离开页面
- **配置验证**: 表单提交前的客户端验证（必填字段、模型名称验证、persona 最大长度）
- **Agent 复制**: 从现有 agent 复制配置以快速创建变体

## Capabilities — 能力

### New Capabilities — 新增能力
- `agent-configurator`: 面向用户的 Agent 配置表单——创建、编辑、验证、复制 agent，集成到画布中
- `agent-test-panel`: 用于 Agent 对话测试的内联聊天界面（non-streaming）

### Modified Capabilities — 修改的能力
- **Agent CRUD API**: 增强后端 `PATCH /api/agents/{id}` 以支持部分更新，确保 `GET /api/agents/{id}` 返回所有配置字段
- **画布 Agent 节点**: 从现有 AGENT 节点上下文菜单链接到 agent 配置器

## Impact — 影响

- **API**: 添加 `PATCH /api/agents/{id}` 用于部分更新。`GET /api/agents/{id}` 已存在。
- **前端**: 新路由 `/agents/new` 和 `/agents/{id}/edit`，用于 agent 配置的面包屑导航。
- **服务**: 可能对现有 AgentService 进行小更新以支持 PATCH，但主要变化在前端。
- **数据库**: 无 schema 变更。使用现有 AgentModel 表。
