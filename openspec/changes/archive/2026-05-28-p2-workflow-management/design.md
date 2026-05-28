## Context

Hecate P1 实现了 Graph DSL 编译器（`graph_dsl.py` + `compiler.py`）和 Pregel 执行引擎，但没有 Workflow 的持久化管理。用户通过 API 直接传入 JSON DSL 来执行，无法保存、版本化、复用工作流定义。

P2 前端画布需要一个后端 Workflow 实体来持久化用户拖拽编排的结果。Workflow 管理是画布的数据基础。

**当前状态**：
- Graph DSL JSON 格式已定义（`schemas/graph-dsl.schema.json`）
- GraphCompiler 可将 JSON DSL 编译为 CompiledGraph
- AgentModel 已有 `workflow_id` 字段（外键预留）
- 无 WorkflowModel、无 Workflow API

## Goals / Non-Goals

**Goals:**

1. 提供 Workflow CRUD API（创建、读取、更新、删除、列表）
2. 提供 Workflow 版本管理（版本列表、版本详情、回滚）
3. 创建/更新时调用 GraphCompiler 验证 DSL 合法性
4. 支持 Agent 绑定 Workflow

**Non-Goals:**

1. 不实现前端画布（属于 P2 前端变更）
2. 不实现工作流执行（已有 Pregel 引擎，通过 workflow_id 触发）
3. 不实现工作流模板市场（属于 P4）
4. 不实现 NL2Workflow（属于 P4）

## Decisions

### D1: 两个表——WorkflowModel + WorkflowVersionModel

**选择**：Workflow 存储拆分为两个表

```
workflows: id, name, workspace_id, current_version, created_at, updated_at, deleted_at
workflow_versions: id, workflow_id, version, graph_dsl(JSONB), compiled_graph(JSONB), change_summary, created_at
```

**理由**：
- Workflow 基本信息（name）和版本历史分离
- 列表查询只读 workflows 表，不需要加载所有版本
- 版本不可变——一旦创建，graph_dsl 不可修改（只能创建新版本）
- 与 architecture.md 中 ResourceVersion 设计一致

**备选方案**：
- ❌ 单表 + version 字段：每次更新覆盖旧数据，无法版本对比
- ❌ 单表 + JSONB 数组版本：查询复杂，无法高效分页

### D2: 创建时自动编译并缓存 CompiledGraph

**选择**：创建/更新 Workflow 时，调用 GraphCompiler 编译并存储 compiled_graph 到 workflow_versions 表

**理由**：
- 执行时直接加载 compiled_graph，不需要重复编译
- 编译错误在创建时就暴露，不会到执行时才失败
- compiled_graph 是 JSON 序列化的，可存 JSONB

### D3: 版本号自增，支持 change_summary

**选择**：版本号由系统自增（1, 2, 3...），用户可填 change_summary 描述变更

**理由**：
- 避免版本冲突
- change_summary 帮助用户理解每个版本的变更
- 与 ResourceVersion 设计一致

### D4: 使用 Pydantic Schema 做输入验证 + GraphCompiler 做 DSL 验证

**选择**：API 输入用 Pydantic Schema 验证基本格式，再用 GraphCompiler 验证 DSL 语义

**理由**：
- Pydantic 验证 JSON 结构（字段类型、必填项）
- GraphCompiler 验证 DSL 语义（节点引用、边连接、可达性）
- 两层验证，错误信息清晰

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| compiled_graph JSONB 可能很大 | 设置大小限制（< 1MB），超大工作流用子图拆分 |
| 版本表增长快 | 设置版本数量限制（默认 100 版本/工作流） |
| 并发更新冲突 | 使用乐观锁（version 字段）+ 409 Conflict 响应 |
| GraphCompiler 验证耗时 | 异步编译，创建时返回 202 Accepted |
