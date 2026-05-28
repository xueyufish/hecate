## Why

P1 的 Prompt 硬编码在 Agent 配置中，无法版本化、无法 A/B 测试、无法复用。P3 A/B 测试依赖 Prompt 版本管理作为前置条件。

## What Changes

- **新增 `PromptModel` ORM 模型**：持久化 Prompt 定义（name, template, variables, version, labels）
- **新增 `PromptVersionModel` ORM 模型**：版本历史（每次修改创建新版本）
- **新增 Prompt CRUD API**：`/api/prompts` 端点，支持创建、读取、更新、删除、列表
- **新增 Prompt 版本管理**：版本列表、版本对比、回滚到指定版本
- **新增模板引擎**：Jinja2 模板 + 变量插值，支持动态 Prompt 生成
- **新增标签部署**：production/staging/development 标签，支持环境隔离

## Capabilities

### New Capabilities

- `prompt-management`: Prompt CRUD + 版本管理 + 标签部署
- `template-engine`: Jinja2 模板引擎 + 变量插值

### Modified Capabilities

（无）

## Impact

- **新增代码**：`models/prompt.py`、`services/prompt_service.py`、`api/management/prompts.py`，约 600-800 行
- **新增数据模型**：`PromptModel` + `PromptVersionModel`，Alembic migration
- **新增 API**：`/api/prompts` CRUD + `/api/prompts/{id}/versions` 版本管理
- **新增依赖**：`jinja2`（模板引擎）
- **零破坏性变更**：P1 的硬编码 Prompt 继续工作
