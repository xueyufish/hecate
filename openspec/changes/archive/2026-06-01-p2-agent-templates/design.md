## Context — 背景

编排模板系统已存在，使用 `src/hecate/data/orchestration_templates/` 中的基于文件的 JSON 模板。它提供：
- `GET /api/orchestration-templates` — 列出并返回元数据
- `GET /api/orchestration-templates/{id}` — 完整的 Graph DSL JSON
- 模板加载一次并缓存
- 元数据包括：名称、描述、类别、节点/边数

Agent 模板遵循相同模式，但用于 Agent 配置而非工作流。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 基于文件的 JSON 模板（与编排模板相同模式）
- 5 个覆盖常见用例的内置模板
- 模板实例化通过现有 API 创建新 Agent
- Agent 创建页面上的前端模板选择器
- 显示配置摘要的模板预览

**非目标：**
- 用户创建的模板（推迟 — 需要数据库存储）
- 模板版本管理（推迟）
- 模板共享/导出（推迟）

## Decisions — 决策

### D1：基于文件的 JSON 存储（非数据库）

**决策**：将模板存储为 `src/hecate/data/agent_templates/` 中的 JSON 文件。

**理由**：与编排模板相同模式。内置模板不需要用户修改。简单，无需迁移。

### D2：通过现有 API 进行模板实例化

**决策**：`POST /api/agent-templates/{id}/instantiate` 返回模板配置，前端使用它预填充表单，然后通过现有 `POST /api/agents` 提交。

**理由**：重用现有的 Agent 创建逻辑、验证和 KB ID 验证。无需新的数据库操作。

### D3：模板 schema 镜像 AgentCreateSchema

**决策**：模板 JSON 结构与 `AgentCreateSchema` 字段匹配，加上元数据（名称、描述、类别、预览）。

**理由**：直接映射，实例化期间无需转换。

## Risks / Trade-offs — 风险 / 权衡

- **[无用户模板]** → 仅内置模板。缓解措施：以后很容易通过数据库存储扩展。
- **[模板中的 KB ID]** → 模板引用的 KB ID 可能不存在。缓解措施：实例化时验证 KB ID。
- **[模型可用性]** → 模板引用特定模型。缓解措施：前端在模型不可用时显示警告。
