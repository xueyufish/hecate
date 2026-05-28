## Why

P1 实现了 Graph DSL 编译器和 Pregel 执行引擎，但没有 Workflow 的 CRUD 管理——用户无法创建、保存、版本化自己的工作流。P2 前端画布需要一个后端 Workflow 实体来持久化用户拖拽编排的结果。这是画布的数据基础。

## What Changes

- **新增 `WorkflowModel` ORM 模型**：持久化工作流定义（nodes、edges、state_schema、entry_node）
- **新增 Workflow CRUD API**：`/api/workflows` 端点，支持创建、读取、更新、删除、列表
- **新增 Workflow 版本管理**：每次保存创建新版本，支持版本列表、版本对比、回滚到指定版本
- **新增 Workflow 验证**：创建/更新时调用 GraphCompiler 验证 DSL 合法性
- **新增 Workflow 与 Agent 关联**：Agent 的 `workflow_id` 字段可绑定工作流

## Capabilities

### New Capabilities

- `workflow-crud`: Workflow 的创建、读取、更新、删除、列表 API，包含 Graph DSL 验证
- `workflow-versioning`: Workflow 版本管理——版本列表、版本详情、版本对比、回滚

### Modified Capabilities

（无——所有变更都是新增模块，不修改现有 spec 行为）

## Impact

- **新增代码**：`models/workflow.py`、`api/management/workflows.py`、`services/workflow_service.py`，约 600-800 行
- **新增数据模型**：`WorkflowModel` + `WorkflowVersionModel`，Alembic migration
- **新增 API**：`/api/workflows` CRUD + `/api/workflows/{id}/versions` 版本管理
- **修改代码**：`alembic/env.py`（导入新模型）
- **新增依赖**：无（复用已有 GraphCompiler）
- **零破坏性变更**：P1 的所有 API 接口、数据模型、引擎行为保持不变
