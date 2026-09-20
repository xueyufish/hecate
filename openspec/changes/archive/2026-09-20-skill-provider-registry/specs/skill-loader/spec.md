# Spec Delta

## MODIFIED Requirements

### Requirement: SkillLoader resolves agent skills to formatted instructions
The system SHALL provide a `SkillLoader` service that accepts an agent ID and workspace ID, queries the agent's `skills` list, and loads matching skill records by name within the workspace. When multiple records share a name across provider origins, the loader SHALL resolve through the provider-registry precedence (project outranks user, user outranks bundled) and serve only the highest-precedence match; resolution SHALL be deterministic regardless of storage or query order, and a workspace-origin match SHALL always shadow a `bundled` match. The loader produces a two-level representation: an **L1 catalog** (per skill: name plus description, with instructions omitted) injected into the system context, and **L2 content** (full SKILL.md instructions) loaded only on demand. Skills with `model_invocable=false` SHALL be excluded from the L1 catalog, and on-demand L2 load requests for them SHALL be rejected; they remain usable on user-facing explicit-invocation surfaces. Skills with `source="plugin"` SHALL follow plugin-enabled gating as before: when the owning plugin is disabled or uninstalled-pending, the loader SHALL skip the skill with a warning and continue. Skills with `auto_load=True` SHALL keep their existing semantics: their full instructions (L2 content) are always injected, not just their catalog entry.

#### Scenario: Agent with skills loads L1 catalog only
- **WHEN** `SkillLoader.format_skills(agent_id, workspace_id)` is called and the agent has `skills=["code-review", "unit-test"]`
- **THEN** the loader SHALL return an L1 catalog containing each skill's name and description, without full instructions

#### Scenario: Agent with no skills returns empty string
- **WHEN** `format_skills()` is called for an agent with `skills=[]`
- **THEN** the loader SHALL return an empty string

#### Scenario: Skill name not found in workspace
- **WHEN** an agent references skill name "missing-skill" but no skill with that name exists in the workspace or the bundled origin
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

#### Scenario: Project skill shadows user and bundled skills of the same name
- **WHEN** an agent references skill name `pdf-report` and the workspace contains a `project` skill named `pdf-report` while a `bundled` skill of the same name also exists
- **THEN** the loader SHALL serve the `project` skill's description and content and SHALL NOT serve the `bundled` skill

#### Scenario: Bundled skill served only when no workspace counterpart exists
- **WHEN** an agent references skill name `translate` and no user- or project-origin skill named `translate` exists in the workspace
- **THEN** the loader SHALL serve the `bundled` skill

#### Scenario: Model-invisible skill excluded from catalog and L2
- **WHEN** an agent advertises a skill whose `model_invocable` is `false`
- **THEN** the skill SHALL NOT appear in the L1 catalog, and an L2 content-load request for it SHALL be rejected with an explicit not-advertised error
