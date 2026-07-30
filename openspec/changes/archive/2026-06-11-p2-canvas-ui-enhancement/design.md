## Context — 背景

工作流画布编辑器（`web/src/components/workflow/`）是一个基于 React Flow 的可视化图谱编辑器。当前支持：

- **6 种节点类型**，通过 `node-types.tsx` 中的 `nodeTypeComponents` 渲染：conversation、condition、tool-call、agent、knowledge-retrieval、variable-set。Fan-out 和 merge 虽作为组件存在，但被排除在节点面板之外。
- **边类型**：仅 handoff 具有自定义渲染（紫色虚线）。所有其他边使用 React Flow 的默认 Bezier 曲线。
- **配置面板**（`config-panel.tsx`）：渲染每种节点类型的表单。Agent 节点仅暴露 `agent_ref` 文本输入框。
- **模板选择器**（`template-picker.tsx`）：从 `/api/orchestration-templates` 加载模板，以只读一次性方式填充画布。
- **节点面板**（`node-palette.tsx`）：6 个可拖拽项。不包含 fan-out 或 merge。
- **DSL 桥接**：`dsl-bridge.ts` 在 Graph DSL JSON 与 React Flow 节点/边数组之间转换。当前为单向（DSL → canvas）。

后端 Graph DSL 模式（`schemas/graph-dsl.schema.json`）已支持：agent `invocation_mode`（direct/tool）、`channels`（readable/writable）、fan-out `branches`、merge `fan_out_source`/`output_channel`、边 `type: "handoff"` 以及条件边目标（字典映射）。无需后端改动 — 这纯粹是前端增强。

## Goals / Non-Goals — 目标 / 非目标

**目标：**
- 提供与完整 Graph DSL 模式能力匹配的丰富 agent 节点配置
- 允许模板加载后进行自定义 — 编辑、修改、另存为新工作流
- 为 4 种不同的边类型提供可视化区分
- 支持交互式 fan-out/merge 节点创建和配置
- 所有更改仅限前端，无需后端 API 修改

**非目标：**
- 后端 Graph DSL 模式变更（已支持所有必要字段）
- 实时协作 / 多用户编辑
- 撤销/重做系统（可后续添加）
- 工作流版本历史（由现有 `workflow-version-publish` 规范覆盖）
- 画布自动布局算法（仅手动定位）
- 测试运行期间的执行可视化（已在 `multi-agent-canvas` 规范中）

## Decisions — 决策

### D1：配置面板采用按字段分区的布局与 API 驱动的选择器

**决策**：将 agent 节点单一的 `agent_ref` 文本输入替换为结构化表单，包含：（1）Agent 选择器（从 `/api/agents` 获取的下拉列表）、（2）角色描述（`system_prompt` 文本域）、（3）调用模式（单选：direct/tool）、（4）通道选择器（基于图谱 `state` 键的双列多选）、（5）模型覆盖（文本输入）。

**理由**：Graph DSL 已在 `node.config` 中支持所有这些字段。配置面板只需将其暴露即可。使用 API 驱动的选择器可确保用户选择有效的 agent ID 和通道名称，而非自由输入。

**备选方案**：保留自由文本输入并在保存时验证 — 已拒绝，因为下拉列表可防止错误且与 Coze/AgentArts 的 UX 一致。

### D2：通过编辑模式标志实现模板自定义，而非克隆后编辑

**决策**：加载模板后，在画布状态中设置 `isCustomizing` 标志。该标志启用所有画布编辑（添加/删除节点、编辑配置、调整连接）。"保存"操作使用修改后的图谱调用工作流创建 API。原始模板永不修改。

**理由**：模板是只读资产。自定义操作会创建从模板派生出的新工作流。这符合用户期望的"另存为"心智模型。

**备选方案**：克隆模板 JSON 然后编辑克隆 — 行为等效，但标志方法实现更简单，因为画布默认已在编辑模式；我们只需防止意外覆盖模板。

### D3：边类型选择器作为连接线弹出层

**决策**：当用户从源手柄拖动连接时，在鼠标附近显示一个小型弹出层，包含边类型选项：Default、Handoff、Conditional。选择决定边的 `data.edgeType` 和视觉样式。Fan-out 边在连接到 fan-out 节点时自动创建（无需手动选择）。

**理由**：React Flow 的 `onConnect` 事件在连接建立后触发。我们在最终确定前拦截它以显示类型选择器。这与 FigJam/Miro 连接类型选择的 UX 模式一致。

**备选方案**：在工具栏中预选边类型 — 已拒绝，因为需要模式切换，不如上下文选择直观。

### D4：Fan-out 和 merge 添加到节点面板并支持结构化配置

**决策**：将 fan-out 和 merge 添加到节点面板。拖放时，fan-out 节点提示输入分支数量（2-6）。通过从 fan-out 节点连接边来配置分支目标。Merge 节点需要链接到 fan-out 源并指定输出通道。

**理由**：当前的 `fan-out-merge-visual` 规范将它们排除在面板之外，因为它们"只能在从现有 DSL 加载时出现"。此变更推翻了该决定 — 实际工作流设计需要交互式创建。

**备选方案**：当用户拖入并行模式时自动插入 fan-out/merge 对 — 过于魔法，难以预测意图。逐个节点创建更可预测。

### D5：通道选择器从图谱状态声明中读取

**决策**：Agent 节点配置中的通道多选从图谱的 `state` 声明（Graph DSL 中的顶层 `state` 对象）读取可用通道。用户可以选择 agent 读取和写入的通道。

**理由**：通道在图谱级别定义，而非每个节点。选择器必须显示所有声明的通道，并让用户为每个 agent 的 readable/writable 列表选择子集。这与 `channels.readable` 和 `channels.writable` 引用顶层 state 键的 DSL 模式一致。

### D6：用于模板自定义的双向 DSL 桥接

**决策**：增强 `dsl-bridge.ts`，添加 `reactFlowToDsl()` 函数，将画布节点/边转换回 Graph DSL JSON。这使保存自定义工作流成为可能。

**理由**：当前仅存在 `dslToReactFlow()`（单向）。模板自定义需要将编辑后的画布状态转换回 DSL 以便保存。该函数必须处理：节点类型映射、配置提取、边类型重建以及从节点配置中发现状态通道。

## Risks / Trade-offs — 风险 / 权衡

**[边类型选择器 UX 复杂度]** → 连接时弹出层模式可能让人感觉陌生。缓解措施：保持弹出层简洁（3 个选项带图标），默认选择 "Default" 类型，并确保现有 handoff 手柄行为继续工作。

**[通道选择器依赖图谱状态]** → 如果未声明任何通道（新的空图谱），选择器显示为空。缓解措施：显示提示"在图谱设置中添加通道"，并允许自由输入通道名称作为后备方案。

**[Fan-out/merge 配置验证]** → 无效的 fan-out/merge 配置（例如 merge 没有 fan-out 源）将在编译时而非编辑时失败。缓解措施：当 fan-out 没有分支或 merge 没有源链接时，在配置面板中添加视觉警告。

**[模板自定义状态管理]** → 向画布状态添加 `isCustomizing` 标志和编辑跟踪。缓解措施：使用现有的 Zustand 存储模式 — 添加 `customizingFrom` 字段来跟踪模板来源。
