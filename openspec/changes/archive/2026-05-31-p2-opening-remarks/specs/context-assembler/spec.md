## MODIFIED Requirements — 修改的需求

### Requirement: Context Assembler supports suggestion generation mode — 需求：Context Assembler 支持建议生成模式
`ContextAssembler` 应接受 `suggestion_mode` 参数（默认 None）。当设置为 `"opening"` 或 `"followup"` 时，汇编器应构建建议生成提示，而非标准聊天上下文。提示应包括 Agent 角色设定和相关的对话上下文，格式化为结构化问题生成。

#### Scenario: Opening remarks suggestion mode — 场景：开场白建议模式
- **WHEN** 提供 `suggestion_mode="opening"`，并附带 Agent 角色设定和能力
- **THEN** 汇编器应返回 `AssembledContext`，包含一条包含开场白提示模板的系统消息和一条包含 Agent 元数据的用户消息

#### Scenario: Follow-up suggestion mode — 场景：后续建议模式
- **WHEN** 提供 `suggestion_mode="followup"`，并附带对话历史和 Agent 角色设定
- **THEN** 汇编器应返回 `AssembledContext`，包含一条包含后续提示模板的系统消息和包含最近 2 轮对话的消息

#### Scenario: Default mode (no change) — 场景：默认模式（无变化）
- **WHEN** `suggestion_mode` 为 None
- **THEN** 汇编器应像之前一样进行标准上下文组装
