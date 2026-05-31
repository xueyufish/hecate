## Why

When a user sends multiple messages rapidly to the same conversation, the current system processes them concurrently. This causes race conditions: messages can arrive at the LLM out of order, memory blocks can be corrupted by concurrent writes, and context assembly can produce inconsistent results. The feature catalog describes this as "会话内顺序处理任务，新消息自动排队等待，避免并发冲突" — sequential task processing within a session with automatic queuing.

## What Changes

- Add a **per-session lock** mechanism — only one message processes at a time per conversation
- Add **automatic queuing** — new messages for a busy session are queued and processed in FIFO order
- Add **queue status in API response** — client can see if a message is queued (position) or processing
- Add **queue timeout** — queued messages expire after 5 minutes to prevent infinite waits
- Add **frontend queue indicator** — chat UI shows "Queued (position 2)..." while waiting

## Capabilities

### New Capabilities
- `task-queuing`: Per-session sequential message processing with automatic queuing and timeout

### Modified Capabilities
- `session-memory`: Add requirement for sequential processing within a session

## Impact

- **Backend**: `conversation.py` — add session lock manager, wrap `chat()` in lock
- **Backend**: `chat.py` — add queue status to response headers
- **Frontend**: Chat page — show queue indicator when message is queued
- **Tests**: Concurrent message handling tests
- **No external dependencies** — uses `asyncio.Lock` per session, in-memory queue
