## ADDED Requirements — 新增需求

### Requirement: 节点位置持久化到 localStorage — 节点位置持久化到 localStorage
当节点被移动或工作流被保存时，系统 SHALL 将每个节点的 x/y 位置持久化到 localStorage，以 `hecate-layout-{workflowId}` 为键。

#### Scenario: 节点拖拽后保存布局
- **WHEN** 用户将节点拖拽到画布上的新位置
- **THEN** 在现有的 2 秒自动保存去抖后，节点位置保存到 localStorage 的 `hecate-layout-{workflowId}` 下

#### Scenario: 页面加载时恢复布局
- **WHEN** 用户打开一个在 localStorage 中有保存布局的工作流
- **THEN** 节点在其保存的位置渲染，而不是默认网格布局

### Requirement: 无保存位置时回退到网格布局 — 无保存位置时回退到网格布局
当加载没有 localStorage 布局数据的工作流时，系统 SHALL 使用现有的基于网格的布局公式。

#### Scenario: 首次打开工作流
- **WHEN** 用户打开一个在此设备上从未编辑过的工作流
- **THEN** 节点使用默认网格布局定位（250px 水平间距，150px 垂直间距）

#### Scenario: 手动清除 localStorage
- **WHEN** 用户清除浏览器 localStorage
- **THEN** 下次页面加载时，节点回退到默认网格布局

### Requirement: 布局数据独立于 DSL — 布局数据独立于 DSL
布局存储 SHALL 完全独立于 Graph DSL。DSL SHALL NOT 包含位置字段。布局和 DSL 分别加载并在渲染时合并。

#### Scenario: DSL 不包含位置数据
- **WHEN** 后端通过 GET /api/workflows/{id} 返回 Graph DSL
- **THEN** 响应中不包含节点的 x/y 位置字段