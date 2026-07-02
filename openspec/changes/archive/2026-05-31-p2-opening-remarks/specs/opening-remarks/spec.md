## ADDED Requirements

### Requirement: Opening remarks generation
The system SHALL generate an opening greeting with 3 starter questions when a conversation starts. The greeting SHALL be based on the agent's persona, tools, and knowledge bases. When the agent has a configured `opening_remarks` field, the system SHALL use that static text instead of generating one via LLM.

#### Scenario: Auto-generated opening remarks for new conversation
- **WHEN** `generate_opening=true` is set in the chat request AND the messages array contains exactly 1 message with role "user"
- **THEN** the system SHALL return an assistant response containing a greeting and 3 starter questions relevant to the agent's persona and capabilities

#### Scenario: Static opening remarks override
- **WHEN** `generate_opening=true` AND the agent has a non-null `opening_remarks` field
- **THEN** the system SHALL return the static `opening_remarks` text as the greeting with 3 LLM-generated starter questions based on the static text

#### Scenario: Opening remarks disabled
- **WHEN** `generate_opening` is false or not provided in the request
- **THEN** the system SHALL NOT generate opening remarks and proceed with normal chat

#### Scenario: Opening remarks for agent without persona
- **WHEN** `generate_opening=true` AND the agent has no persona configured
- **THEN** the system SHALL generate a generic greeting ("Hi! How can I help you today?") with 3 generic starter questions

### Requirement: Follow-up question suggestions
The system SHALL generate 3-5 contextual follow-up questions after each assistant response when `generate_suggestions=true`. Suggestions SHALL be based on the conversation history (last 2 turns) and the agent's persona. The system SHALL use a secondary LLM call for generation with a 2-second timeout and static fallback.

#### Scenario: Suggestions generated after response
- **WHEN** `generate_suggestions=true` is set in the chat request AND the assistant has responded
- **THEN** the system SHALL return 3-5 follow-up question suggestions relevant to the conversation context

#### Scenario: LLM suggestion generation fails
- **WHEN** the suggestion LLM call fails or times out (2 seconds)
- **THEN** the system SHALL fall back to returning 3 generic questions derived from the agent's persona keywords

#### Scenario: Suggestions disabled per agent
- **WHEN** the agent has `enable_suggestions=false`
- **THEN** the system SHALL NOT generate suggestions regardless of the request flag

#### Scenario: Suggestions disabled per request
- **WHEN** `generate_suggestions` is false or not provided in the request
- **THEN** the system SHALL NOT generate suggestions

### Requirement: Suggestions in streaming responses
The system SHALL emit follow-up suggestions as a typed SSE event `{"type": "suggestions", "questions": [...]}` after content streaming completes and before the `done` event. For opening remarks, the suggestions SHALL be included in the same event.

#### Scenario: Suggestions streamed after content
- **WHEN** streaming is enabled and `generate_suggestions=true`
- **THEN** the system SHALL emit `{"type": "suggestions", "questions": ["q1", "q2", ...]}` after all content events and before the `done` event

#### Scenario: Opening remarks with suggestions streamed
- **WHEN** streaming is enabled and `generate_opening=true`
- **THEN** the system SHALL emit the greeting as content events, then a suggestions event with 3 starter questions, then the `done` event

### Requirement: Suggestions in non-streaming responses
The system SHALL include follow-up suggestions in the `suggested_questions` field on the assistant `ChatMessage` in non-streaming responses. For opening remarks, the greeting SHALL be the message content with starter questions in `suggested_questions`.

#### Scenario: Suggestions in non-streaming response
- **WHEN** streaming is disabled and `generate_suggestions=true`
- **THEN** the response message SHALL include `suggested_questions: ["q1", "q2", ...]` alongside the content

#### Scenario: Opening remarks in non-streaming response
- **WHEN** streaming is disabled and `generate_opening=true`
- **THEN** the response message SHALL contain the greeting as content and `suggested_questions` with 3 starter questions

### Requirement: Agent configuration for suggestions
The `AgentModel` SHALL support `opening_remarks` (TEXT, nullable) for a static greeting override and `enable_suggestions` (BOOLEAN, default true) to toggle suggestion generation at the agent level.

#### Scenario: Agent with static opening remarks
- **WHEN** an agent is created with `opening_remarks="Welcome! I'm your assistant."`
- **THEN** the agent model SHALL store the text and use it as the greeting when opening remarks are requested

#### Scenario: Agent with suggestions disabled
- **WHEN** an agent is created with `enable_suggestions=false`
- **THEN** no suggestions SHALL be generated for any conversation with this agent, regardless of request flags

### Requirement: Suggestion prompt template
The system SHALL use a structured prompt template for generating suggestions. The template SHALL include: agent persona (max 200 chars), last 2 conversation turns, and the current response. The prompt SHALL instruct the LLM to return a JSON array of 3-5 concise questions.

#### Scenario: Prompt includes relevant context
- **WHEN** generating suggestions for a conversation about "database optimization"
- **THEN** the prompt SHALL include the agent's persona, the user's question about "database optimization", and the assistant's response about indexing strategies

#### Scenario: Prompt handles long personas
- **WHEN** the agent's persona exceeds 200 characters
- **THEN** the template SHALL truncate the persona to 200 characters with "..." suffix
