## ADDED Requirements — 新增需求

### Requirement: 扇出和合并节点类型在前端类型系统中定义 — 扇出和合并节点类型在前端类型系统中定义
前端 NodeTypeSchema SHALL 包含 "fan-out" 和 "merge" 作为有效的节点类型枚举值，与后端 DSL 模式匹配。

#### Scenario: 加载包含扇出节点的 DSL 无错误
- **WHEN** 包含类型为 "fan-out" 的节点的 Graph DSL 通过 dslToReactFlow 加载
- **THEN** 节点在画布上渲染，无错误且不会被丢弃

### Requirement: 扇出和合并节点使用不同的视觉组件渲染 — 扇出和合并节点使用不同的视觉组件渲染
扇出节点 SHALL 使用不同的图标和颜色显示（例如，分叉图标，靛蓝色）。合并节点 SHALL 使用不同的图标和颜色显示（例如，合并图标，石板色）。

#### Scenario: 扇出节点视觉渲染
- **WHEN** 扇出节点从 DSL 加载到画布上
- **THEN** 它以靛蓝色背景、类似分叉的图标和标签 "Fan Out" 渲染

#### Scenario: 合并节点视觉渲染
- **WHEN** 合并节点从 DSL 加载到画布上
- **THEN** 它以石板色背景、合并图标和标签 "Merge" 渲染

### Requirement: 扇出和合并不在节点调色板中 — 扇出和合并不在节点调色板中
NodePalette 组件 SHALL NOT 包含扇出或合并作为可拖拽项。这些节点类型只能在从现有 DSL 加载时出现。

#### Scenario: 节点调色板内容
- **WHEN** 工作流编辑器加载
- **THEN** 节点调色板显示恰好 6 项：Conversation、Condition、Tool Call、Agent、Knowledge Retrieval、Variable Set

### Requirement: DSL 桥接映射扇出和合并标签 — DSL 桥接映射扇出和合并标签
dsl-bridge.ts 中的 NODE_TYPE_LABELS 映射 SHALL 包含 "fan-out" → "Fan Out" 和 "merge" → "Merge" 的条目。

#### Scenario: 来自 DSL 的扇出节点标签
- **WHEN** dslToReactFlow 处理类型为 "fan-out" 的节点
- **THEN** 如果不存在 system_prompt 或其他标签源，节点的标签默认为 "Fan Out"