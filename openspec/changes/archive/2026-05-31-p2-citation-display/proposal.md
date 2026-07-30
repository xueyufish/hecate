## Why — 动机

当 Agent 响应引用知识库条目或工具调用结果时，用户目前看到的是一条纯文本回复，无法区分哪些内容来自"系统知道的事实"（知识库）与"模型生成的"。没有引用显示，用户必须信任 LLM 的事实准确性——这是 RAG 应用中的一个已知风险。

引用解决了这个问题：提供来源，用户可以点击以验证 LLM 声明。显示的引用类型包括：
- 工具调用输出（来自 `tool_execute` 的结果）
- 知识库引用（来自 `knowledge_query` 的段落）
- 聊天消息引用（指向对话中的早期消息）

当前系统通过 `EnginePort` 已经有工具调用和知识库查询能力（来自 P1）。缺失的环节是 UI——用于显示带有可点击来源的引用的前端组件。

## What Changes — 变更内容

- **引用数据类型**: Pydantic 模型，用于表示引用元数据（source_id, source_type, excerpt, relevance_score, verified）
- **引用提取**: 从工具调用结果和知识库查询中提取引用的服务层逻辑
- **引用 UI 组件**: 在消息气泡内内联渲染引用的前端组件。引用显示为可折叠的源引用，点击可展开，显示摘录和来源路径
- **使用场景**: 知识库引用显示，工具调用结果引用，会话（debug）模式中的引用显示
- **API 集成**: 消息响应中包含引用数据，由前端渲染

## Capabilities — 能力

### New Capabilities — 新增能力
- `citation-display`: 在聊天 UI 中带有可折叠摘录的源引用，知识库查询的来源引用，工具调用结果的来源引用，引用元数据（来源、类型、分值）

### Modified Capabilities — 修改的能力
- **消息 API**: `MessageResponse` schema 包含可选引用列表
- **LLM 聊天 UI**: 消息气泡和对话框为内联引用做好准备

## Impact — 影响

- **API**: `MessageResponse` 添加可选的 `citations` 字段。`GET /api/conversations/{id}` 在消息中包含引用。
- **服务**: 新的引用提取逻辑处理知识库和工具调用结果的引用。与现有的 `ConversationService` 和 `KnowledgeService` 集成。
- **前端**: 新的 `Citation` React 组件。更新了对话组件以渲染引用。
- **模型**: 新增 `Citation` schema，但 DB 迁移是可选的（引用可以在运行时计算）。
