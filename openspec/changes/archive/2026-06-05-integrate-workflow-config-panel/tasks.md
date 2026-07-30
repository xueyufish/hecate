## 1. 扇出/合并视觉支持

- [x] 1.1 在 `web/src/lib/workflow-types.ts` 的 `NodeTypeSchema` 枚举中添加 "fan-out" 和 "merge"
- [x] 1.2 在 `web/src/components/workflow/node-types.tsx` 中添加 FanOutNode 和 MergeNode 组件，具有不同的图标和颜色
- [x] 1.3 在 `web/src/lib/dsl-bridge.ts` 的 `NODE_TYPE_LABELS` 中添加 "fan-out" → "Fan Out" 和 "merge" → "Merge"
- [x] 1.4 在 `node-types.tsx` 的 `nodeTypeComponents` 映射中添加 FanOutNode 和 MergeNode 条目

## 2. 节点点击交互

- [x] 2.1 在 `web/src/components/workflow/canvas-area.tsx` 的 `CanvasAreaProps` 中添加 `onNodeClick` prop，并将其连接到 ReactFlow 的 `onNodeClick` 事件
- [x] 2.2 在 `page.tsx` 中添加 `selectedNodeId` 状态，并将 `onNodeClick` 处理函数传递给 CanvasArea
- [x] 2.3 通过 canvas-area.tsx 中的 ReactFlow `onPaneClick` 添加点击画布取消选择处理函数

## 3. ConfigPanel 集成

- [x] 3.1 在 `page.tsx` 中导入 ConfigPanel
- [x] 3.2 将右侧面板（w-[280px]）替换为 w-[300px] 动态面板：选中节点时显示 ConfigPanel，未选中时显示占位文本
- [x] 3.3 在 `page.tsx` 中实现 `handleConfigUpdate` 回调，更新节点数据并触发 `scheduleSave`
- [x] 3.4 从右侧面板移除或迁移 Input Form（保留为工具栏按钮或可折叠区域）

## 4. 布局持久化

- [x] 4.1 创建布局保存辅助函数：将节点位置序列化到 localStorage，键为 `hecate-layout-{workflowId}`
- [x] 4.2 将布局保存集成到 `page.tsx` 中现有的 `scheduleSave` 回调中
- [x] 4.3 修改 `dslToReactFlow` 或添加加载后合并步骤：如果 localStorage 中存在该 workflowId 的布局，则将保存的位置应用到节点；否则使用默认网格
- [ ] 4.4 测试往返：创建工作流、移动节点、重新加载页面、验证位置被恢复

## 5. 验证

- [x] 5.1 在 web/ 中运行 `npm run build` —— 零错误
- [x] 5.2 运行 `npm test` —— 所有测试通过
- [x] 5.3 运行 `ruff check src/hecate/ tests/` —— 预期无 Python 变更，验证干净
- [ ] 5.4 手动验证：打开工作流编辑器、选中节点、编辑配置、保存、重新加载、验证持久化