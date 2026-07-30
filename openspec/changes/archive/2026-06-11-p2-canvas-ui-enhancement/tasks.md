## 1. Shared Infrastructure — 共享基础设施

- [x] 1.1 创建 `web/src/components/workflow/channel-selector.tsx` — 用于通道可读/可写分配的双列多选组件，从图谱状态读取可用通道，未声明通道时支持自由输入
- [x] 1.2 创建 `web/src/components/workflow/edge-type-selector.tsx` — 包含边类型选项（Default、Handoff、Conditional）、图标和条件标签输入的弹出层组件
- [x] 1.3 向 `web/src/lib/dsl-bridge.ts` 添加 `reactFlowToDsl()` — 代码库中已存在，无需变更

## 2. Agent Node Config Enhancement (1.1.14) — Agent 节点配置增强

- [x] 2.1 重写 `config-panel.tsx` 中的 agent 节点部分 — 将单一 `agent_ref` 文本输入替换为结构化表单：agent 选择器下拉列表（获取 `/api/agents`）、角色描述文本域（`system_prompt`）、调用模式单选（`direct`/`tool`）、通道选择器组件（来自 1.1）、模型覆盖文本输入
- [x] 2.2 将 agent 选择器下拉列表连接到 `/api/agents` API — 用 agent 名称填充下拉列表，选择时设置 `config.agent_ref`，编辑现有节点时预填充
- [x] 2.3 将通道选择器连接到图谱状态 — 从画布图谱数据读取 `state` 键，作为可用通道传递给 channel-selector 组件，将选择保存到 `config.channels.readable` 和 `config.channels.writable`

## 3. Typed Edge Visualization (1.1.16) — 类型化边可视化

- [x] 3.1 在 `canvas-area.tsx` 中创建 `ConditionalEdge` 自定义边组件 — 深琥珀色（#d97706）点线 Bezier 曲线，中点带标签
- [x] 3.2 在 `canvas-area.tsx` 中创建 `FanOutEdge` 自定义边组件 — 靛蓝色（#6366f1）实线，起点处带分支图标指示器
- [x] 3.3 更新 `canvas-area.tsx` 中的 `edgeTypes` 注册表 — 将 ConditionalEdge 和 FanOutEdge 与现有 HandoffEdge 一同注册
- [x] 3.4 更新 `canvas-area.tsx` 中的 `typedEdges` 映射 — 扩展边分类逻辑以检测 conditional（`data.edgeType === "conditional"`）和 fan-out（源节点为 fan-out 类型）边
- [x] 3.5 将边类型选择器集成到 `handleConnect` 中 — 连接时显示 `EdgeTypeSelector` 弹出层，使用选中的 `data.edgeType` 创建边，保留 handoff 手柄快捷方式和 fan-out 自动检测
- [x] 3.6 添加边点击处理器 — 点击边时，显示包含边类型选项的上下文菜单，选择后更新边类型和视觉样式

## 4. Fan-Out/Merge Node Editing (1.1.17) — Fan-Out/Merge 节点编辑

- [x] 4.1 向 `node-palette.tsx` 的 PALETTE_ITEMS 数组添加 fan-out 和 merge 项 — Fan Out（GitFork 图标，靛蓝色）和 Merge（GitMerge 图标，石板色）
- [x] 4.2 向 `config-panel.tsx` 添加 fan-out 配置部分 — 分支数量选择器（2-6）、已连接分支目标列表（从边自动同步）、无分支连接时的视觉警告
- [x] 4.3 向 `config-panel.tsx` 添加 merge 配置部分 — fan-out 源下拉列表（列出画布上所有 fan-out 节点）、输出通道文本输入、未链接 fan-out 源时的视觉警告
- [x] 4.4 为 `node-types.tsx` 中的 `FanOutNode` 添加分支数量徽章 — 显示 "×N" 徽章，表示已连接的分支边数量

## 5. Template Customization (1.1.15) — 模板自定义

- [x] 5.1 向画布存储添加自定义模式状态 — `isCustomizing` 标志、`customizingFrom` 模板名称、工具栏指示器显示 "Customizing: {name}"
- [x] 5.2 更新 `TemplatePicker` 以设置自定义模式 — `onSelect` 后，设置 `isCustomizing=true` 和 `customizingFrom` 为模板名称，启用画布编辑
- [x] 5.3 向工具栏添加"另存为工作流"按钮 — 仅在自定义模式下显示，打开名称对话框，调用 `reactFlowToDsl()` 将画布转换为 DSL，通过工作流创建 API 保存
- [x] 5.4 验证原始模板未被修改 — 确保自定义后模板选择器仍列出原始模板，无模板突变

## 6. Verification — 验证

- [ ] 6.1 手动测试：创建 agent 节点 → 配置面板显示所有 5 个字段 → 保存 → 重新加载 → 值已持久化
- [ ] 6.2 手动测试：加载模板 → 自定义 agent 角色 → 添加/删除节点 → 另存为工作流 → 原始模板不变
- [ ] 6.3 手动测试：连接节点 → 边类型选择器出现 → 选择每种类型 → 视觉样式正确
- [ ] 6.4 手动测试：从面板拖拽 fan-out/merge → 配置 → 连接边 → 分支数量自动同步
- [x] 6.5 运行 `ruff check src/ tests/ && ruff format --check src/ tests/ && mypy src/` — 后端未变更，验证无回归
