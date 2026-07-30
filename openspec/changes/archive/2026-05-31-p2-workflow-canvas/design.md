## Context — 背景

Hecate 的执行引擎已完成：Graph DSL JSON Schema 定义了 4 种节点类型（conversation、tool-call、condition、agent）和 4 种通道类型；`compiler.py` 将 DSL 编译为验证后的图；`PregelRuntime` 使用通道、检查点和中断支持执行它。WorkflowModel + WorkflowVersionModel ORM 模型存在并支持版本管理。现有的工作流 CRUD API（`api/management/workflows.py`）处理基本的创建/更新/删除，但没有前端。

差距：用户必须编写原始 JSON DSL 才能创建工作流。没有可视化编辑器，没有在不理解 JSON Schema 的情况下配置节点的方式，也没有在不部署 Agent 的情况下测试工作流的方式。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 提供基于 React Flow 的可视化 DAG 编辑器，用户从面板拖拽节点，用边连接，通过侧面板配置每个节点
- 支持所有 4 种现有 DSL 节点类型 + 2 种新工具类型（knowledge-retrieval、variable-set）
- 双向转换：可视化状态 ↔ Graph DSL JSON，用户可通过可视化或 JSON 编辑
- 测试运行：使用样本输入执行工作流并显示每个节点的执行状态
- 集成到现有仪表板（侧边栏入口、一致样式）

**非目标：**
- 实时协作编辑（目前仅单用户）
- 用户创建自定义节点类型（仅内置类型）
- 画布中的子工作流嵌套（子图节点按 ID 引用现有工作流）
- 超越浏览器默认的撤销/重做
- 移动端响应式画布（仅桌面）
- 工作流调度 / cron 触发器（P3）
- 版本差异可视化（仅列出版本）

## Decisions — 决策

### D1：React Flow v12（`@xyflow/react`）作为画布库

**选择**：`@xyflow/react`（React Flow v12）
**考虑过的替代方案**：elkjs（仅布局）、dagre（仅布局）、draw2d（基于 Canvas）、自定义 Canvas/SVG
**理由**：React Flow 是最成熟的 React DAG 库（25k+ GitHub 星标），提供现成的拖拽/缩放/平移/小地图/自定义节点和边。被 Langflow、Coze 和 Dify 用于类似用例。v12 对 Next.js App Router 有一流支持。

### D2：客户端 DSL 序列化

**选择**：Graph DSL ↔ React Flow 转换完全在浏览器中进行
**考虑过的替代方案**：服务端转换端点
**理由**：DSL schema 简单（nodes dict + edges array）。客户端转换避免了网络往返，支持即时验证反馈，并保持画布响应式。现有的 `graph-dsl.schema.json` 可与 `zod` 一起用于验证。

### D3：工作流 API 扩展现有端点

**选择**：向现有工作流 API 添加测试运行和验证端点
**考虑过的替代方案**：独立的工作流执行服务
**理由**：现有的 `api/management/workflows.py` 已有 CRUD。我们添加 `POST /api/workflows/{id}/validate`（dry-run 编译）、`POST /api/workflows/{id}/test-run`（用输入执行）和 `GET /api/workflows/{id}/runs`（列出运行）。不需要新的服务层 — 通过 EnginePort 直接调用 Pregel runtime。

### D4：节点配置通过侧面板，而非内联

**选择**：点击节点 → 右侧面板打开，包含该节点配置的表单
**考虑过的替代方案**：内联编辑（双击编辑）、模态对话框
**理由**：侧面板为复杂配置（模型选择器、工具选择器、提示编辑器）提供更多空间，不遮挡画布，且与 Coze/Dify 处理节点配置的方式一致。

### D5：通过配置而非引擎更改添加新节点类型

**选择**：通过扩展 JSON Schema 并在引擎的工作线程分发中添加处理程序，将 `knowledge-retrieval` 和 `variable-set` 添加为新的 DSL 节点类型
**理由**：引擎已按 `node.type` 分发。添加新类型是干净的扩展。知识检索包装现有的 RAG 流水线。变量设置写入通道而不调用 LLM。

## Risks / Trade-offs — 风险 / 权衡

- **[React Flow 包大小约 200KB]** — 对仅桌面的仪表板可接受。使用动态导入（`next/dynamic`）避免影响初始页面加载。
- **[Graph DSL schema 演化]** — 添加新节点类型需要更新 `graph-dsl.schema.json`。编译器和运行时必须优雅处理未知节点类型（记录警告、跳过），以便旧工作流仍可执行。
- **[测试运行资源消耗]** — 在测试模式下使用真实 LLM 调用执行工作流可能花费较高。缓解措施：测试运行使用可配置的 mock 模式，记录提示而不调用 LLM；真实模式需要显式 opt-in。
- **[50+ 节点时的画布性能]** — React Flow 可以良好处理最多约 500 个节点。超过时需要虚拟化。对于 P2，50 节点限制是合理的。接近限制时记录警告。
