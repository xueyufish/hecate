## Why

The model debug page at `/settings/models/debug` exists but is minimal — it supports single-shot testing with temperature and max_tokens. For a production-grade platform, users need **streaming responses** to see output as it arrives, **system prompt support** to test real agent configurations, **response time measurement** to compare providers, and **test history** to track experiments. These are table-stakes for model debugging in tools like AgentArts and Coze.

## What Changes

- Add **streaming support** — response appears progressively, not after completion
- Add **system prompt field** — test with system messages like real agents use
- Add **response time measurement** — show latency in milliseconds
- Add **test history** — save last 10 tests in localStorage for comparison
- Add **token usage visualization** — progress bar showing prompt vs completion tokens
- Improve **error display** — show provider-specific error details with suggestions

## Capabilities

### New Capabilities
- `model-debug-playground`: Enhanced model testing UI with streaming, system prompt, latency tracking, and test history

### Modified Capabilities
- (none — existing spec is not formalized)

## Impact

- **Frontend only** — no backend changes needed (streaming uses existing `/v1/chat/completions` endpoint)
- `web/src/app/(dashboard)/settings/models/debug/page.tsx` — rewrite with enhanced features
- **Tests**: No new backend tests needed; frontend manual verification
