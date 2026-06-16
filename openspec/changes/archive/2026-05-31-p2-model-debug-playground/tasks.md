## 1. Frontend: Streaming Response Support

- [x] 1.1 Rewrite `handleTest()` to use `api.stream("/v1/chat/completions", ...)` instead of `api.post("/api/models/test", ...)`
- [x] 1.2 Add streaming state management: content accumulates progressively, typing indicator while streaming
- [x] 1.3 Add streaming toggle checkbox (default: enabled)
- [x] 1.4 Handle streaming errors gracefully (fall back to error display)

## 2. Frontend: System Prompt Support

- [x] 2.1 Add system prompt Textarea field above the user prompt field
- [x] 2.2 Include system message in request: `{role: "system", content: systemPrompt}` as first message
- [x] 2.3 Omit system message when field is empty

## 3. Frontend: Response Time Measurement

- [x] 3.1 Record `Date.now()` at request start
- [x] 3.2 Record TTFT when first content chunk arrives
- [x] 3.3 Record total time when streaming completes
- [x] 3.4 Display TTFT and total time in result section (e.g., "TTFT: 234ms | Total: 1.2s")

## 4. Frontend: Token Usage Visualization

- [x] 4.1 Add horizontal progress bar showing prompt tokens (blue) and completion tokens (green)
- [x] 4.2 Display numeric labels: "Prompt: 45 | Completion: 120 | Total: 165"

## 5. Frontend: Test History

- [x] 5.1 Define TestHistoryEntry interface: model, prompt, systemPrompt, temperature, maxTokens, response, timestamp, latency
- [x] 5.2 Save test results to localStorage (max 10 entries, FIFO)
- [x] 5.3 Add "History" button that opens a dialog/panel showing last 10 tests
- [x] 5.4 Add "Load" action on history entry to populate form fields
- [x] 5.5 Add "Clear History" button with confirmation
- [x] 5.6 Truncate content in history: prompt 100 chars, system prompt 100 chars, response 500 chars

## 6. Frontend: Error Display Improvements

- [x] 6.1 Parse API error response and display code + message
- [x] 6.2 Add suggestion mapping: AUTH errors → "Check API key", NOT_FOUND → "Model not available", 429 → "Rate limited, try again later"
- [x] 6.3 Add retry button on network errors

## 7. Verification

- [x] 7.1 Run `npm run lint` in `web/` — zero errors (1 pre-existing warning)
- [x] 7.2 Run `npm run build` in `web/` — zero errors
