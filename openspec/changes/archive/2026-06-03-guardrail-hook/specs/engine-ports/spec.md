## MODIFIED Requirements

### Requirement: Optional event_store property for Event Persistence
The EnginePort SHALL expose an optional `event_store` property that returns an EventStore instance or None.

#### Scenario: Event store not configured
- **WHEN** a concrete EnginePort does not set `event_store`
- **THEN** the property SHALL return `None`

#### Scenario: Event store configured
- **WHEN** a concrete EnginePort sets `event_store` to an EventStore instance
- **THEN** `port.event_store` SHALL return that instance

### Requirement: Optional guardrail hook properties for Guardrail Integration
The EnginePort SHALL expose four optional properties for guardrail hooks: `pre_llm_hooks` (returns `list[PreLLMHook]`), `post_llm_hooks` (returns `list[PostLLMHook]`), `pre_tool_hooks` (returns `list[PreToolHook]`), `post_tool_hooks` (returns `list[PostToolHook]`). Each default implementation SHALL return an empty list.

#### Scenario: No guardrail hooks configured
- **WHEN** a concrete EnginePort does not override any guardrail property
- **THEN** all four properties SHALL return `[]`

#### Scenario: Pre-LLM hooks configured
- **WHEN** a concrete EnginePort overrides `pre_llm_hooks` to return `[hook1, hook2]`
- **THEN** `port.pre_llm_hooks` SHALL return that list
