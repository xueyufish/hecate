## Why — 动机

在画布上构建多 agent 工作流的用户目前必须逐个节点手动构建图谱拓扑、连接边、配置通道和设置节点类型 — 这个过程需要深入理解引擎的 Graph DSL。企业平台（Coze、CrewAI、Dify）提供基于模式的工作流创建，用户选择高级协作模式（Sequential、Parallel、Handoff、Broadcast、Negotiation、Debate），系统自动生成图谱结构。这大幅降低了多 agent 编排的门槛。后端已有 9 个模板构建器函数和 8 个 JSON 模板，但没有统一的模式词汇表、模式分类系统，也没有从模式选择生成图谱的 API。现在是合适的时机，因为画布 UI（React Flow）和模板自定义基础设施已从 p2-canvas-ui-enhancement 变更中就位。

## What Changes — 变更内容

- 向引擎添加 `CollaborationPattern` 枚举，包含 6 种模式类型：`SEQUENTIAL`、`PARALLEL`、`HANDOFF`、`BROADCAST`、`NEGOTIATION`、`DEBATE`
- 添加模式推断逻辑（`infer_pattern()`），分析 `GraphConfig` 或 `CompiledGraph` 以检测其遵循的模式
- 添加模式到图谱的构建器（`build_graph_from_pattern()`），从模式选择加配置参数（agent 数量、模型、提示等）生成 `GraphConfig`
- 添加 `GET /api/collaboration-patterns` 端点，返回 6 种模式定义（含描述、参数和预览元数据）
- 添加 `POST /api/collaboration-patterns/{pattern}/generate` 端点，接受模式参数并返回完整的 Graph DSL JSON
- 增强 `GET /api/orchestration-templates`，包含从模板结构推断的 `pattern_type` 字段
- 添加 2 个缺失的 JSON 模板：`data/orchestration_templates/` 中的 `negotiation.json` 和 `debate.json`
- 构建一个可从画布工具栏访问的前端模式选择器组件（6 种模式的卡片网格）
- 构建模式选择后的模式配置对话框，用于参数输入（agent 数量、模型、提示）
- 将模式生成的图谱与现有画布和模板自定义流程集成

## Capabilities — 能力

### 新能力
- `collaboration-pattern-engine`：后端模式词汇表（枚举）、从图谱结构进行模式推断以及模式到图谱的构建器。新模块 `engine/patterns.py` 加上用于模式列表和图谱生成的 API 端点。
- `pattern-selector-ui`：前端模式选择器卡片网格组件、模式配置对话框以及与画布工作流页面的集成。

### 修改的能力
- `orchestration-templates`：向模板列表 API 响应添加 `pattern_type` 字段，使用新的模式推断逻辑从模板图谱结构推断。
- `multi-agent-canvas`：在画布工具栏中与现有模板选择器并列添加模式选择器触发器；通过现有的 `dslToReactFlow()` 流程将模式生成的图谱集成到画布中。

## Impact — 影响范围

**后端（引擎层）**：
- 新文件：`src/hecate/engine/patterns.py`（模式枚举、推断、构建器）— 零外部依赖，与引擎层约束一致
- 修改：`src/hecate/api/management/orchestration_templates.py`（向响应添加 pattern_type）
- 新文件：`src/hecate/api/management/collaboration_patterns.py`（模式 API 端点）
- 新文件：`src/hecate/data/orchestration_templates/negotiation.json`、`debate.json`
- 核心引擎类型（types.py）、编译器或 Pregel 运行时无变更 — 模式生成 GraphConfig，后者已可消费

**前端（web）**：
- 新组件：`web/src/components/workflow/pattern-selector.tsx`（卡片网格）
- 新组件：`web/src/components/workflow/pattern-config-dialog.tsx`（参数表单）
- 修改：`web/src/app/(dashboard)/workflows/[id]/page.tsx`（工具栏集成）
- 无新依赖 — 使用现有 React Flow + UI 库

**API**：
- 新增：`GET /api/collaboration-patterns` — 列出模式
- 新增：`POST /api/collaboration-patterns/{pattern}/generate` — 从模式生成图谱
- 修改：`GET /api/orchestration-templates` — 向条目添加 `pattern_type` 字段
