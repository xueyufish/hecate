## Why — 动机

Hecate 拥有功能完备的执行引擎（Graph DSL → Compiler → Pregel Runtime），但工作流只能通过编写原始 JSON DSL 来创建。用户需要一个可视化的拖拽画布来设计、配置和测试工作流 — 这是 P2 "好用"阶段的核心差异化功能。没有它，非开发人员无法使用引擎。

## What Changes — 变更内容

- **React Flow 画布编辑器** — 拖拽式 DAG 编辑器，用于可视化构建工作流图，包含节点面板、边绘制、缩放/平移、小地图
- **工作流节点类型库** — 6 种内置节点类型（LLM Call、Condition、Tool Call、Knowledge Retrieval、Sub-Agent、Variable Set），每种都带配置面板
- **工作流 CRUD API** — 用于工作流创建/读取/更新/删除/列表的 REST 端点，带版本管理（已有 WorkflowModel + WorkflowVersionModel ORM）
- **Graph DSL 序列化器** — React Flow 节点/边状态与现有 Graph DSL JSON Schema 之间的双向转换
- **工作流测试运行** — 能够用样本输入触发工作流执行，实时查看每个节点的执行状态和输出
- **前端工作流页面** — 工作流列表、画布编辑器、版本历史、测试运行面板，集成到仪表板侧边栏

## Capabilities — 能力

### New Capabilities — 新增能力

- `workflow-canvas`：基于 React Flow 的可视化拖拽 DAG 编辑器，带节点面板、边绘制和画布控制
- `workflow-node-types`：内置节点类型定义（LLM、Condition、Tool、Knowledge Retrieval、Sub-Agent、Variable），带配置面板
- `workflow-api`：用于工作流 CRUD、版本管理和测试执行的 REST API
- `workflow-dsl-bridge`：React Flow 可视化状态与 Graph DSL JSON 之间的双向转换
- `workflow-test-run`：使用样本输入触发工作流执行并查看每个节点的结果

### Modified Capabilities — 修改的能力

（无 — 所有现有规范针对 P1 引擎内部，保持不变）

## Impact — 影响范围

- **Frontend**：`web/src/app/(dashboard)/workflows/` 下的新页面，新增对 `reactflow`（或 `@xyflow/react`）的依赖
- **Backend**：新路由文件 `api/management/workflows.py`，扩展现有 `api/management/` 模式
- **Models**：重用现有 `WorkflowModel` + `WorkflowVersionModel`（无模式变更）
- **Engine**：无需更改 — 画布产生 Graph DSL，输入到现有的 `graph_dsl.parse_graph()` → `compiler.compile()` → `PregelRuntime`
- **Dependencies**：`@xyflow/react`（React Flow v12）、`zod` 用于前端 DSL 验证
