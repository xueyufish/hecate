## 1. 服务层 — 记忆接入 ConversationService

- [x] 1.1 在 `ConversationService.chat()` 中加载 L1 工作记忆块 — 调用 `WorkingMemoryService.list_blocks(agent_id)` 并将结果传入 `ContextAssembler.assemble(memory_blocks=...)`
- [x] 1.2 在 `ConversationService.chat()` 中实现 L2 压缩触发 — 检查 token 计数，超过 `compression_threshold` 时调用 `CompressionPipeline.compress()` 压缩历史消息
- [x] 1.3 在 `ConversationService.chat()` 中实现 L3 用户记忆检索 — 调用 `UserMemoryService.retrieve_memories(user_id, query)` 获取相关记忆，传入 `ContextAssembler.assemble(user_memories=...)`
- [x] 1.4 在 `ConversationService.chat()` Assistant 回复后调用 `UserMemoryService.extract_facts()` 提取新事实 — 异步执行，不阻塞响应
- [x] 1.5 注册 `update_memory_block` 和 `search_user_memory` 记忆工具到 Agent 工具列表 — 当 Agent 配置了工作记忆或用户启用了 L3 记忆时自动注册

## 2. API 层 — 记忆管理端点

- [x] 2.1 扩展 `api/management/memory.py` — `GET /api/agents/{agent_id}/memory/blocks` 列出工作记忆块
- [x] 2.2 `POST /api/agents/{agent_id}/memory/blocks` 创建/更新记忆块
- [x] 2.3 `PUT /api/agents/{agent_id}/memory/blocks/{block_id}` 更新指定记忆块
- [x] 2.4 `DELETE /api/agents/{agent_id}/memory/blocks/{block_id}` 删除记忆块
- [x] 2.5 `GET /api/users/{user_id}/memories` 列出用户记忆（分页）
- [x] 2.6 `GET /api/users/{user_id}/memories/search?q={query}` 语义搜索用户记忆
- [x] 2.7 `DELETE /api/users/{user_id}/memories/{memory_id}` 删除指定用户记忆
- [x] 2.8 `GET /api/sessions/{session_id}/compression` 返回会话压缩历史
- [x] 2.9 注册记忆管理路由到 `main.py`

## 3. 测试

- [x] 3.1 `tests/test_services/test_memory/test_integration.py` — 测试 ConversationService 记忆注入集成（L1 注入、L2 压缩触发、L3 检索 + 提取）
- [x] 3.2 `tests/test_api/test_memory.py` — 测试记忆管理 API 端点（CRUD 块、用户记忆列表/搜索/删除、压缩状态）
- [x] 3.3 更新 `tests/test_services/test_context/test_integration.py` — 添加记忆注入到 ContextAssembler 的集成测试

## 4. 文档

- [x] 4.1 更新 `docs/features/feature-catalog.md` — 标记 4.2 会话记忆、4.3 用户记忆为已实现
- [x] 4.2 运行 ruff + mypy + pytest 全量验证
