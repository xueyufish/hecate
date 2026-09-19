# Spec Delta

## MODIFIED Requirements

### Requirement: File-role severity matrix
Finding severity SHALL be assigned from rule-intrinsic severity combined with file role, where role reflects runtime exposure: skill frontmatter description and skill body (both injected into agent context by skill loading) and mcp.json credential values are highest-exposure roles; nested supporting files readable by agents on demand are medium; README and catalog-facing text are low. Files under the Hecate namespace directory form additional roles: the namespace manifest (`plugin.yaml`) is a high-exposure role because its values feed permission and configuration decisions; namespace code files are a medium role — they execute only behind the platform-level T0 gates, and scanning provides defense-in-depth. The severity matrix SHALL be fixed platform behavior, not per-package or per-workspace configuration.

#### Scenario: Same phrase tiered by location
- **WHEN** the same medium-intrinsic injection phrase appears in a skill frontmatter description and in a README
- **THEN** the description occurrence receives higher severity than the README occurrence

#### Scenario: Frontmatter smuggling treated as highest exposure
- **WHEN** an invisible-Unicode smuggling run appears in a skill description field
- **THEN** the finding receives the highest severity the rule set assigns

#### Scenario: Namespace manifest findings ranked above namespace code
- **WHEN** the same finding appears in `io.github.xueyufish/plugin.yaml` and in a Python file under the namespace directory
- **THEN** the manifest occurrence receives the higher severity of the two namespace roles

## ADDED Requirements

### Requirement: Namespace permissions audit
For a package carrying a Hecate namespace manifest, the scanner SHALL audit each `permissions` entry with the same rule set used for skill allowed-tools audit, attributing findings to the namespace-manifest role. The audit runs within the normal scan stage (install and enable-time rescan); a block verdict aborts the install per the existing fail-closed enforcement. A malformed permissions value SHALL produce a finding rather than being silently skipped.

#### Scenario: Dangerous permission entry flagged
- **WHEN** a namespace manifest declares a permissions entry matching a secret-exfiltration rule
- **THEN** the scan produces a finding with the namespace-manifest role, contributing to the verdict per the threshold

#### Scenario: Malformed permissions value produces finding
- **WHEN** the namespace manifest's permissions value is not a list of strings
- **THEN** the scanner records a finding describing the malformation instead of skipping the audit
