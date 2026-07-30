## MODIFIED Requirements — 修改后的需求

### Requirement: Knowledge retrieval in conversation flow — 对话流程中的知识检索
The system SHALL accept optional `kb_ids` in `ConversationService.chat()`. When provided, it SHALL retrieve relevant chunks via `KnowledgeBaseService.search()` for each KB in parallel, aggregate results globally sorted by score, and pass the top-k chunks to the context assembler. The knowledge retrieval step SHALL be graceful — errors SHALL be logged and the conversation SHALL proceed without citations.

系统应在 `ConversationService.chat()` 中接受可选的 `kb_ids` 参数。当提供该参数时，系统应通过 `KnowledgeBaseService.search()` 为每个知识库并行检索相关文本块，按分数全局排序聚合结果，并将 top-k 文本块传递给 context assembler。知识检索步骤应具备容错性——错误应被记录，对话应在无引用的情况下继续执行。

#### Scenario: Chat with knowledge bases — 使用知识库进行对话
- **WHEN** `kb_ids` is provided to `ConversationService.chat()`
- **THEN** the service SHALL call `knowledge_base_service.search()` for each KB in parallel, aggregate results globally, sort by score descending, and pass the top-k (default 5) chunks to `ContextAssembler.assemble(knowledge=...)`
- **当**向 `ConversationService.chat()` 提供了 `kb_ids`
- **则**服务应为每个知识库并行调用 `knowledge_base_service.search()`，全局聚合结果，按分数降序排列，并将 top-k（默认 5 个）文本块传递给 `ContextAssembler.assemble(knowledge=...)`

#### Scenario: Chat without knowledge bases — 不使用知识库的对话
- **WHEN** `kb_ids` is not provided or is empty
- **THEN** the conversation SHALL proceed as before with no knowledge retrieval or citation attachment
- **当**未提供或 `kb_ids` 为空
- **则**对话应按原样继续进行，不进行知识检索或引用附加

#### Scenario: Knowledge retrieval failure — 知识检索失败
- **WHEN** `knowledge_base_service.search()` raises an exception for a KB
- **THEN** the service SHALL log the error, skip that KB, and continue with remaining KBs or no citations
- **当** `knowledge_base_service.search()` 对某个知识库抛出异常
- **则**服务应记录错误，跳过该知识库，并使用剩余知识库或无引用继续执行
