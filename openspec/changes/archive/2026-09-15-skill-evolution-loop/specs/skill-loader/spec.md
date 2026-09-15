## MODIFIED Requirements

### Requirement: SkillLoader resolves agent skills to formatted instructions
The system SHALL provide a `SkillLoader` service that accepts an agent ID and workspace ID, queries the agent's `skills` list, loads matching `SkillModel` records by name within the workspace, and produces a two-level representation: an **L1 catalog** (per skill: name plus description, with instructions omitted) injected into the system context, and **L2 content** (full SKILL.md instructions) loaded only on demand. Skills with `source="plugin"` SHALL follow plugin-enabled gating as before: when the owning plugin is disabled or uninstalled-pending, the loader SHALL skip the skill with a warning and continue. Skills with `auto_load=True` SHALL keep their existing semantics: their full instructions (L2 content) are always injected, not just their catalog entry.

#### Scenario: Agent with skills loads L1 catalog only
- **WHEN** `SkillLoader.format_skills(agent_id, workspace_id)` is called and the agent has `skills=["code-review", "unit-test"]`
- **THEN** the loader SHALL return an L1 catalog containing each skill's name and description, without full instructions

#### Scenario: Agent with no skills returns empty string
- **WHEN** `format_skills()` is called for an agent with `skills=[]`
- **THEN** the loader SHALL return an empty string

#### Scenario: Skill name not found in workspace
- **WHEN** an agent references skill name "missing-skill" but no `SkillModel` with that name exists in the workspace
- **THEN** the loader SHALL log a warning and skip that skill, continuing with remaining skills

#### Scenario: auto_load=True skills keep full injection
- **WHEN** a skill has `auto_load=True`
- **THEN** its full instructions SHALL be injected into system context regardless of the agent's `skills` field, without requiring an L2 load request

#### Scenario: Disabled plugin skill skipped
- **WHEN** an agent references a skill with `source="plugin"` whose owning plugin is disabled
- **THEN** the loader SHALL log a warning and skip that skill in both L1 and L2, continuing with remaining skills

#### Scenario: Enabled plugin skill included
- **WHEN** an agent references a skill with `source="plugin"` whose owning plugin is enabled
- **THEN** the skill's L1 catalog entry SHALL be included like any other skill, with L2 available on demand

### Requirement: SkillLoader respects per-skill token budget
The loader SHALL enforce budgets at both levels: the L1 catalog SHALL respect a compact catalog budget (each entry truncated to its description), and L2 content SHALL be truncated to the skill's `max_tokens` limit before delivery. The combined always-injected content (auto_load skills plus any L2 content loaded for the current run) SHALL respect the total system budget; when exceeded, the loader SHALL drop skills starting from the lowest priority.

#### Scenario: Skill exceeds max_tokens on L2 load
- **WHEN** an L2 load is requested for a skill with `max_tokens=500` whose instructions would produce ~2000 tokens
- **THEN** the delivered L2 content SHALL be truncated to approximately 500 tokens (splitting at sentence or paragraph boundaries)

#### Scenario: L1 catalog exceeds catalog budget
- **WHEN** the combined L1 catalog entries exceed the catalog budget
- **THEN** the loader SHALL drop non-auto_load catalog entries starting from the lowest priority until the budget is met

#### Scenario: Total injected content exceeds budget
- **WHEN** always-injected content plus loaded L2 content exceeds the system budget (default 4000 tokens)
- **THEN** the loader SHALL drop lowest-priority content until the budget is met and log the eviction

### Requirement: Skills are injected into system prompt as XML block
When skills are loaded for an agent, the L1 catalog SHALL be formatted as an XML block appended to the agent's persona (system prompt) before LLM invocation, advertising each skill's name, description, and how to request full content. L2 content SHALL be injected as run-scoped context when a load request is accepted, and SHALL NOT be permanently appended to the system prompt.

#### Scenario: Chat mode agent with persona and skills
- **WHEN** `WorkflowExecutionService.execute()` is called with an `agent_id` and the agent has `persona="You are a coding assistant"` and `skills=["code-review"]`
- **THEN** the system prompt passed to `build_chat_graph()` SHALL be the persona followed by an L1 catalog XML block listing "code-review" with its description, without the skill's full instructions

#### Scenario: Sub-agent execution with skills
- **WHEN** `AgentExecutionPort.agent_execute()` is called for an agent with skills
- **THEN** the system message SHALL contain the agent's persona followed by the L1 catalog XML block

#### Scenario: Agent with persona=None and skills
- **WHEN** an agent has `persona=None` and `skills=["code-review"]`
- **THEN** the system prompt SHALL be the default persona followed by the L1 catalog XML block

## ADDED Requirements

### Requirement: On-demand L2 skill loading
The system SHALL expose an on-demand loading mechanism by which the model can request the full content (L2) of an advertised skill by name during a run; the system SHALL inject the requested content as run-scoped context with provenance marking which skill was loaded. Requests for skills not in the agent's L1 catalog SHALL be rejected with an informative error.

#### Scenario: Model requests full skill content
- **WHEN** during a run the model requests L2 content for "code-review" advertised in the L1 catalog
- **THEN** the full instructions (subject to `max_tokens` truncation) are injected as run-scoped context and the load is recorded for usage statistics

#### Scenario: Request for unadvertised skill rejected
- **WHEN** the model requests L2 content for a skill not present in the agent's L1 catalog
- **THEN** the request SHALL fail with an error naming the unavailable skill and the catalog remains unchanged
