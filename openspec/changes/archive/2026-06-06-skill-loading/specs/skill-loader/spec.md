## ADDED Requirements

### Requirement: SkillLoader resolves agent skills to formatted instructions
The system SHALL provide a `SkillLoader` service in `services/skill/loader.py` that accepts an agent ID and workspace ID, queries the agent's `skills` list, loads matching `SkillModel` records by name within the workspace, and returns a formatted XML string for system prompt injection.

#### Scenario: Agent with skills loads all instructions
- **WHEN** `SkillLoader.format_skills(agent_id, workspace_id)` is called and the agent has `skills=["code-review", "unit-test"]`
- **THEN** the loader SHALL query `SkillModel` by name and workspace, format each as `<skill name="...">description\n\ninstructions</skill>`, wrap in `<skills>` tags, and return the XML block

#### Scenario: Agent with no skills returns empty string
- **WHEN** `format_skills()` is called for an agent with `skills=[]`
- **THEN** the loader SHALL return an empty string

#### Scenario: Skill name not found in workspace
- **WHEN** an agent references skill name "missing-skill" but no `SkillModel` with that name exists in the workspace
- **THEN** the loader SHALL log a warning and skip that skill, continuing with remaining skills

#### Scenario: auto_load=True skills are always included
- **WHEN** a skill has `auto_load=True`
- **THEN** it SHALL always be included in the formatted output regardless of whether the agent explicitly lists it in its `skills` field

### Requirement: SkillLoader respects per-skill token budget
The loader SHALL truncate individual skill instructions to their `max_tokens` limit before formatting. If the total formatted skills block exceeds a configurable budget, the loader SHALL drop skills (starting from the lowest-priority) until the budget is met.

#### Scenario: Skill exceeds max_tokens
- **WHEN** a skill has `max_tokens=500` but its instructions would produce ~2000 tokens
- **THEN** the loader SHALL truncate the instructions to approximately 500 tokens (splitting at sentence or paragraph boundaries)

#### Scenario: Total skills exceed budget
- **WHEN** the combined formatted skills exceed the system budget (default 4000 tokens)
- **THEN** the loader SHALL drop skills with `auto_load=False` first, then truncate remaining skills to fit

### Requirement: Skills are injected into system prompt as XML block
When skills are loaded for an agent, the formatted XML block SHALL be appended to the agent's persona (system prompt) before LLM invocation.

#### Scenario: Chat mode agent with persona and skills
- **WHEN** `WorkflowExecutionService.execute()` is called with an `agent_id` and the agent has `persona="You are a coding assistant"` and `skills=["code-review"]`
- **THEN** the system prompt passed to `build_chat_graph()` SHALL be `"You are a coding assistant\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"`

#### Scenario: Sub-agent execution with skills
- **WHEN** `AgentExecutionPort.agent_execute()` is called for an agent with skills
- **THEN** the system message SHALL contain the agent's persona followed by the formatted skills XML block

#### Scenario: Agent with persona=None and skills
- **WHEN** an agent has `persona=None` and `skills=["code-review"]`
- **THEN** the system prompt SHALL be `"You are a helpful assistant.\n\n<skills>\n<skill name=\"code-review\">\n...\n</skill>\n</skills>"`
