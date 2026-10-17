# Proposal

## Why

Hecate currently requires the full OpenSpec artifact set for every change, including small documentation corrections and narrowly scoped maintenance. That overhead can make a small-team coding loop feel slower without improving the risk control those changes need.

## What Changes

- Define a conservative lightweight tier for documentation-only changes, test-only changes, mechanical refactors, local developer tooling, and narrow fixes that preserve an existing contract.
- Keep the full spec-driven tier for new or changed product behavior, public or persisted contracts, migrations, security or tenant-isolation semantics, architecture, extension points, and uncertain or cross-domain work.
- Add a project-local OpenSpec schema that uses only a concise proposal and implementation/verification tasks for lightweight changes; leave the full schema as the default.
- Update the proposal skill and contributor guidance to select the tier from the repository risk rules, while requiring escalation when classification is uncertain.
- Keep focused tests, applicable static checks, CI, and recording actual verification results mandatory in both tiers.
- Make post-merge archive review proportional: synchronize delta specs when present, and revisit product positioning only when product-facing descriptions or catalog entries changed.

## Capabilities

### New Capabilities

None. This change adjusts repository development policy and OpenSpec tooling, not Hecate product behavior; the change opts out of product delta specs with `skip_specs: true`.

### Modified Capabilities

None.

## Impact

Updates `AGENTS.md`, `docs/how-to/openspec-workflow.md`, the repository OpenSpec proposal skill, and a project-local lightweight schema with templates. No runtime code, dependency, CI gate, or branch-protection change is included.
