# Spec Delta

## Purpose

Lets workspace users package their user- and project-sourced skills as a conformant Agent Plugins 1.0 bundle usable in other Agent Plugins ecosystem clients, closing the loop with plugin ingestion — skills learned or authored in Hecate can travel out as a standard package, and packages installed from outside flow back in through the standard ingestion path.

## ADDED Requirements

### Requirement: Export source scope
Export SHALL select only skills owned by the requesting workspace whose `source` is `user` or `project`. Skills with `source` of `system`, `plugin`, or `learned` SHALL be excluded from export selection; requesting them by name SHALL produce a warning in the preview and a skip at execution. Exporting a skill SHALL require the same access permission as reading it.

#### Scenario: User skill exported
- **WHEN** a workspace user exports a skill they authored (`source="user"`)
- **THEN** the skill is included in the bundle

#### Scenario: Plugin-sourced skill excluded
- **WHEN** an export request names a skill with `source="plugin"`
- **THEN** the skill is excluded with a recorded warning and the remaining eligible skills export

### Requirement: Two-phase export
Export SHALL operate in two phases: a preview that computes the bundle plan (bundle name, included skills, sanitized name mapping, warnings, measured size) with no side effects, and an execution that materializes the bundle exactly as previewed. The CLI and REST API SHALL both expose both phases.

#### Scenario: Preview shows plan without side effects
- **WHEN** a preview is requested for three skills, one of which needs name sanitization
- **THEN** the response lists the bundle identity, the three skills with their target directories, the sanitization mapping, and warnings — and no bundle or file is created

#### Scenario: Execution matches preview
- **WHEN** execution runs for a previewed selection
- **THEN** the produced bundle matches the previewed plan (same skills, same names, same warnings)

### Requirement: Bundle layout conformance
An export bundle SHALL be a valid Agent Plugins 1.0 package: `plugin.json` at the root (schema 1.0.0, grammar-conforming name, version, description) and one `skills/<dir>/SKILL.md` per exported skill plus that skill's supporting files. Multiple selected skills SHALL export into a single bundle. The bundle SHALL NOT carry Hecate-private content outside skill frontmatter `metadata`.

#### Scenario: Multi-skill single bundle
- **WHEN** a user exports three skills in one request
- **THEN** one bundle is produced containing all three skills under `skills/`

#### Scenario: Bundle validates as Agent Plugins package
- **WHEN** an exported bundle directory is installed through the standard Agent Plugins ingestion path
- **THEN** it validates, imports all its skills, and the round-trip succeeds

### Requirement: Name sanitization and collision handling
Skill names SHALL be sanitized to the Agent Plugins name grammar (lowercase letters, digits, hyphens; conforming start/end; no `--` or `..`) for both bundle name and skill directory names, with the frontmatter `name` rewritten to equal the directory name. Collisions after sanitization SHALL be disambiguated deterministically. Every rename SHALL be recorded in the skill's frontmatter `metadata` under `hecate.*` keys (original name, workspace, export time) so provenance survives the round trip.

#### Scenario: Non-conforming name sanitized
- **WHEN** a skill named `Deploy Helper_v2` is exported
- **THEN** the bundle uses a grammar-conforming directory name, the frontmatter name matches it, and `hecate.original-name` records `Deploy Helper_v2`

#### Scenario: Collision disambiguated deterministically
- **WHEN** two skills sanitize to the same directory name
- **THEN** the second receives a deterministic numeric suffix, and both mappings appear in the preview

### Requirement: Export snapshot semantics
An exported bundle SHALL be a static copy: it SHALL NOT reference the platform at export time, SHALL NOT activate or execute anything during export, and later edits to the source skills SHALL have no effect on an already-exported bundle.

#### Scenario: Export activates nothing
- **WHEN** an export executes
- **THEN** no skill is enabled, registered, or executed as part of producing the bundle

#### Scenario: Source edits do not propagate
- **WHEN** a source skill is edited after its bundle was exported
- **THEN** the exported bundle retains its original content

### Requirement: Export resource budgets
Export SHALL enforce configurable caps aligned with ingestion limits: a total bundle size cap and a per-file size cap (defaults matching the package size caps). A request exceeding a cap SHALL be rejected at preview with measured and allowed sizes reported.

#### Scenario: Oversized export rejected at preview
- **WHEN** the selected skills' total size exceeds the bundle cap
- **THEN** the preview fails with measured and allowed sizes and no bundle is produced

### Requirement: Export CLI and API
The CLI SHALL provide `hecate plugin export` with workspace selection, optional skill-name filters, and an output path; the primary artifact is a git-ready bundle directory, with an optional ZIP transport flag. The REST API SHALL provide the two-phase surface: a preview endpoint returning the bundle plan and an execute endpoint returning the produced ZIP for download. Both surfaces SHALL be gated by the existing authentication and workspace membership checks.

#### Scenario: CLI export produces git-ready directory
- **WHEN** `hecate plugin export --skill deploy --skill triage --output ./out` runs in a workspace context
- **THEN** a bundle directory containing both skills is created and is directly usable as a git repository content root

#### Scenario: API execute returns ZIP
- **WHEN** the execute endpoint is called for a previewed selection
- **THEN** the API returns the bundle as a ZIP download with the plan metadata in the response headers or body
