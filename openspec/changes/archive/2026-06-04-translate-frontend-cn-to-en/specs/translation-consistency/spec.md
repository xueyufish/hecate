## ADDED Requirements — 新增需求

### Requirement: 中文 UI 文本翻译为英文 — 中文 UI 文本翻译为英文
`web/src/` 中所有 UI 组件中的中文字符串 SHALL 根据映射表被英文对应词替换。映射表 SHALL 维护一致：中文术语始终映射到相同的英文术语。

#### Scenario: 节点类型标签
- **WHEN** 显示节点类型 `"对话"`
- **THEN** UI SHALL 显示 `"Conversation"`

#### Scenario: 工具栏按钮标签
- **WHEN** 显示 `"保存"` 按钮
- **THEN** UI SHALL 显示 `"Save"`

#### Scenario: 空状态消息
- **WHEN** 没有工作流且显示 `"暂无工作流"`
- **THEN** UI SHALL 显示 `"No workflows yet"`

#### Scenario: 验证消息
- **WHEN** 验证成功且触发 `alert("验证通过")`
- **THEN** 消息 SHALL 为 `"Validation passed"`

### Requirement: Handoff 边标签修复 — Handoff 边标签修复
所有出现 `"移交"` 的地方 SHALL 翻译为 `"Handoff"`，包括渲染和相等性检查。

#### Scenario: 边标签渲染
- **WHEN** 边类型为 handoff
- **THEN** 边的 UI 标签 SHALL 显示 `"Handoff"`

#### Scenario: 相等性检查
- **WHEN** 代码检查 `edge.type === "移交"`
- **THEN** 它 SHALL 检查 `"Handoff"` 而不是 `"移交"`

### Requirement: 拼写错误修复 — 拼写错误修复
`config-panel.tsx` 中的字符串 `"knowledge-rerieval"` SHALL 更正为 `"knowledge-retrieval"`。

#### Scenario: 拼写正确
- **WHEN** 在 config-panel.tsx 中引用知识检索类型
- **THEN** 它 SHALL 使用 `"knowledge-retrieval"`