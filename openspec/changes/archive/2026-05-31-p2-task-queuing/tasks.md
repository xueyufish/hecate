## 1. Backend: Session Lock Manager

- [x] 1.1 Create `src/hecate/services/session_lock.py` with `SessionLockManager` class that manages `asyncio.Lock` per session_id
- [x] 1.2 Implement `acquire(session_id, timeout=300)` method that returns lock context manager, raises timeout after 5 minutes
- [x] 1.3 Implement `get_queue_position(session_id)` method returning 0 if idle, 1+ if queued
- [x] 1.4 Add singleton instance `session_lock_manager` at module level

## 2. Backend: Conversation Service Integration

- [x] 2.1 Update `ConversationService.chat()` to accept optional `session_id` parameter for locking
- [x] 2.2 Wrap the chat processing logic in `session_lock_manager.acquire(session_id)` when session_id is provided
- [x] 2.3 Add queue position tracking: increment counter on acquire, decrement on release

## 3. Backend: Chat API Integration

- [x] 3.1 Update `/v1/chat/completions` endpoint to extract `session_id` from request (from conversation or generate)
- [x] 3.2 Wrap endpoint handler in session lock acquisition with timeout handling
- [x] 3.3 Add `X-Queue-Position` and `X-Queue-Wait-Ms` response headers
- [x] 3.4 Return HTTP 408 when queue timeout is exceeded

## 4. Backend: Tests

- [x] 4.1 Add unit tests for `SessionLockManager`: acquire/release, queue position, timeout
- [x] 4.2 Add integration tests: concurrent messages for same session processed sequentially
- [x] 4.3 Add integration tests: different sessions process independently
- [x] 4.4 Add test for queue timeout returning 408

## 5. Frontend: Queue Indicator

- [x] 5.1 Update chat page to read `X-Queue-Position` header from streaming response
- [x] 5.2 Display "Queued (position N)..." indicator when position > 0
- [x] 5.3 Remove indicator when response starts streaming

## 6. Verification

- [x] 6.1 Run `ruff check src/hecate/ tests/` — zero errors (1 pre-existing S101)
- [x] 6.2 Run `ruff format --check src/ tests/` — zero errors
- [x] 6.3 Run `mypy src/` — zero errors
- [x] 6.4 Run `python -m pytest tests/ -q` — all tests pass
- [x] 6.5 Run `npm run lint` and `npm run build` in `web/` — zero errors
