## Why — 动机

画布工作流编辑器功能极为有限 — agent 节点仅暴露 `agent_ref` 文本字段，边只能区分 handoff 与默认，模板以只读一次性图谱形式加载，fan-out/merge 节点无法交互式创建或编辑。这些差距使得画布无法用于真实的多 agent 编排设计。竞品平台（Coze、AgentArts、Dify）提供了丰富的节点配置、可视化边类型、模板自定义和交互式分支编辑。弥合这些差距是关键 P2 里程碑。

## What Changes — 变更内容

- **Agent 节点配置增强 (1.1.14)**：将 agent 节点配置面板从 `agent_ref` 扩展到包含：角色描述、调用模式选择器（direct/tool）、可读/可写通道多选和模型覆盖。将当前纯文本输入替换为从 API 获取 agent 和从图谱 DSL 状态声明获取通道的结构化表单。
- **模板自定义 (1.1.15)**：加载编排模板后，允许用户编辑 agent 角色（system prompts）、添加/删除 agent 节点、调整连接、修改通道声明，并将修改后的图谱保存为新工作流。当前模板加载为一次性操作，无编辑能力。
- **类型化边可视化 (1.1.16)**：为 4 种边类型添加视觉区分：default（实线灰色）、handoff（紫色虚线 — 已存在）、conditional（带标签的点线）、fan-out（带分支指示器的多目标）。在连接交互中添加边类型选择器。当前仅 handoff 有自定义渲染。
- **Fan-Out/Merge 节点编辑 (1.1.17)**：允许用户从节点面板创建 fan-out 和 merge 节点，在 fan-out 节点中配置分支目标，将 merge 节点链接到其 fan-out 源，以及编辑输出通道。当前这些节点仅在从模板加载时出现，无法交互式创建或配置。

## Capabilities — 能力

### 新能力
- `agent-node-config`：丰富的 agent 节点配置面板，包含角色描述、调用模式、通道选择和模型覆盖
- `template-customization`：加载后编辑编排模板 — 修改 agent 角色、添加/删除节点、调整连接、另存为新工作流
- `typed-edge-visualization`：可视化边类型区分（default、handoff、conditional、fan-out），连接时带边类型选择器
- `fan-out-merge-editing`：在画布中交互式创建和配置 fan-out 及 merge 节点

### 修改的能力
- `node-config-panel`：Agent 节点部分从单个 `agent_ref` 字段扩展为完整结构化表单 — 保留现有配置面板结构，替换 agent 部分
- `fan-out-merge-visual`：Fan-out 和 merge 节点添加到节点面板以支持交互式创建 — 当前被排除在面板外，现在可拖拽并支持配置
- `multi-agent-canvas`：边类型选择从 handoff 扩展到包含 conditional 和 fan-out 类型 — 保留现有 handoff 渲染，新增类型并列显示

## Impact — 影响范围

**前端 (web/src/components/workflow/)**：
- `config-panel.tsx`：Agent 部分用结构化表单重写（agent 选择器、角色描述、调用模式、通道、模型覆盖）
- `canvas-area.tsx`：边渲染扩展 — 新增 conditional 和 fan-out 类型的边组件，连接时带边类型选择器
- `node-palette.tsx`：添加 fan-out 和 merge 为可拖拽项
- `node-types.tsx`：为 fan-out/merge 节点添加配置徽章（分支数量、源链接）
- `template-picker.tsx`：模板加载后添加"自定义"模式 — 启用画布编辑而非只读

**前端新文件**：
- `web/src/lib/dsl-bridge.ts` 增强：带编辑跟踪的模板到画布往返转换
- `web/src/components/workflow/edge-type-selector.tsx`：连接时边类型选择对话框
- `web/src/components/workflow/channel-selector.tsx`：通道读/写配置的多选组件

**后端**：最小变更 — 现有 Graph DSL 模式已支持所有节点类型、边类型、通道配置和调用模式。无需模式变更。API 可能需要工作流保存端点（如果不存在）。

**依赖**：无需新的 npm 包。使用现有的 React Flow 自定义边/节点 API、现有的 shadcn/ui 组件和现有的 API 客户端。
