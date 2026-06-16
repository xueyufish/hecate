## MODIFIED Requirements

### Requirement: Context Assembler supports suggestion generation mode
The `ContextAssembler` SHALL accept a `suggestion_mode` parameter (default None). When set to `"opening"` or `"followup"`, the assembler SHALL build a suggestion-generation prompt instead of the standard chat context. The prompt SHALL include the agent persona and relevant conversation context formatted for structured question generation.

#### Scenario: Opening remarks suggestion mode
- **WHEN** `suggestion_mode="opening"` is provided with agent persona and capabilities
- **THEN** the assembler SHALL return an `AssembledContext` with a single system message containing the opening remarks prompt template and a user message with agent metadata

#### Scenario: Follow-up suggestion mode
- **WHEN** `suggestion_mode="followup"` is provided with conversation history and agent persona
- **THEN** the assembler SHALL return an `AssembledContext` with a system message containing the follow-up prompt template and messages containing the last 2 turns of conversation

#### Scenario: Default mode (no change)
- **WHEN** `suggestion_mode` is None
- **THEN** the assembler SHALL proceed with standard context assembly as before
