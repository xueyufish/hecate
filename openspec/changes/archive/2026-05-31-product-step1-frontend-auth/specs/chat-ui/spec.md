## ADDED Requirements — 新增需求

### Requirement: Chat interface — 需求：聊天界面
系统应提供与 Agent 实时对话的聊天界面。

#### Scenario: Send message — 场景：发送消息
- **WHEN** 用户输入消息并按 Enter 或点击 Send
- **THEN** 系统将消息发送到 `POST /v1/chat/completions`，并通过 SSE 流式实时显示响应

#### Scenario: Streaming display — 场景：流式显示
- **WHEN** Agent 正在生成响应
- **THEN** 系统在文本到达时逐 token 显示，生成期间显示输入指示器

#### Scenario: Chat initialization — 场景：聊天初始化
- **WHEN** 用户为 Agent 打开聊天
- **THEN** 系统创建新对话（或加载现有对话），并显示带输入框的聊天界面

### Requirement: Tool call display — 需求：工具调用展示
系统应在聊天中内联显示工具调用及其结果。

#### Scenario: Tool call shown — 场景：工具调用展示
- **WHEN** Agent 在生成过程中调用工具
- **THEN** 系统显示可折叠块，包含工具名称、参数和结果

### Requirement: Conversation history — 需求：对话历史
系统应加载并显示当前对话中的历史消息。

#### Scenario: Load history — 场景：加载历史
- **WHEN** 用户打开现有对话
- **THEN** 系统从 `GET /api/conversations/{id}` 加载所有历史消息并显示

### Requirement: New conversation — 需求：新对话
系统应允许与 Agent 开始新对话。

#### Scenario: Start new chat — 场景：开始新聊天
- **WHEN** 用户点击"New Chat"按钮
- **THEN** 系统创建新对话并显示空聊天界面
