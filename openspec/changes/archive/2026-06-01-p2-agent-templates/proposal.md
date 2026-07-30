## Why — 动机

用户目前每次都要从头配置 Agent — 选择角色设定、模型、工具、技能和知识库。许多用例共享常见模式：客服 Agent、代码审查 Agent、研究助手。功能目录将此描述为"把配置好的 Agent 打包成可复用的场景方案"，参考 AgentArts。

本次变更添加 Agent 模板 — 预配置的 Agent 定义，用户可以一键实例化，然后按需自定义。

## What Changes — 变更内容

- 添加 **AgentTemplate 模型** — 定义可复用 Agent 配置的 JSON schema（角色设定、模型、工具、技能、KB、内存块）
- 添加 **Template API** — `GET /api/agent-templates` 列出、`GET /api/agent-templates/{id}` 获取详情
- 添加 **模板实例化** — `POST /api/agent-templates/{id}/instantiate` 从模板创建新 Agent
- 添加 **内置模板** — 5 个预配置模板（客服、代码审查、研究助手、内容写手、数据分析师）
- 添加 **前端模板选择器** — Agent 创建页面上的"From Template"按钮

## Capabilities — 能力

### New Capabilities — 新增能力
- `agent-templates`：Agent 模板系统，包含内置模板、API 和前端选择器

### Modified Capabilities — 修改的能力
- （无 — 新功能）

## Impact — 影响范围

- **Backend**：新的 `src/hecate/data/agent_templates/` 目录，包含 JSON 文件
- **Backend**：新的 `src/hecate/api/management/agent_templates.py` API
- **Frontend**：Agent 创建页面中的模板选择器组件
- **Tests**：API 端点测试
- **Pattern**：遵循现有编排模板模式（基于文件的 JSON，缓存）
