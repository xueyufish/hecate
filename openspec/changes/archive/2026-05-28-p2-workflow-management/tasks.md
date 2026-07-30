## 1. 数据模型

- [x] 1.1 在 `models/workflow.py` 中创建 `WorkflowModel` ORM——字段：id, name, workspace_id, current_version, created_at, updated_at, deleted_at
- [x] 1.2 在 `models/workflow.py` 中创建 `WorkflowVersionModel` ORM——字段：id, workflow_id, version, graph_dsl(JSONB), compiled_graph(JSONB), change_summary, created_at
- [x] 1.3 创建 Pydantic schema：WorkflowCreateSchema, WorkflowUpdateSchema, WorkflowReadSchema, WorkflowVersionReadSchema
- [x] 1.4 为 workflow 和 workflow_versions 表生成 Alembic 迁移
- [x] 1.5 更新 `alembic/env.py` 导入 workflow 模型

## 2. 服务层

- [x] 2.1 创建 `services/workflow_service.py` 及 WorkflowService 类
- [x] 2.2 实现 `create_workflow(name, graph_dsl, workspace_id)`——使用 GraphCompiler 验证 DSL，创建 WorkflowModel + WorkflowVersionModel(v1)
- [x] 2.3 实现 `get_workflow(workflow_id)`——返回带有当前版本的 workflow
- [x] 2.4 实现 `update_workflow(workflow_id, name?, graph_dsl?)`——更新名称或使用 DSL 验证创建新版本
- [x] 2.5 实现 `delete_workflow(workflow_id)`——软删除
- [x] 2.6 实现 `list_workflows(workspace_id, page, page_size)`——分页列表（排除已删除）
- [x] 2.7 实现 `list_versions(workflow_id)`——按版本号排序的所有版本
- [x] 2.8 实现 `get_version(workflow_id, version)`——特定版本详情
- [x] 2.9 实现 `rollback_to_version(workflow_id, target_version)`——使用目标的 graph_dsl 创建新版本

## 3. API 层

- [x] 3.1 创建 `api/management/workflows.py` 及 CRUD 端点
- [x] 3.2 实现 `POST /api/workflows`——创建工作流
- [x] 3.3 实现 `GET /api/workflows/{id}`——读取工作流
- [x] 3.4 实现 `PUT /api/workflows/{id}`——更新工作流
- [x] 3.5 实现 `DELETE /api/workflows/{id}`——删除工作流
- [x] 3.6 实现 `GET /api/workflows`——带分页列出工作流
- [x] 3.7 实现 `GET /api/workflows/{id}/versions`——列出版本
- [x] 3.8 实现 `GET /api/workflows/{id}/versions/{version}`——获取特定版本
- [x] 3.9 实现 `POST /api/workflows/{id}/rollback/{version}`——回滚到版本
- [x] 3.10 在主 FastAPI 应用中注册 workflow 路由

## 4. 测试

- [x] 4.1 WorkflowModel 和 schema 的单元测试
- [x] 4.2 WorkflowService 的单元测试——创建、获取、更新、删除、列出
- [x] 4.3 WorkflowService 的单元测试——版本、回滚
- [x] 4.4 API 端点的集成测试——CRUD 操作
- [x] 4.5 API 端点的集成测试——版本管理
- [x] 4.6 测试无效 graph_dsl 的拒绝（422 响应）
