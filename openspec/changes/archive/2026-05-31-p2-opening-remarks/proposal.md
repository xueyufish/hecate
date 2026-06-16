## Why

When a user starts a new conversation with an Agent, they see a blank input box with no guidance on what the Agent can do. After each response, users must think of follow-up questions themselves. This creates friction and reduces engagement. Major Agent platforms (Coze, AgentArts) solve this by auto-generating an opening greeting and contextual follow-up suggestions — making conversations feel natural and guided from the first interaction.

## What Changes

- **Opening remarks generation**: When a conversation starts (first user message or explicit request), the system uses the Agent's persona and configuration to auto-generate a greeting message with suggested starter questions.
- **Follow-up suggestions**: After each assistant response, the system generates 3-5 contextual follow-up question suggestions based on the conversation history and Agent capabilities.
- **SSE streaming event**: New `"suggestions"` event type in the SSE stream, emitted after content and before `[DONE]`.
- **API response extension**: Non-streaming responses include a `suggested_questions` field alongside existing `annotations`.
- **Agent configuration**: Optional `opening_remarks` (static greeting override) and `enable_suggestions` (toggle, default true) fields on AgentModel.
- **LLM-based generation**: Use a lightweight LLM call to generate context-aware suggestions, with fallback to a static list derived from Agent persona.

## Capabilities

### New Capabilities
- `opening-remarks`: Opening greeting generation and follow-up question suggestion system — LLM-driven with static fallback, SSE event streaming, and Agent-level configuration

### Modified Capabilities
- `context-assembler`: Add support for a suggestion-generation prompt mode alongside existing knowledge injection

## Impact

- **Models**: `AgentModel` gains `opening_remarks` (str | None) and `enable_suggestions` (bool, default True) columns via Alembic migration
- **Services**: `ConversationService` gains `_generate_opening()` and `_generate_suggestions()` methods
- **API**: `ChatCompletionRequest` gains `generate_opening` (bool) and `generate_suggestions` (bool) flags; `ChatMessage` gains `suggested_questions` field
- **API**: New SSE event type `{"type": "suggestions", "questions": [...]}` emitted before `[DONE]`
- **Streaming**: Suggestions generated via a secondary LLM call after the primary response completes
- **Dependencies**: No new external dependencies — uses existing LLM service
