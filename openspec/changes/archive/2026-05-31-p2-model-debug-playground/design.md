## Context

The model debug page at `/settings/models/debug` currently supports:
- Model selection (grouped by provider via `/v1/models`)
- Prompt input
- Temperature slider (0-2)
- Max tokens slider (1-2000)
- Non-streaming test via `POST /api/models/test`
- Result display (content, model, usage)

The backend API `POST /api/models/test` accepts `model_id`, `prompt`, `temperature`, `max_tokens` and returns `content`, `model`, `usage`, `finish_reason`. It does NOT support streaming or system prompts.

However, the existing `/v1/chat/completions` endpoint supports both streaming and system messages. We can use this endpoint instead of `/api/models/test` for enhanced functionality.

## Goals / Non-Goals

**Goals:**
- Streaming response display (progressive rendering)
- System prompt field for testing agent-like configurations
- Response time measurement (latency in ms)
- Test history in localStorage (last 10 tests)
- Token usage visualization
- Better error messages

**Non-Goals:**
- Backend changes (use existing `/v1/chat/completions`)
- Tool calling tests (future enhancement)
- Multi-model side-by-side comparison (future enhancement)
- Export/import test configurations

## Decisions

### D1: Use `/v1/chat/completions` instead of `/api/models/test`

**Decision**: Switch from `POST /api/models/test` to `POST /v1/chat/completions` with `stream: true`.

**Rationale**: The chat completions endpoint already supports streaming, system messages, and returns the same data. No backend changes needed.

**Alternatives considered**:
- Extend `/api/models/test` to support streaming — adds backend complexity for no benefit
- Use both endpoints (streaming via chat, non-streaming via test) — inconsistent UX

### D2: Streaming via existing api.stream() client method

**Decision**: Use the existing `api.stream()` method for progressive rendering.

**Rationale**: Already implemented in `api-client.ts`, handles SSE parsing, yields content chunks.

### D3: Test history in localStorage (not database)

**Decision**: Store last 10 tests in localStorage with model, prompt, system prompt, params, result, and timestamp.

**Rationale**: No backend changes. Tests are ephemeral debugging artifacts, not persistent data. localStorage is sufficient for session-level history.

**Alternatives considered**:
- Database storage — overkill for debug tool, adds API complexity
- No history — users lose context when navigating away

### D4: Response time via Date.now() measurement

**Decision**: Measure latency as `Date.now()` before request start and after first/last chunk received.

**Rationale**: Simple, no backend changes. Shows time-to-first-token (TTFT) and total time.

## Risks / Trade-offs

- **[Streaming requires different UI state]** → Need to handle progressive content rendering, show "typing" indicator, disable controls during streaming
- **[localStorage size limits]** → 10 tests with full response content could be large. Mitigation: truncate content to 500 chars in history
- **[System prompt via /v1/chat/completions]** → The endpoint uses `get_current_user_id` dependency, not `verify_api_key`. Need to handle auth correctly in the frontend
