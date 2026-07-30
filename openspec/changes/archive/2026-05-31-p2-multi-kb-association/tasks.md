## 1. 后端：KB 验证

- [x] 1.1 在 `src/hecate/api/management/agents.py` 中添加 `validate_knowledge_base_ids()` 辅助方法，查询 `KnowledgeBaseModel` 验证所有 KB ID 存在且未被软删除，若无效则抛出 HTTP 400 并列出无效 ID
- [x] 1.2 在 `create_agent()` 端点中创建 Agent 模型前调用 `validate_knowledge_base_ids()`
- [x] 1.3 在 `update_agent()` 端点中，当更新 schema 提供了 `knowledge_base_ids` 时调用 `validate_knowledge_base_ids()`
- [x] 1.4 在 `tests/test_api/test_agents.py`（或新文件 `tests/test_api/test_agent_kb_validation.py`）中添加测试：有效的 KB ID、不存在的 KB ID、已软删除的 KB ID、空列表、无 KB ID 字段

## 2. 后端：级联清理

- [x] 2.1 在 `src/hecate/api/management/knowledge.py` 中添加 `cleanup_kb_references()` 辅助函数，从所有 Agent 的 `knowledge_base_ids` JSON 数组中移除已删除的 KB ID
- [x] 2.2 在 KB 删除端点中，软删除 KB 后、返回响应前调用 `cleanup_kb_references()`
- [x] 2.3 添加级联清理测试：删除被多个 Agent 引用的 KB、删除未被任何 Agent 引用的 KB、删除被一个 Agent 引用同时该 Agent 还有其他 KB 的 KB

## 3. 后端：反向查找 API

- [x] 3.1 在 `src/hecate/api/management/knowledge.py` 中添加 `GET /api/knowledge-bases/{id}/agents` 端点，查询 `knowledge_base_ids` JSON 数组中包含该 KB ID 的 Agent，支持分页
- [x] 3.2 如果 KB 不存在或已删除，返回 404
- [x] 3.3 添加反向查找测试：使用某 KB 的 Agent、不存在的 KB、分页

## 4. 后端：跨 KB 搜索聚合

- [x] 4.1 重构 `src/hecate/services/conversation.py` 中的 `_retrieve_knowledge()`，使用 `asyncio.gather()` 并行搜索 KB，而非顺序迭代
- [x] 4.2 重构 `src/hecate/services/orchestration/agent_execution_port.py` 中的 `knowledge_query()`，使用 `asyncio.gather()` 并行搜索 KB
- [x] 4.3 添加测试，验证跨多个 KB 的并行搜索和全局分数排序

## 5. 前端：聊天自动加载 KB ID

- [x] 5.1 更新 `web/src/app/(dashboard)/chat/[conversationId]/page.tsx`，从 Agent 配置中获取 Agent 的 `knowledge_base_ids`（已因模型名称而获取），并存储在状态中
- [x] 5.2 在发送消息时，在 `/v1/chat/completions` 请求中传递 `kb_ids`，从 Agent 的 `knowledge_base_ids` 填充
- [x] 5.3 当 Agent 有关联的 KB ID 时，通过调用 `GET /api/knowledge-bases` 或单个 KB 查找获取 KB 名称用于显示

## 6. 前端：聊天 UI 中的 KB 指示器

- [x] 6.1 在聊天页面头部添加 KB 指示徽章，显示当前对话的活跃 KB 名称
- [x] 6.2 当 Agent 没有 KB 关联时不显示任何内容
- [x] 6.3 在获取 KB 名称期间显示加载状态

## 7. 前端：Agent 配置器错误处理

- [x] 7.1 更新 `web/src/components/agent/agent-configurator.tsx`，捕获并显示由无效 `knowledge_base_ids` 导致的 400 错误，显示在 KB 选择器附近
- [x] 7.2 在 `web/src/components/agent/knowledge-selector.tsx` 中添加错误状态显示，展示验证错误消息

## 8. 验证

- [x] 8.1 运行 `ruff check src/hecate/ tests/` — 零错误（1 个预先存在的 S101）
- [x] 8.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 8.3 运行 `mypy src/` — 零错误
- [x] 8.4 运行 `python -m pytest tests/ -q` — 所有测试通过（排除 5 个预先存在的 test_citation_chat.py 失败）
- [x] 8.5 在 `web/` 目录运行 `npm run lint` 和 `npm run build` — 零错误（1 个预先存在的警告）
