## 1. Models & Schemas — 模型与模式

- [x] 1.1 向 `WorkflowModel` 添加 `execution_mode` 字段（String(20)，默认值="conversational"，nullable=False），并更新 `WorkflowCreateSchema`、`WorkflowUpdateSchema`、`WorkflowReadSchema`、`WorkflowDetailSchema`
- [x] 1.2 向 `WorkflowModel` 添加 `published_version` 字段（Integer，nullable=True，默认值=None），并更新读取/详情模式
- [x] 1.3 向 `WorkflowVersionModel` 添加 `labels` 字段（JSON，默认值=list），并更新 `WorkflowVersionReadSchema`
- [x] 1.4 为三个新列添加 Alembic 迁移

## 2. Engine Layer — Execution Mode Validation — 引擎层 — 执行模式验证

- [x] 2.1 向 `GraphCompiler.compile()` 添加 `execution_mode` 参数 — 默认值 "conversational"，验证任务模式下禁止 INTERRUPT/SUGGESTION 节点
- [x] 2.2 向 `engine/types.py` 添加 `ExecutionMode` 枚举，值为 CONVERSATIONAL 和 TASK
- [x] 2.3 向通道初始化添加系统变量：`sys.execution_mode`、`sys.conversation_id`、`sys.dialogue_count`（仅限对话模式）

## 3. Engine Layer — Runtime Behavior — 引擎层 — 运行时行为

- [x] 3.1 向 `PregelRuntime.execute()` 添加 execution_mode 参数 — 在任务模式下禁用检查点，在任务模式下将 StreamMode.MESSAGES 覆盖为 StreamMode.VALUES
- [x] 3.2 更新 `WorkflowExecutionService` 以将 execution_mode 从 WorkflowModel 传递给 PregelRuntime

## 4. Service Layer — Workflow Mode & Version — 服务层 — 工作流模式与版本

- [x] 4.1 更新 `WorkflowService.create_workflow()` 以接受并持久化 execution_mode
- [x] 4.2 更新 `WorkflowService.update_workflow()` 以验证 execution_mode 变更，并在 graph_dsl 发生变化时重新编译
- [x] 4.3 实现 `WorkflowService.publish_version(workflow_id, version)` — 设置 published_version、管理 production 标签、创建审计日志
- [x] 4.4 实现 `WorkflowService.get_version_by_label(workflow_id, label)` — 按 labels 字段查询
- [x] 4.5 实现 `WorkflowService.get_published_version(workflow_id)` — 返回 published_version 指针指向的版本
- [x] 4.6 实现 `WorkflowService.diff_versions(workflow_id, v1, v2)` — 使用 deepdiff 进行结构化 JSON 差异比较，返回分类结果

## 5. API Layer — New Endpoints — API 层 — 新端点

- [x] 5.1 添加 `POST /api/workflows/{id}/publish/{version}` 端点
- [x] 5.2 添加 `GET /api/workflows/{id}/diff?v1=&v2=` 端点
- [x] 5.3 添加 `GET /api/workflows/{id}/published` 端点
- [x] 5.4 更新现有工作流 CRUD 端点，在响应中包含 execution_mode 和 published_version

## 6. Dependencies — 依赖

- [x] 6.1 将 `deepdiff` 添加到 pyproject.toml 中的 `[dev]` 可选依赖组

## 7. Tests — 测试

- [x] 7.1 测试 WorkflowModel execution_mode 字段：使用默认值创建、使用 task 创建、更新模式
- [x] 7.2 测试 GraphCompiler 任务模式验证：INTERRUPT 节点被拒绝、SUGGESTION 节点被拒绝、对话模式允许所有节点
- [x] 7.3 测试 PregelRuntime 任务模式行为：无检查点、流模式覆盖
- [x] 7.4 测试 WorkflowService.publish_version：发布、重新发布、发布不存在的版本
- [x] 7.5 测试 WorkflowService.diff_versions：节点变更、相同版本、不存在的版本
- [x] 7.6 测试工作流 API publish/diff/published 端点
- [x] 7.7 测试系统变量：两种模式下的 sys.execution_mode，sys.conversation_id 仅限对话模式
