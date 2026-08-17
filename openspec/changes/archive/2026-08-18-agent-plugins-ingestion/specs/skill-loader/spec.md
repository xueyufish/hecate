## MODIFIED Requirements

### Requirement: SkillLoader resolves agent skills to formatted instructions
The system SHALL provide a `SkillLoader` service in `services/skill/loader.py` that accepts an agent ID and workspace ID, queries the agent's `skills` list, loads matching `SkillModel` records by name within the workspace, and returns a formatted XML string for system prompt injection. Skills with `source="plugin"` SHALL be loaded only when their owning plugin is enabled; when the owning plugin is disabled or uninstalled-pending, the loader SHALL skip the skill with a warning, exactly as for a missing skill, and continue with remaining skills.

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

#### Scenario: Disabled plugin skill skipped
- **WHEN** an agent references a skill with `source="plugin"` whose owning plugin is disabled
- **THEN** the loader SHALL log a warning and skip that skill, continuing with remaining skills

#### Scenario: Enabled plugin skill included
- **WHEN** an agent references a skill with `source="plugin"` whose owning plugin is enabled
- **THEN** the loader SHALL include the skill in the formatted output like any other skill
