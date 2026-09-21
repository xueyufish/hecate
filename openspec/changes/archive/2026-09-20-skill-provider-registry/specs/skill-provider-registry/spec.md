# Spec Delta

## Purpose

Provide a provider registry for skills: a source-origin taxonomy with rank precedence, same-name coexistence with deterministic shadowing resolution, an explicit model-vs-user invocation policy, trust-tier metadata with anti-escalation rules, and content-hash integrity anchoring. This is the identity layer of the 5.9 skill lifecycle stack (5.9-enh), consumed by skill loading and prerequisite for skill versioning (5.9d).

## ADDED Requirements

### Requirement: Skills carry a provider classification with rank precedence

Every skill SHALL carry a `provider` classification drawn from `bundled` (platform-shipped, zero-UUID workspace), `user` (personal to a workspace member), and `project` (shared within a workspace). The origin `custom` SHALL be reserved in the enumeration for a future external-registry integration and SHALL NOT be assignable in this iteration. The existing `source` values SHALL remain the ownership-mode field (`system`/`user`/`project`/`plugin`); `provider` SHALL be derived from and consistent with `source` (`system` maps to `bundled`; plugin-sourced rows keep `provider` unset and stay outside rank competition). The existing nullable `origin` column (package provenance for ingested skills) SHALL remain unchanged and unaffected. Plugin-sourced skills SHALL keep their existing provenance model and SHALL NOT participate in origin-rank competition in this iteration. Resolution precedence SHALL be fixed: `project` outranks `user`, and `user` outranks `bundled`.

#### Scenario: Provider derived from source on creation
- **WHEN** a skill is created with `source="project"`
- **THEN** the skill's `provider` SHALL be `project`, and reads SHALL report both values consistently

#### Scenario: Bundled origin for system skills
- **WHEN** an existing system skill (`workspace_id` zero-UUID, `source="system"`) is migrated
- **THEN** its `provider` SHALL be `bundled`

#### Scenario: Custom provider not assignable
- **WHEN** a create or import request declares `custom` as the skill's provider
- **THEN** the request SHALL be rejected with a validation error stating the origin is reserved

### Requirement: Same-name skills from different providers coexist with deterministic shadowing

The system SHALL allow multiple skills with the same `name` to coexist within one workspace provided their `provider` values differ, and SHALL reject creation of a second skill with the same `name` and the same `provider` in that workspace (409 Conflict). Name resolution for any consumer SHALL return the single highest-precedence match: a `project` skill shadows same-named `user` and `bundled` skills; a `user` skill shadows same-named `bundled` skills; a `bundled` skill is served only when no same-named skill exists in the workspace. Shadowing SHALL be deterministic — the outcome MUST NOT depend on query order or storage order.

#### Scenario: Project skill shadows user skill
- **WHEN** a workspace contains a `project` skill and a `user` skill, both named `pdf-report`, and a consumer resolves `pdf-report`
- **THEN** the `project` skill SHALL be resolved and served

#### Scenario: User skill shadows bundled skill
- **WHEN** a workspace contains a `user` skill named `summarize` and a `bundled` skill named `summarize`
- **THEN** resolution SHALL return the `user` skill

#### Scenario: Bundled skill served when no workspace counterpart exists
- **WHEN** a workspace has no skill named `translate` but a `bundled` skill named `translate` exists
- **THEN** resolution SHALL return the `bundled` skill

#### Scenario: Same-provider duplicate rejected
- **WHEN** a create request names a skill `pdf-report` with provider `project` while a `project` skill named `pdf-report` already exists in the workspace
- **THEN** the API SHALL return 409 Conflict

#### Scenario: Cross-provider same name allowed
- **WHEN** a create request names a skill `pdf-report` with provider `user` while only a `project` skill named `pdf-report` exists in the workspace
- **THEN** the skill SHALL be created successfully and coexist with the `project` skill

#### Scenario: Cross-workspace isolation preserved
- **WHEN** workspace A contains a `project` skill named `audit` and a user of workspace B resolves `audit`
- **THEN** the workspace A skill SHALL NOT be visible or resolvable in workspace B

### Requirement: Invocation policy separates model-visible from user-invocable skills

Every skill SHALL carry two independent invocation-policy flags, `model_invocable` and `user_invocable`, both defaulting to `true`. When `model_invocable` is `false`, the skill SHALL be excluded from every model-visible surface: it SHALL NOT appear in the L1 skill catalog injected into prompts, and on-demand content-load requests targeting it SHALL be rejected. When `user_invocable` is `false`, the skill SHALL NOT be offered on user-facing explicit-invocation surfaces. Both flags `false` SHALL be a valid configuration reserving the skill for programmatic (trusted-caller) use only. A skill with `auto_load=true` MUST have `model_invocable=true`; the combination SHALL be rejected at creation and update with a validation error.

#### Scenario: Model-invocable default
- **WHEN** a skill is created without specifying invocation-policy flags
- **THEN** both `model_invocable` and `user_invocable` SHALL be `true`

#### Scenario: Model-invisible skill excluded from catalog
- **WHEN** an agent advertises a skill whose `model_invocable` is `false`
- **THEN** the skill SHALL NOT appear in the L1 catalog injected into the system context, and a content-load request for it SHALL be rejected

#### Scenario: User-invisible skill hidden from explicit invocation
- **WHEN** a skill has `user_invocable=false`
- **THEN** user-facing explicit-invocation surfaces SHALL NOT offer that skill

#### Scenario: Auto-load requires model visibility
- **WHEN** a create or update request sets `auto_load=true` on a skill whose `model_invocable` is `false`
- **THEN** the request SHALL be rejected with a validation error explaining the conflict

### Requirement: Trust tier metadata with anti-escalation

Every skill SHALL carry a `trust_tier` drawn from `official`, `trusted`, and `community`, defaulting to `community` for user- and project-origin skills. `bundled` skills SHALL be `official`. The tier `official` (and `trusted`) SHALL be assignable only from platform-level configuration allowlisting specific sources; a workspace administrator MUST NOT be able to elevate an arbitrary origin to `official` or `trusted` through workspace-scoped APIs. Plugin-sourced skills SHALL inherit their owning package's trust tier. The trust tier SHALL be exposed read-only on skill reads.

#### Scenario: Default community tier for user-created skill
- **WHEN** a user creates a skill in their workspace
- **THEN** the skill's `trust_tier` SHALL be `community`

#### Scenario: Bundled skills are official
- **WHEN** a `bundled` skill is read
- **THEN** its `trust_tier` SHALL be `official`

#### Scenario: Workspace admin cannot self-designate official
- **WHEN** a workspace-scoped request attempts to set `trust_tier="official"` on a user-origin skill
- **THEN** the request SHALL be rejected; elevation is only possible via platform-level source allowlisting

#### Scenario: Plugin skill inherits package tier
- **WHEN** a skill originates from an installed plugin package whose trust tier is `trusted`
- **THEN** the skill's `trust_tier` SHALL be `trusted`

### Requirement: Content hash anchors skill content integrity

The system SHALL compute a content hash over a skill's content fields (name, instructions, allowed tools, scripts, references) at creation, import, and every content update, and SHALL store it on the skill record. The hash SHALL be exposed read-only. The hash algorithm and field set SHALL match the reference manifest hashing used by agent versioning so the two remain comparable.

#### Scenario: Hash computed at import
- **WHEN** a skill is imported from a SKILL.md file
- **THEN** the stored record SHALL carry a `content_hash` computed over its content fields

#### Scenario: Hash tracks content edits
- **WHEN** a skill's instructions are updated
- **THEN** the stored `content_hash` SHALL change to reflect the new content

#### Scenario: Hash stable across unrelated edits
- **WHEN** a skill's runtime parameter (for example its token budget) is changed without touching content fields
- **THEN** the stored `content_hash` SHALL remain unchanged

### Requirement: Kebab-case name grammar is enforced registry-wide

Skill names SHALL conform to the kebab-case grammar `^[a-z][a-z0-9-]*$` regardless of origin or creation path (manual create, SKILL.md import, programmatic API). Requests violating the grammar SHALL be rejected with a validation error naming the expected pattern.

#### Scenario: Import with invalid name rejected
- **WHEN** a SKILL.md import declares frontmatter `name: "My Skill!"` and `source="user"`
- **THEN** the import SHALL be rejected with a validation error indicating the kebab-case requirement

#### Scenario: Grammar consistent across origins
- **WHEN** skills are created with origin `user` and `project` respectively using valid kebab-case names
- **THEN** both SHALL be accepted without origin-specific name rules
