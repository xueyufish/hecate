## ADDED Requirements — 新增需求

### Requirement: Default edge type rendered as solid gray Bezier — 默认边类型渲染为实线灰色 Bezier 曲线
默认边（无类型或 `type: "default"`）应渲染为实线灰色 Bezier 曲线，与 React Flow 的默认渲染一致。

#### Scenario: Default edge visual — 默认边视觉样式
- **当** 边没有 `data.edgeType` 或 `data.edgeType` 为 "default"
- **则** 边应渲染为实线灰色（#94a3b8）Bezier 曲线，无标签

### Requirement: Handoff edge type rendered as dashed purple — Handoff 边类型渲染为紫色虚线
Handoff 边应渲染为带 "Handoff" 标签的紫色虚线 Bezier 曲线。这取代了现有的 handoff 渲染。

#### Scenario: Handoff edge visual — Handoff 边视觉样式
- **当** 边的 `data.edgeType` 设置为 "handoff"
- **则** 边应渲染为紫色虚线（#8b5cf6）Bezier 曲线，中点处带 "Handoff" 标签

### Requirement: Conditional edge type rendered as dotted with label — Conditional 边类型渲染为带标签的点线
Conditional 边应渲染为深琥珀色点线 Bezier 曲线，条件键作为标签显示。

#### Scenario: Conditional edge visual — Conditional 边视觉样式
- **当** 边的 `data.edgeType` 设置为 "conditional" 且 `data.label` 设置为 "finance"
- **则** 边应渲染为深琥珀色点线（#d97706）Bezier 曲线，中点处带 "finance" 标签

#### Scenario: Conditional edge without label — 无标签的 Conditional 边
- **当** 边的 `data.edgeType` 设置为 "conditional" 且无 `data.label`
- **则** 边应渲染为深琥珀色点线 Bezier 曲线，中点处带 "Condition" 标签

### Requirement: Fan-out edge type rendered with branch indicators — Fan-out 边类型渲染为带分支指示器
Fan-out 边应渲染为靛蓝色实线，带显示并行分支的小箭头指示器。

#### Scenario: Fan-out edge visual — Fan-out 边视觉样式
- **当** 边从 fan-out 节点出发
- **则** 边应渲染为靛蓝色实线（#6366f1），起点处带小分支图标

### Requirement: Edge type selector on connect — 连接时的边类型选择器
当用户在两个节点之间创建连接时，系统应呈现一个边类型选择器，选项包括：Default、Handoff、Conditional。

#### Scenario: Show edge type selector — 显示边类型选择器
- **当** 用户从节点 A 拖拽连接到节点 B
- **则** 连接点附近应出现弹出层，显示选项：Default（实线）、Handoff（虚线）、Conditional（点线）

#### Scenario: Select default edge type — 选择默认边类型
- **当** 用户在边类型选择器中选择 "Default"
- **则** 应以 `data.edgeType` 设置为 "default" 创建边

#### Scenario: Select handoff edge type — 选择 Handoff 边类型
- **当** 用户在边类型选择器中选择 "Handoff"
- **则** 应以 `data.edgeType` 设置为 "handoff" 创建边，并渲染为紫色虚线

#### Scenario: Select conditional edge type — 选择 Conditional 边类型
- **当** 用户在边类型选择器中选择 "Conditional"
- **则** 应以 `data.edgeType` 设置为 "conditional" 创建边，并提示用户输入条件标签

#### Scenario: Handoff handle shortcut preserved — 保留 Handoff 手柄快捷方式
- **当** 用户从 agent 节点的 "handoff" 源手柄连接
- **则** 边应自动创建为 handoff 类型，不显示选择器

#### Scenario: Connection to fan-out node auto-sets type — 连接到 Fan-out 节点自动设置类型
- **当** 用户连接任何节点到 fan-out 节点
- **则** 边应自动创建为 fan-out 边类型，不显示选择器

### Requirement: Edge type changeable after creation — 创建后可更改边类型
用户应能通过点击边并选择新类型来更改现有边的类型。

#### Scenario: Change edge type via context menu — 通过上下文菜单更改边类型
- **当** 用户点击现有边
- **则** 应出现一个包含边类型选项的上下文菜单：Default、Handoff、Conditional
- **则** 选择新类型应更新边的视觉样式和 `data.edgeType`
