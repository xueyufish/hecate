## ADDED Requirements — 新增需求

### Requirement: Fan-out and merge nodes in node palette — 节点面板中的 Fan-out 和 Merge 节点
NodePalette 组件应将 fan-out 和 merge 作为可拖拽项包含在内，允许用户交互式创建这些节点。

#### Scenario: Fan-out in node palette — 节点面板中的 Fan-out
- **当** 工作流编辑器加载
- **则** 节点面板应包含一个带有分支图标和靛蓝色的 "Fan Out" 项

#### Scenario: Merge in node palette — 节点面板中的 Merge
- **当** 工作流编辑器加载
- **则** 节点面板应包含一个带有合并图标和石板色的 "Merge" 项

#### Scenario: Drag fan-out to canvas — 拖拽 Fan-out 到画布
- **当** 用户将 "Fan Out" 项从面板拖拽到画布上
- **则** 在拖放位置创建一个新的 fan-out 节点，默认分支数量为 2

#### Scenario: Drag merge to canvas — 拖拽 Merge 到画布
- **当** 用户将 "Merge" 项从面板拖拽到画布上
- **则** 在拖放位置创建一个新的 merge 节点

### Requirement: Fan-out node branch configuration — Fan-out 节点分支配置
当选中 fan-out 节点时，配置面板应显示分支数量选择器（2-6）和已连接的分支目标节点列表。

#### Scenario: Configure branch count — 配置分支数量
- **当** 用户在 fan-out 配置面板中将分支数量从 2 改为 3
- **则** fan-out 节点的 `config.branches` 应更新以容纳 3 个分支目标

#### Scenario: Branch targets listed — 列出分支目标
- **当** 用户选择一个连接到 "analyst_a"、"analyst_b"、"analyst_c" 节点的 fan-out 节点
- **则** 配置面板应列出这些目标节点，并将 `config.branches` 更新为 ["analyst_a", "analyst_b", "analyst_c"]

#### Scenario: Branch targets auto-synced from edges — 分支目标从边自动同步
- **当** 用户从 fan-out 节点连接到目标节点的新边
- **则** fan-out 配置面板应自动更新分支列表以包含新目标

### Requirement: Merge node source configuration — Merge 节点源配置
当选中 merge 节点时，配置面板应显示 fan-out 源选择器和输出通道字段。

#### Scenario: Configure fan-out source — 配置 Fan-out 源
- **当** 用户选择 merge 节点并从 fan-out 源下拉列表中选择 "fanout"
- **则** 节点的 `config.fan_out_source` 应设置为 "fanout"

#### Scenario: Configure output channel — 配置输出通道
- **当** 用户在输出通道字段中输入 "analysis_results"
- **则** 节点的 `config.output_channel` 应设置为 "analysis_results"

#### Scenario: Fan-out source dropdown lists available fan-out nodes — Fan-out 源下拉列表列出可用 Fan-out 节点
- **当** 用户打开 merge 节点的 fan-out 源下拉列表
- **则** 下拉列表应列出当前画布上的所有 fan-out 节点

### Requirement: Fan-out/merge validation warnings — Fan-out/Merge 验证警告
当 fan-out 或 merge 节点具有无效配置时，配置面板应显示视觉警告。

#### Scenario: Fan-out with no branches warning — Fan-out 无分支警告
- **当** fan-out 节点没有已连接的分支边
- **则** 配置面板应显示警告"未连接任何分支。请连接边到目标节点。"

#### Scenario: Merge with no fan-out source warning — Merge 无 Fan-out 源警告
- **当** merge 节点未设置 `config.fan_out_source`
- **则** 配置面板应显示警告"未链接任何 fan-out 源。请选择一个 fan-out 节点作为源。"

### Requirement: Fan-out visual badge on canvas — 画布上的 Fan-out 视觉徽章
Fan-out 节点应显示一个徽章，表示已连接分支的数量。

#### Scenario: Fan-out branch count badge — Fan-out 分支数量徽章
- **当** fan-out 节点有 3 条已连接的分支边
- **则** 节点应显示徽章 "×3" 表示 3 个分支
