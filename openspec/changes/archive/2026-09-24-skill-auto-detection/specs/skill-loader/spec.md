# skill-loader Delta

## MODIFIED Requirements

### Requirement: SkillLoader resolves agent skills to formatted instructions

The system SHALL provide a `SkillLoader` service that accepts an agent ID and workspace ID, queries the agent's `skills` list, and loads matching skill records by name within the workspace. When multiple records share a name across provider origins, the loader SHALL resolve through the provider-registry precedence (project outranks user, user outranks bundled) and serve only the highest-precedence match; resolution SHALL be deterministic regardless of storage or query order, and a workspace-origin match SHALL always shadow a `bundled` match. The loader produces a two-level representation: an **L1 catalog** (per skill: name plus description, with instructions omitted) injected into the system context, and **L2 content** (full SKILL.md instructions) loaded only on demand. Skills with `model_invocable=false` SHALL be excluded from the L1 catalog, and on-demand L2 load requests for them SHALL be rejected; they remain usable on user-facing explicit-invocation surfaces. Skills with `source="plugin"` SHALL follow plugin-enabled gating as before: when the owning plugin is disabled or uninstalled-pending, the loader SHALL skip the skill with a warning and continue. Skills with `auto_load=True` SHALL keep their existing semantics: their full instructions (L2 content) are always injected, not just their catalog entry. When skill discovery is active for the agent (per the `skill-auto-detection` capability's three-layer governance), the loader SHALL additionally resolve the workspace's discovery pool — workspace and bundled skills meeting the discovery eligibility rules — and SHALL include eligible non-bound entries in the L1 catalog alongside bound and `auto_load` entries. Discovery-resolved skills SHALL be served from the live row; they are never pinned through an agent-version reference manifest, and reference-manifest pins SHALL continue to apply only to bound skills.

#### Scenario: Agent with skills loads L1 catalog only
- **WHEN** `SkillLoader.format_skills(agent_id, workspace_id)` is called and the agent has `skills=["code-review", "unit-test"]` and discovery is inactive
- **THEN** the loader SHALL return an L1 catalog containing each bound skill's name and description, without full instructions

#### Scenario: Agent with no skills returns empty string
- **WHEN** `format_skills()` is called for an agent with `skills=[]`, no `auto_load` skills exist, and discovery is inactive
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

#### Scenario: Discovery adds unbound workspace skills to catalog
- **WHEN** skill discovery is active for the agent and the workspace contains an eligible unbound skill
- **THEN** the loader SHALL include that skill's catalog entry in the L1 catalog in addition to bound and `auto_load` entries

#### Scenario: Discovery inactive keeps catalog closed-set
- **WHEN** skill discovery is inactive for the agent (global switch off, workspace off, or agent opted out) and an eligible unbound skill exists in the workspace
- **THEN** the loader SHALL NOT include that skill in the L1 catalog

#### Scenario: Pinned bound skill keeps snapshot content while discovery entry serves live
- **WHEN** an agent-version reference manifest pins a bound skill to a snapshot, and discovery additionally surfaces an unbound skill
- **THEN** the pinned bound skill SHALL serve snapshot content while the discovered skill SHALL serve live-row content

### Requirement: SkillLoader respects per-skill token budget

The loader SHALL enforce budgets at both levels: the L1 catalog SHALL respect a compact catalog budget (default 2000 tokens; each entry truncated to its description), and L2 content SHALL be truncated to the skill's `max_tokens` limit before delivery. The combined always-injected content (auto_load skills plus any L2 content loaded for the current run) SHALL respect the total system budget; when exceeded, the loader SHALL drop skills starting from the lowest priority. When discovery entries compete for the catalog budget, the loader SHALL apply the selection policy defined by the `skill-auto-detection` capability (bound and `auto_load` first, then trust tier, provider rank, usage count, name).

#### Scenario: Skill exceeds max_tokens on L2 load
- **WHEN** an L2 load is requested for a skill with `max_tokens=500` whose instructions would produce ~2000 tokens
- **THEN** the delivered L2 content SHALL be truncated to approximately 500 tokens (splitting at sentence or paragraph boundaries)

#### Scenario: L1 catalog exceeds catalog budget
- **WHEN** the combined L1 catalog entries exceed the catalog budget (default 2000 tokens)
- **THEN** the loader SHALL drop non-auto_load catalog entries starting from the lowest priority per the discovery selection policy until the budget is met

#### Scenario: Total injected content exceeds budget
- **WHEN** always-injected content plus loaded L2 content exceeds the system budget (default 4000 tokens)
- **THEN** the loader SHALL drop lowest-priority content until the budget is met and log the eviction

### Requirement: On-demand L2 skill loading

The system SHALL expose an on-demand loading mechanism by which the model can request the full content (L2) of an advertised skill by name during a run; the system SHALL inject the requested content as run-scoped context with provenance marking which skill was loaded. The advertised set SHALL be the agent's bound skills plus its `auto_load` skills, extended with the discovery pool when skill discovery is active for that agent (per the `skill-auto-detection` capability). Requests for skills outside the advertised set SHALL be rejected with an informative error.

#### Scenario: Model requests full skill content
- **WHEN** during a run the model requests L2 content for "code-review" advertised in the L1 catalog
- **THEN** the full instructions (subject to `max_tokens` truncation) are injected as run-scoped context and the load is recorded for usage statistics

#### Scenario: Request for unadvertised skill rejected
- **WHEN** the model requests L2 content for a skill not present in the advertised set
- **THEN** the request SHALL fail with an error naming the unavailable skill and the catalog remains unchanged

#### Scenario: Discovered skill loadable without binding
- **WHEN** skill discovery is active for the agent and the model requests L2 content for an eligible unbound skill that is not in `agent.skills`
- **THEN** the request SHALL succeed: full instructions are injected as run-scoped context and the usage event SHALL be marked as discovery-sourced

#### Scenario: Discovered skill not loadable when agent opted out
- **WHEN** the agent opted out of discovery and the model requests L2 content for an eligible unbound workspace skill
- **THEN** the request SHALL fail with an informative error
