## 1. 数据和 Schema

- [x] 1.1 在 `src/hecate/models/llm.py` 中创建 `CitationSchema`——字段：`source_type: Literal["knowledge_base", "tool_call", "message"]`, `source_id: str`, `excerpt: str`, `relevance_score: float | None`, `title: str | None`, `url: str | None`
- [x] 1.2 向 `src/hecate/models/llm.py` 中的 `MessageReadSchema` 添加可选的 `citations: list[CitationSchema]` 字段

## 2. 服务层 — 引用提取

- [x] 2.1 创建 `src/hecate/services/llm/citation_extractor.py`——`CitationExtractor` 类，方法 `from_knowledge_query(result: dict) -> list[CitationSchema]`，从向量搜索结果提取引用（source_id、摘录、分值、文档标题）
- [x] 2.2 添加 `CitationExtractor.from_tool_call(tool_name: str, result: dict) -> list[CitationSchema]`——当工具结果包含标准引用字段时提取引用
- [x] 2.3 更新 `src/hecate/services/llm/conversation_service.py`——在 `send_message()` 中，在 LLM 调用 + 工具执行后，提取引用并附加到消息响应

## 3. 前端 — 引用 UI 组件

- [x] 3.1 创建 `web/src/types/llm.ts` 中的 `Citation` 接口——映射 `CitationSchema`
- [x] 3.2 创建 `web/src/components/chat/CitationTag.tsx`——内联引用标签组件（`<sup>[1]</sup>` 样式标签），悬停展开显示工具提示，点击展开详细信息卡片
- [x] 3.3 创建 `web/src/components/chat/CitationCard.tsx`——展开的引用细节卡片，显示标题、摘录文本、相关性分值、来源类型
- [x] 3.4 更新 `web/src/components/chat/MessageBubble.tsx`——在 LLM 回复文本中的引用编号位置渲染 `CitationTag`，在消息气泡底部渲染 `CitationCard` 列表
- [x] 3.5 更新 `web/src/components/chat/Conversation.tsx`——渲染引用作为消息渲染时传递 `citations` 属性
- [x] 3.6 更新 `web/src/components/workflow/TestRunnerPanel.tsx`——在调试/会话模式下显示引用

## 4. 测试

- [x] 4.1 创建 `tests/test_services/test_citation_extractor.py`——测试从知识库查询提取引用（含匹配、无匹配、空结果）、从工具调用提取引用（含引用字段、无引用字段）
- [x] 4.2 创建 `tests/test_services/test_conversation_with_citations.py`——测试消息响应包含正确引用
