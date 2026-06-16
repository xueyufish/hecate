## Context

Hecate's `ConversationService` orchestrates the full chat loop: security check → context assembly → LLM invocation → tool calling → response streaming. The Agent model (`AgentModel`) stores persona, tools, knowledge bases, and LLM config. The OpenAI-compatible chat endpoint (`/v1/chat/completions`) supports both streaming (SSE) and non-streaming responses.

Currently, when a user starts a new conversation, they receive no greeting or guidance. After each response, there are no follow-up suggestions. This proposal adds auto-generated opening remarks and contextual follow-up suggestions.

Key existing patterns:
- SSE streaming yields typed events: `{"type": "content"}`, `{"type": "citations"}`, `{"type": "done"}`
- Non-streaming responses use `ChatCompletionResponse` with `annotations` on messages
- `AgentModel.persona` stores the agent's system prompt / personality
- LLM calls go through `llm_service.chat()` and `llm_service.chat_stream()`

## Goals / Non-Goals

**Goals:**
- Auto-generate opening remarks (greeting + 3 starter questions) when a conversation begins
- Generate 3-5 contextual follow-up questions after each assistant response
- Support both streaming and non-streaming response modes
- Allow agents to disable suggestions or provide a static opening greeting
- Minimize latency impact — suggestions generated in parallel with response delivery

**Non-Goals:**
- Frontend UI for rendering suggestions (client responsibility)
- Suggestion persistence beyond the current response (no database storage)
- User feedback on suggestions (thumbs up/down) — P3 concern
- Suggestion personalization based on user history — future enhancement
- Opening remarks for workflow-mode agents (only chat mode supported)

## Decisions

### D1: LLM-driven generation with static fallback

**Decision**: Use the configured LLM to generate suggestions via a secondary call. If the LLM call fails or times out (2s), fall back to extracting questions from the agent's persona text.

**Rationale**: LLM-generated suggestions are contextually relevant and adapt to conversation flow. Static fallback ensures the feature never blocks the user experience.

**Alternatives considered:**
- *Pure static suggestions from persona*: Too rigid, doesn't adapt to conversation context.
- *Dedicated suggestion model*: Over-engineered for P2, adds deployment complexity.
- *Pre-computed suggestions at agent creation*: Can't adapt to conversation context.

### D2: Suggestions as SSE event type

**Decision**: Emit `{"type": "suggestions", "questions": [...]}` as a new SSE event, following the same pattern as the existing `citations` event.

**Rationale**: Consistent with the existing streaming architecture. Frontend already handles typed events — adding a new type is minimal effort. Non-streaming responses add `suggested_questions` to the message object.

**Alternatives considered:**
- *Include suggestions in the final chunk*: Breaks the clean event model, mixes concerns.
- *Separate WebSocket channel*: Over-engineered, breaks SSE compatibility.

### D3: Agent-level configuration fields

**Decision**: Add `opening_remarks` (TEXT, nullable) and `enable_suggestions` (BOOLEAN, default true) to `AgentModel`. API request includes `generate_opening` and `generate_suggestions` boolean flags.

**Rationale**: Agent-level config allows per-agent customization. Request-level flags give the client control over when to request suggestions. Static `opening_remarks` bypasses LLM generation entirely for predictable greetings.

**Alternatives considered:**
- *Configuration in model_config JSON*: Harder to query and validate.
- *No per-agent config, always generate*: Too rigid — some agents may not want suggestions.

### D4: Suggestion generation prompt strategy

**Decision**: Use a structured prompt template that includes: agent persona, conversation history (last 2 turns), and the current response. The prompt asks the LLM to return a JSON array of 3-5 questions.

**Rationale**: Including persona ensures suggestions match the agent's domain. Limiting history to 2 turns keeps the prompt short and fast. JSON array format is easy to parse.

**Alternatives considered:**
- *Include full conversation history*: Token-heavy, slow, unnecessary for suggestion quality.
- *Include knowledge base context*: Over-engineered for P2, increases latency.

### D5: Opening remarks trigger

**Decision**: Opening remarks are triggered when `generate_opening=true` in the request AND the messages array contains only the first user message (single message with role "user"). The system generates a greeting and starter questions as the assistant response.

**Rationale**: Explicit trigger via request flag gives the client control. Checking message count (1 message) ensures it's truly the start of a conversation. The greeting is returned as the assistant's content with suggested questions.

**Alternatives considered:**
- *Auto-detect from conversation state*: Requires session/conversation tracking in the stateless chat endpoint.
- *Separate `/opening` endpoint*: Adds API surface unnecessarily.

## Risks / Trade-offs

- **[Latency]** Secondary LLM call for suggestions adds 1-3s → Mitigation: suggestions stream after content, non-blocking; 2s timeout with static fallback.
- **[LLM cost]** Extra token usage per response for suggestion generation → Mitigation: use a small/fast model (e.g., gpt-4o-mini) if available; static opening_remarks bypasses LLM entirely.
- **[Suggestion quality]** LLM may generate irrelevant or duplicate suggestions → Mitigation: structured prompt with persona context; future P3 can add user feedback loop.
- **[Migration]** Two new columns on AgentModel → Mitigation: nullable/defaults ensure zero-downtime migration.
