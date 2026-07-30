## 1. 后端：会话锁管理器

- [x] 1.1 创建 `src/hecate/services/session_lock.py`，包含 `SessionLockManager` 类，管理每个 session_id 的 `asyncio.Lock`
- [x] 1.2 实现 `acquire(session_id, timeout=300)` 方法，返回锁上下文管理器，5 分钟后抛出超时异常
- [x] 1.3 实现 `get_queue_position(session_id)` 方法，空闲时返回 0，排队中返回 1+
- [x] 1.4 在模块级别添加单例实例 `session_lock_manager`

## 2. 后端：Conversation Service 集成

- [x] 2.1 更新 `ConversationService.chat()` 以接受可选的 `session_id` 参数用于加锁
- [x] 2.2 在提供 session_id 时将聊天处理逻辑包装在 `session_lock_manager.acquire(session_id)` 中
- [x] 2.3 添加队列位置追踪：获取时增加计数，释放时减少计数

## 3. 后端：聊天 API 集成

- [x] 3.1 更新 `/v1/chat/completions` 端点，从请求中提取 `session_id`（从对话中或生成）
- [x] 3.2 将端点处理程序包装在会话锁获取中，带超时处理
- [x] 3.3 添加 `X-Queue-Position` 和 `X-Queue-Wait-Ms` 响应头
- [x] 3.4 当队列超时时返回 HTTP 408

## 4. 后端：测试

- [x] 4.1 为 `SessionLockManager` 添加单元测试：获取/释放、队列位置、超时
- [x] 4.2 添加集成测试：同一会话的并发消息按顺序处理
- [x] 4.3 添加集成测试：不同会话独立处理
- [x] 4.4 添加队列超时返回 408 的测试

## 5. 前端：队列指示器

- [x] 5.1 更新聊天页面以从流式响应读取 `X-Queue-Position` 头
- [x] 5.2 当 position > 0 时显示"Queued (position N)..."指示器
- [x] 5.3 响应开始流式传输时移除指示器

## 6. 验证

- [x] 6.1 运行 `ruff check src/hecate/ tests/` — 零错误（1 个预先存在的 S101）
- [x] 6.2 运行 `ruff format --check src/ tests/` — 零错误
- [x] 6.3 运行 `mypy src/` — 零错误
- [x] 6.4 运行 `python -m pytest tests/ -q` — 所有测试通过
- [x] 6.5 在 `web/` 目录运行 `npm run lint` 和 `npm run build` — 零错误
