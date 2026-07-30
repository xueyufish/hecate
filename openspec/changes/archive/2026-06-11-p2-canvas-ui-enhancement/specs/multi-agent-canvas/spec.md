## MODIFIED Requirements — 修改的需求

### Requirement: Edge type differentiation in canvas — 画布中的边类型区分
系统应以 4 种不同的视觉类型渲染边：default（实线灰色）、handoff（紫色虚线）、conditional（深琥珀色点线带标签）和 fan-out（靛蓝色实线带分支指示器）。原有的 2 类型系统（handoff vs default）已扩展为包含 conditional 和 fan-out 边类型。

#### Scenario: Handoff edge rendered as dashed purple — Handoff 边渲染为紫色虚线
- **当** 图谱中包含一条 `data.edgeType` 设置为 "handoff" 的边，连接两个 agent 节点
- **则** 画布将该边渲染为带 "Handoff" 标签的紫色虚线 Bezier 曲线

#### Scenario: Default edge rendered as solid gray — Default 边渲染为实线灰色
- **当** 图谱中包含一条标准边（无 `data.edgeType` 或 `data.edgeType` 为 "default"）
- **则** 画布将该边渲染为实线灰色 Bezier 曲线

#### Scenario: Conditional edge rendered as dotted with label — Conditional 边渲染为带标签的点线
- **当** 图谱中包含一条 `data.edgeType` 设置为 "conditional" 的边
- **则** 画布将该边渲染为深琥珀色点线 Bezier 曲线，并以条件键作为标签

#### Scenario: Fan-out edge rendered with branch indicators — Fan-out 边渲染为带分支指示器
- **当** 边从 fan-out 节点出发
- **则** 画布将该边渲染为带分支图标指示器的靛蓝色实线

### Requirement: Edge type selection when connecting nodes — 连接节点时的边类型选择
系统应允许用户通过弹出层边类型选择器在连接两个节点时选择边类型，选项包括：Default、Handoff、Conditional。原有的 2 选项连接对话框已替换为支持 3 种以上类型的弹出层选择器。连接到 fan-out 节点时自动创建 fan-out 边。

#### Scenario: User creates handoff connection — 用户创建 handoff 连接
- **当** 用户连接 agent 节点 A 到 agent 节点 B，并在边类型选择器中选择 "Handoff"
- **则** Graph DSL 将边存储为 `data.edgeType` 设置为 "handoff"

#### Scenario: User creates conditional connection — 用户创建 conditional 连接
- **当** 用户连接 condition 节点到 agent 节点，并在边类型选择器中选择 "Conditional"
- **则** Graph DSL 将边存储为 `data.edgeType` 设置为 "conditional"，并提示输入条件标签

#### Scenario: Connection to fan-out auto-sets type — 连接到 Fan-out 自动设置类型
- **当** 用户连接任何节点到 fan-out 节点
- **则** 边自动创建为 fan-out 类型，不显示选择器
