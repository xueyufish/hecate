## ADDED Requirements — 新增需求

### Requirement: Canvas renders a directed graph of nodes and edges — 画布渲染节点和边的有向图
The system SHALL render a visual canvas where each DSL node appears as a draggable card with an icon and label, and each DSL edge appears as a directed line between source and target nodes.

系统应渲染一个可视化画布，其中每个 DSL 节点显示为带有图标和标签的可拖拽卡片，每个 DSL 边显示为源节点和目标节点之间的有向线。

#### Scenario: Render a workflow with 3 connected nodes — 渲染包含 3 个连接节点的工作流
- **WHEN** a workflow is opened in the canvas editor
- **THEN** all nodes from the graph DSL are rendered as draggable cards at their stored positions, and all edges are rendered as directed lines
- **当**在画布编辑器中打开工作流
- **则**图 DSL 中的所有节点渲染为存储位置上的可拖拽卡片，所有边渲染为有向线

#### Scenario: Empty workflow shows entry point — 空工作流显示入口点
- **WHEN** a new empty workflow is created
- **THEN** the canvas shows a single `__start__` entry node that cannot be deleted
- **当**创建新的空工作流
- **则**画布显示一个不可删除的 `__start__` 入口节点

### Requirement: Users can add nodes from a palette — 用户可以从面板添加节点
The system SHALL provide a node palette sidebar listing all available node types. Dragging a node type from the palette onto the canvas SHALL create a new node of that type.

系统应提供一个节点面板侧边栏，列出所有可用的节点类型。将节点类型从面板拖拽到画布上应创建一个该类型的新节点。

#### Scenario: Drag LLM node from palette — 从面板拖拽 LLM 节点
- **WHEN** user drags "LLM Call" from the node palette onto the canvas
- **THEN** a new node of type `conversation` appears at the drop position with a generated unique ID and default config
- **当**用户从节点面板拖拽"LLM Call"到画布上
- **则**在拖放位置出现一个类型为 `conversation` 的新节点，带有生成的唯一 ID 和默认配置

#### Scenario: Drag Condition node from palette — 从面板拖拽条件节点
- **WHEN** user drags "Condition" from the node palette onto the canvas
- **THEN** a new node of type `condition` appears with a default expression field
- **当**用户从节点面板拖拽"Condition"到画布上
- **则**出现一个类型为 `condition` 的新节点，带有默认表达式字段

### Requirement: Users can connect nodes with edges — 用户可以用边连接节点
The system SHALL allow users to draw directed edges between nodes by dragging from a source node's output handle to a target node's input handle.

系统应允许用户通过从源节点的输出句柄拖拽到目标节点的输入句柄来绘制节点之间的有向边。

#### Scenario: Connect two nodes — 连接两个节点
- **WHEN** user drags from node A's output handle to node B's input handle
- **THEN** a directed edge from A to B is created and displayed on the canvas
- **当**用户从节点 A 的输出句柄拖拽到节点 B 的输入句柄
- **则**创建从 A 到 B 的有向边并在画布上显示

#### Scenario: Prevent self-loops — 防止自环
- **WHEN** user attempts to connect a node's output to its own input
- **THEN** the connection is rejected and no edge is created
- **当**用户尝试将节点的输出连接到自身的输入
- **则**连接被拒绝，不会创建边

### Requirement: Canvas supports zoom, pan, and minimap — 画布支持缩放、平移和小地图
The system SHALL provide zoom controls (+/-/fit), mouse wheel zoom, click-drag panning, and a minimap overview in the bottom-right corner.

系统应提供缩放控制（+/-/适应）、鼠标滚轮缩放、点击拖拽平移以及右下角的小地图概览。

#### Scenario: Zoom to fit — 自适应缩放
- **WHEN** user clicks the "Fit View" button
- **THEN** the canvas zooms and pans so all nodes are visible within the viewport
- **当**用户点击"适应视图"按钮
- **则**画布缩放和平移，使所有节点在视口内可见

### Requirement: Canvas state persists per workflow version — 画布状态按工作流版本持久化
Node positions and viewport state SHALL be stored as part of the graph DSL metadata so reopening a workflow restores the visual layout.

节点位置和视口状态应作为图 DSL 元数据的一部分存储，以便重新打开工作流时恢复可视化布局。

#### Scenario: Reopen workflow restores layout — 重新打开工作流恢复布局
- **WHEN** user closes and reopens a workflow
- **THEN** all nodes appear at their last saved positions with the same viewport zoom and pan
- **当**用户关闭并重新打开工作流
- **则**所有节点出现在上次保存的位置，并保持相同的视口缩放和平移状态
