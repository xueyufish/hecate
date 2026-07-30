## Why — 动机

工作流画布编辑器拥有完全构建好的 `ConfigPanel` 组件（6 种节点类型表单）和 `CanvasArea` 组件，但它们没有连接起来。用户可以将节点拖放到画布上，但无法配置任何节点属性（model、system_prompt、tool_name 等）。此外，扇出/合并节点类型在后台 DSL 模式中存在，但没有前端渲染，并且节点布局位置在每次页面重新加载时丢失，因为 DSL 不持久化视觉坐标。

## What Changes — 变更内容

- 将 `ConfigPanel` 集成到工作流编辑器的右侧面板中，当在画布上选中节点时激活
- 向 `CanvasArea` 添加 `onNodeClick` 回调，向 `page.tsx` 添加 `selectedNodeId` 状态
- 右侧面板宽度统一为 300px，选中节点时显示 ConfigPanel，未选中时显示占位文本
- 测试运行结果保持在底部面板（右侧面板无模式切换）
- 将节点布局位置持久化到 localStorage（以 workflowId 为键），独立于语义 DSL
- 向 `NodeTypeSchema`、`node-types.tsx` 和 `dsl-bridge.ts` 添加扇出和合并节点类型作为仅视觉支持（无调色板入口，无配置表单）——完整创建/编辑推迟到 P3
- 在 DSL 加载时从 localStorage 恢复布局，对新工作流回退到自动网格布局

## Capabilities — 能力变更

### 新增能力

- `node-config-panel`: 当在画布上选中节点时，右侧面板集成用于编辑节点属性
- `node-layout-persistence`: 在 localStorage 中持久化和恢复 ReactFlow 节点位置，独立于语义 DSL
- `fan-out-merge-visual`: 扇出和合并节点类型的仅视觉渲染支持（无创建/编辑）

### 修改的能力

## Impact — 影响范围

- **前端文件**: `page.tsx`（编辑器页面）、`canvas-area.tsx`（添加 onNodeClick）、`node-types.tsx`（添加 FanOutNode/MergeNode）、`dsl-bridge.ts`（添加标签）、`workflow-types.ts`（添加枚举值）
- **无后端变更**: 所有变更仅限前端；DSL 模式和 API 端点不变
- **无破坏性变更**: 现有工作流加载和渲染方式相同；新的 localStorage 键是附加性的